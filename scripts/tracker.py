"""Local web app to view and manage the application pipeline (Flask, localhost, $0).

All writes route through scripts/store.py (schema-validated, forward-only stages).
Run: python -m scripts.tracker
"""
from __future__ import annotations

import datetime
import os
import re
from pathlib import Path

from flask import Flask, current_app, redirect, render_template, request, url_for, flash, session, abort

from scripts import locations, store

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_APPLICATIONS = REPO_ROOT / "data" / "applications.json"
DEFAULT_JOBS = REPO_ROOT / "data" / "jobs.json"
# Gitignored; holds other people's names. Read only when running locally (see /jobs).
DEFAULT_LINKEDIN_ARCHIVE = REPO_ROOT / "data" / "linkedin_archive"

STAGE_COLUMNS = ["draft", "applied", "screening", "interview", "offer"]
TERMINAL_COLUMNS = ["rejected", "withdrawn"]
# Jobs-tab buckets: "considering" = these; everything else ("closed"/"dismissed",
# and the schema's unused-on-jobs "applied") is "eliminated" and offered a Restore.
_OPEN_JOB_STATUSES = {"new", "interested"}
_DISMISS_STATUS = {"closed": "closed", "not_fit": "dismissed"}


def _age_days(app: dict, today: datetime.date) -> int:
    """Days since the application's first stage_history date (0 if unknown)."""
    hist = app.get("stage_history") or []
    if not hist:
        return 0
    try:
        first = datetime.date.fromisoformat(hist[0]["date"])
    except (KeyError, ValueError):
        return 0
    return max(0, (today - first).days)


def _find_artifacts(job: dict) -> str | None:
    """Return output/<company-role>/ path if a matching tailored-materials folder exists."""
    company = (job.get("company") or "").lower()
    title = (job.get("title") or "").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", f"{company}-{title}").strip("-")
    if not slug:
        return None  # guard: empty slug would resolve to the output root
    folder = REPO_ROOT / "output" / slug
    return f"output/{slug}" if folder.is_dir() else None


def _save_applications(path: Path, apps: list[dict]) -> None:
    """Validate every application, then persist. Raises on invalid (guards bad writes)."""
    for a in apps:
        store.validate_item(a, "applications")
    store.save_store(path, apps)


def _unique_job_id(company: str, title: str, jobs: list[dict]) -> str:
    """Slug of 'company-title', suffixed -2/-3/... so the id is unique in the store."""
    base = re.sub(r"[^a-z0-9]+", "-", f"{company}-{title}".lower()).strip("-") or "job"
    existing = {j.get("id") for j in jobs}
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


def _application_from_job(job: dict, job_id: str) -> dict:
    """Build the draft-application upsert payload for a job (links job_id + artifacts)."""
    incoming = {"company": job.get("company", ""), "title": job.get("title", ""),
                "stage": "draft", "source": "manual", "job_id": job_id}
    artifacts = _find_artifacts(job)
    if artifacts:
        incoming["artifacts_path"] = artifacts
    return incoming


def _install_auth(app: Flask) -> None:
    """Owner-only Google OAuth guard. Active only when AUTH_ENABLED=1."""
    if os.environ.get("AUTH_ENABLED") != "1":
        return

    from authlib.integrations.flask_client import OAuth

    app.secret_key = os.environ["SECRET_KEY"]  # must be a strong random value; signs session cookies (weak key => session forgery)
    allowed = {e.strip().lower() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()}

    oauth = OAuth(app)
    oauth.register(
        "google",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

    _OPEN = {"login", "auth_callback", "healthz", "static"}  # "static": Flask's default endpoint; guarded-bypass in case assets are added later

    @app.before_request
    def _require_login():
        if request.endpoint in _OPEN:
            return None
        email = session.get("user_email", "").lower()
        if email in allowed:
            return None
        if email:            # signed in but not allowlisted
            abort(403)
        return redirect(url_for("login"))

    # On Vercel, TLS is terminated at the proxy, so url_for(_external=True) may emit an
    # http:// URI that won't match Google's registered https:// redirect. Prefer an
    # explicit env-provided redirect URI in production.
    def _redirect_uri():
        return os.environ.get("OAUTH_REDIRECT_URI") or url_for("auth_callback", _external=True)

    @app.get("/login")
    def login():
        return oauth.google.authorize_redirect(_redirect_uri())

    @app.get("/auth/callback")
    def auth_callback():
        token = oauth.google.authorize_access_token()
        info = token.get("userinfo") or {}
        email = info.get("email", "").lower()
        if email and info.get("email_verified") and email in allowed:
            session["user_email"] = email
            return redirect(url_for("board"))
        return ("Not authorized for this account.", 403)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}


def _default_score_job(job: dict) -> dict | None:
    """Score a manually-added job. Returns None when no API key is set (add unscored).
    Any real failure (bad key, missing config, API error) PROPAGATES so the caller can
    surface it — silent swallowing here is what hid a broken key for days."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import json as _json
    import anthropic
    from scripts import backfill_scores
    from scripts.scan import CONFIG_PATH, PROFILE_PATH
    config = _json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    profile = (_json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
               if PROFILE_PATH.exists() else {})
    return backfill_scores.score_job(anthropic.Anthropic(), profile, job, config)


def create_app(applications_path: Path = DEFAULT_APPLICATIONS,
               jobs_path: Path = DEFAULT_JOBS,
               config_path: Path = locations.CONFIG_PATH) -> Flask:
    app = Flask(__name__)
    app.secret_key = "tracker-local"  # flash messages only; localhost single-user
    app.config["APPLICATIONS_PATH"] = Path(applications_path)
    app.config["JOBS_PATH"] = Path(jobs_path)
    app.config["LINKEDIN_ARCHIVE_DIR"] = DEFAULT_LINKEDIN_ARCHIVE
    app.config["CONFIG_PATH"] = Path(config_path)

    def _config() -> dict:
        path = app.config["CONFIG_PATH"]
        return locations.load_config(path) if path.exists() else {}
    # Seam: overridden in tests; default scores via the API only when a key is present.
    app.config.setdefault("SCORE_JOB", _default_score_job)

    @app.context_processor
    def inject_current_app():
        return {"current_app": current_app}

    @app.before_request
    def _reset_store_sha_cache():
        if os.environ.get("STORE_BACKEND") == "github":
            from scripts import github_store
            github_store.reset_sha_cache()

    @app.errorhandler(store.StoreConflict)
    def _on_store_conflict(_e):
        flash("The data changed underneath — please try again.")
        ref = request.referrer
        if not (ref and ref.startswith(request.host_url)):
            ref = url_for("board")
        return redirect(ref)

    @app.get("/")
    def board():
        apps = store.load_store(app.config["APPLICATIONS_PATH"])
        jobs_by_id = {j.get("id"): j for j in store.load_store(app.config["JOBS_PATH"])}
        today = datetime.date.today()
        columns = {stage: [] for stage in STAGE_COLUMNS}
        terminal = {stage: [] for stage in TERMINAL_COLUMNS}
        for a in apps:
            stage = a.get("stage", "draft")
            next_stage = None
            if stage in STAGE_COLUMNS:
                idx = STAGE_COLUMNS.index(stage)
                if idx + 1 < len(STAGE_COLUMNS):
                    next_stage = STAGE_COLUMNS[idx + 1]
            job = jobs_by_id.get(a.get("job_id"))
            a = dict(a, _key=store.application_key(a), _age=_age_days(a, today),
                     _next_stage=next_stage, _job_url=(job.get("url") if job else None),
                     _can_undo=len(a.get("stage_history") or []) > 1,
                     _is_open=stage in STAGE_COLUMNS)
            if stage in columns:
                columns[stage].append(a)
            elif stage in terminal:
                terminal[stage].append(a)
        return render_template("board.html", columns=columns, terminal=terminal,
                               stage_columns=STAGE_COLUMNS, terminal_columns=TERMINAL_COLUMNS)

    @app.post("/advance")
    def advance():
        key = request.form["key"]
        stage = request.form["stage"]
        path = app.config["APPLICATIONS_PATH"]
        apps = store.load_store(path)
        today = datetime.date.today().isoformat()
        # find company/title/job_id for the matching key so upsert can match it
        match = next((a for a in apps if store.application_key(a) == key), None)
        if match is None:
            flash("Application not found.")
            return redirect(url_for("board"))
        incoming = {"company": match.get("company", ""), "title": match.get("title", ""),
                    "stage": stage, "source": "manual"}
        if match.get("job_id"):
            incoming["job_id"] = match["job_id"]
        merged, changed = store.upsert_application(apps, incoming, today)
        if not changed:
            flash("Can't move backward or to the same stage.")
            return redirect(url_for("board"))
        _save_applications(path, merged)
        return redirect(url_for("board"))

    @app.post("/undo")
    def undo():
        key = request.form["key"]
        path = app.config["APPLICATIONS_PATH"]
        apps = store.load_store(path)
        merged, changed = store.revert_application(apps, key)
        if not changed:
            flash("Nothing to undo.")
            return redirect(url_for("board"))
        _save_applications(path, merged)
        return redirect(url_for("board"))

    @app.post("/new")
    def new():
        company = (request.form.get("company") or "").strip()
        title = (request.form.get("title") or "").strip()
        stage = request.form.get("stage") or "draft"
        if not company:
            flash("Company is required.")
            return redirect(url_for("board"))
        path = app.config["APPLICATIONS_PATH"]
        apps = store.load_store(path)
        today = datetime.date.today().isoformat()
        incoming = {"company": company, "title": title, "stage": stage, "source": "manual"}
        merged, changed = store.upsert_application(apps, incoming, today)
        if not changed:
            flash("That application already exists at a later stage.")
            return redirect(url_for("board"))
        _save_applications(path, merged)
        return redirect(url_for("board"))

    @app.get("/jobs")
    def jobs():
        all_jobs = store.load_store(app.config["JOBS_PATH"])
        shown = sorted(all_jobs, key=lambda j: j.get("score") or 0, reverse=True)
        # Warm paths carry real names from the LinkedIn archive, so they are computed
        # only on a local backend -- never on the deployed (github-backed) tracker.
        warm = {}
        if not store._use_github():
            from linkedin import warm_paths as _wp
            warm = _wp.warm_paths(all_jobs, app.config["LINKEDIN_ARCHIVE_DIR"],
                                  datetime.date.today())
        return render_template("jobs.html", jobs=shown, open_statuses=_OPEN_JOB_STATUSES,
                               warm=warm, locations=locations.entries(_config()))

    @app.post("/jobs/start")
    def jobs_start():
        job_id = request.form["job_id"]
        all_jobs = store.load_store(app.config["JOBS_PATH"])
        job = next((j for j in all_jobs if j.get("id") == job_id), None)
        if job is None:
            flash("Job not found.")
            return redirect(url_for("jobs"))
        path = app.config["APPLICATIONS_PATH"]
        apps = store.load_store(path)
        today = datetime.date.today().isoformat()
        incoming = _application_from_job(job, job_id)
        merged, changed = store.upsert_application(apps, incoming, today)
        if not changed:
            flash("An application for that job already exists.")
            return redirect(url_for("board"))
        _save_applications(path, merged)
        return redirect(url_for("board"))

    @app.post("/jobs/add")
    def jobs_add():
        company = (request.form.get("company") or "").strip()
        title = (request.form.get("title") or "").strip()
        url = (request.form.get("url") or "").strip()
        location_type = request.form.get("location_type") or locations.keys(_config())[0]
        status = request.form.get("status") or "interested"
        description = (request.form.get("description") or "").strip()
        also_start = request.form.get("start")  # checkbox: present only when checked
        if not (company and title and url):
            flash("Company, title, and URL are required.")
            return redirect(url_for("jobs"))
        jobs_path = app.config["JOBS_PATH"]
        jobs = store.load_store(jobs_path)
        today = datetime.date.today().isoformat()
        job = {"id": _unique_job_id(company, title, jobs), "title": title,
               "company": company, "location_type": location_type, "url": url,
               "found_date": today, "status": status, "source": "manual"}
        if description:
            job["description_excerpt"] = description
        try:
            store.validate_item(job, "jobs")
        except Exception:  # bad enum value from a hand-crafted POST
            flash("Invalid job details (check location type / status).")
            return redirect(url_for("jobs"))
        try:
            locations.check_type(location_type, _config())
        except ValueError as exc:
            flash(f"Invalid job details: {exc}")
            return redirect(url_for("jobs"))
        merged, added = store.append_jobs(jobs, [job])
        if added == 0:
            flash("That job is already tracked — use “Start application” below.")
            return redirect(url_for("jobs"))
        # Best-effort scoring; must never block the add. `job` is the same object
        # append_jobs placed into `merged`, so mutating it updates what we save.
        scored = False
        score_error = None
        try:
            result = current_app.config["SCORE_JOB"](job)
        except Exception as exc:
            import traceback
            traceback.print_exc()  # -> Vercel runtime logs
            result = None
            score_error = f"{type(exc).__name__}: {exc}"
        if result:
            job.update(result)
            scored = True
        store.save_store(jobs_path, merged)
        if scored:
            flash(f"Added & scored {company} — {title} ({job['score']}).")
        elif score_error:
            flash(f"Added {company} — {title}, but SCORING FAILED: {score_error}")
        else:
            flash(f"Added {company} — {title}.")
        if not also_start:
            return redirect(url_for("jobs"))
        apps_path = app.config["APPLICATIONS_PATH"]
        apps = store.load_store(apps_path)
        merged_apps, _ = store.upsert_application(
            apps, _application_from_job(job, job["id"]), today)
        _save_applications(apps_path, merged_apps)
        return redirect(url_for("board"))

    @app.post("/jobs/dismiss")
    def jobs_dismiss():
        job_id = request.form["job_id"]
        new_status = _DISMISS_STATUS.get(request.form.get("reason", ""))
        if new_status is None:
            flash("Unknown dismiss reason.")
            return redirect(url_for("jobs"))
        jobs_path = app.config["JOBS_PATH"]
        jobs = store.load_store(jobs_path)
        merged, changed = store.update_jobs(jobs, [{"id": job_id, "status": new_status}])
        if not changed:
            flash("Job not found.")
            return redirect(url_for("jobs"))
        store.save_store(jobs_path, merged)
        return redirect(url_for("jobs"))

    @app.post("/jobs/restore")
    def jobs_restore():
        job_id = request.form["job_id"]
        jobs_path = app.config["JOBS_PATH"]
        jobs = store.load_store(jobs_path)
        merged, changed = store.update_jobs(jobs, [{"id": job_id, "status": "interested"}])
        if not changed:
            flash("Job not found.")
            return redirect(url_for("jobs"))
        store.save_store(jobs_path, merged)
        return redirect(url_for("jobs"))

    @app.post("/jobs/rescore")
    def jobs_rescore():
        job_id = request.form["job_id"]
        description = (request.form.get("description") or "").strip()
        jobs_path = app.config["JOBS_PATH"]
        jobs = store.load_store(jobs_path)
        job = next((j for j in jobs if j.get("id") == job_id), None)
        if job is None:
            flash("Job not found.")
            return redirect(url_for("jobs"))
        # Score the job with the new description via the same seam the add flow uses.
        # Best-effort: a scoring failure still saves the description (with an explanation).
        updated = dict(job)
        updated["description_excerpt"] = description
        score_error = None
        try:
            result = current_app.config["SCORE_JOB"](updated)
        except Exception as exc:
            import traceback
            traceback.print_exc()  # -> Vercel runtime logs
            result = None
            score_error = f"{type(exc).__name__}: {exc}"
        patch = {"id": job_id, "description_excerpt": description}
        if result:
            patch.update(result)  # score, score_breakdown, strengths, gaps
        merged, _ = store.update_jobs(jobs, [patch])
        store.save_store(jobs_path, merged)
        title = job.get("title")
        if result:
            flash(f"Rescored {job.get('company')} — {title} ({patch['score']}).")
        elif score_error:
            flash(f"Saved description for {title}, but SCORING FAILED: {score_error}")
        else:
            flash(f"Saved description for {title} (not scored).")
        return redirect(url_for("jobs"))

    @app.post("/edit")
    def edit():
        key = request.form["key"]
        fields = {}
        for name in ("notes", "next_action"):
            if request.form.get(name) is not None:
                fields[name] = request.form.get(name, "")
        due = request.form.get("due_date")
        if due is not None:
            fields["due_date"] = due or None  # blank clears it (schema allows null)
        path = app.config["APPLICATIONS_PATH"]
        apps = store.load_store(path)
        merged, changed = store.patch_application(apps, key, fields)
        if not changed:
            flash("Application not found.")
            return redirect(url_for("board"))
        _save_applications(path, merged)
        return redirect(url_for("board"))

    _install_auth(app)
    return app


def main() -> int:
    app = create_app()
    host = os.environ.get("TRACKER_HOST", "127.0.0.1")
    port = int(os.environ.get("TRACKER_PORT", "8000"))
    if os.environ.get("TRACKER_OPEN", "1") == "1":
        import threading, webbrowser
        threading.Timer(0.8, webbrowser.open, args=[f"http://{host}:{port}"]).start()
    app.run(host=host, port=port, debug=False)
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main())
