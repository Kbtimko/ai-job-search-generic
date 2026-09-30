import json
from pathlib import Path

import pytest

from scripts.tracker import create_app


def _seed(tmp_path: Path, apps=None, jobs=None) -> dict:
    apps_path = tmp_path / "applications.json"
    jobs_path = tmp_path / "jobs.json"
    apps_path.write_text(json.dumps(apps or []))
    jobs_path.write_text(json.dumps(jobs or []))
    return {"apps": apps_path, "jobs": jobs_path}


def _remote_cfg(tmp_path):
    """Self-contained config so these tests do not depend on the owner's metros."""
    cfg = tmp_path / "search-config.json"
    if not cfg.exists():
        cfg.write_text(json.dumps({"titles": ["x"], "locations": [
            {"key": "remote", "label": "Remote", "score": 10}]}))
    return cfg


@pytest.fixture
def client(tmp_path):
    paths = _seed(
        tmp_path,
        apps=[{"company": "Etsy", "title": "Director", "stage": "draft",
               "applied_date": None,
               "stage_history": [{"stage": "draft", "date": "2026-07-01", "source": "tailor"}]}],
    )
    app = create_app(applications_path=paths["apps"], jobs_path=paths["jobs"],
                     config_path=_remote_cfg(tmp_path))
    app.config.update(TESTING=True)
    app.config["LINKEDIN_ARCHIVE_DIR"] = tmp_path / "no-linkedin-archive"
    c = app.test_client()
    c._paths = paths  # stash for assertions
    return c


def test_board_lists_applications(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Etsy" in body
    assert "Director" in body
    assert "Draft" in body  # stage column header


def test_board_shows_terminal_applications(tmp_path):
    paths = _seed(
        tmp_path,
        apps=[{"company": "OldCo", "title": "Analyst", "stage": "rejected",
               "applied_date": None,
               "stage_history": [{"stage": "rejected", "date": "2026-07-01", "source": "gmail"}]}],
    )
    app = create_app(applications_path=paths["apps"], jobs_path=paths["jobs"],
                     config_path=_remote_cfg(tmp_path))
    app.config.update(TESTING=True)
    resp = app.test_client().get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "OldCo" in body
    assert "Closed" in body


def test_advance_moves_forward(client):
    resp = client.post("/advance", data={"key": "etsy::director", "stage": "applied"},
                       follow_redirects=True)
    assert resp.status_code == 200
    saved = json.loads(client._paths["apps"].read_text())
    assert saved[0]["stage"] == "applied"
    assert saved[0]["applied_date"]  # set when reaching applied


def test_edit_writes_metadata(client):
    resp = client.post("/edit", data={"key": "etsy::director", "notes": "warm intro",
                                       "next_action": "email recruiter", "due_date": "2026-07-15"},
                       follow_redirects=True)
    assert resp.status_code == 200
    saved = json.loads(client._paths["apps"].read_text())
    assert saved[0]["notes"] == "warm intro"
    assert saved[0]["next_action"] == "email recruiter"
    assert saved[0]["due_date"] == "2026-07-15"


def test_edit_can_clear_due_date(client):
    client.post("/edit", data={"key": "etsy::director", "due_date": "2026-07-15"},
                follow_redirects=True)
    # submitting a blank date clears it (schema allows null)
    client.post("/edit", data={"key": "etsy::director", "due_date": ""},
                follow_redirects=True)
    saved = json.loads(client._paths["apps"].read_text())
    assert saved[0]["due_date"] is None


def test_new_creates_application(client):
    resp = client.post("/new", data={"company": "Figma", "title": "Head of Analytics",
                                      "stage": "draft"}, follow_redirects=True)
    assert resp.status_code == 200
    saved = json.loads(client._paths["apps"].read_text())
    keys = {a["company"] for a in saved}
    assert "Figma" in keys
    body = resp.get_data(as_text=True)
    assert "Figma" in body


def _seed_jobs(client, jobs):
    client._paths["jobs"].write_text(json.dumps(jobs))


def test_jobs_lists_all_with_buckets(client):
    _seed_jobs(client, [
        {"id": "figma-hoa-2026-06", "title": "Design Dir", "company": "Figma",
         "url": "https://x.com/f", "found_date": "2026-06-28",
         "status": "new", "score": 88},
        {"id": "deadco-eng-2026-06", "title": "Eng", "company": "DeadCo",
         "url": "https://x.com/d", "found_date": "2026-06-20",
         "status": "closed", "score": 70},
    ])
    body = client.get("/jobs").get_data(as_text=True)
    # every job now renders in one page; the browser filters the view
    assert "Figma" in body
    assert "DeadCo" in body
    assert 'data-bucket="considering"' in body
    assert 'data-bucket="eliminated"' in body
    # dismiss + restore controls are present
    assert 'value="not_fit"' in body
    assert 'action="/jobs/restore"' in body


def test_jobs_start_creates_prefilled_draft(client):
    _seed_jobs(client, [
        {"id": "figma-hoa-2026-06", "title": "Head of Analytics", "company": "Figma",
         "location_type": "remote", "url": "https://x.com/f", "found_date": "2026-06-28",
         "status": "new", "score": 88},
    ])
    resp = client.post("/jobs/start", data={"job_id": "figma-hoa-2026-06"},
                       follow_redirects=True)
    assert resp.status_code == 200
    saved = json.loads(client._paths["apps"].read_text())
    match = next(a for a in saved if a.get("job_id") == "figma-hoa-2026-06")
    assert match["company"] == "Figma"
    assert match["title"] == "Head of Analytics"
    assert match["stage"] == "draft"


def test_jobs_add_creates_job_and_starts_application(client):
    resp = client.post("/jobs/add", data={
        "company": "Notion", "title": "Head of Data", "url": "https://notion.so/careers/hod",
        "location_type": "remote", "status": "interested", "start": "1"},
        follow_redirects=True)
    assert resp.status_code == 200
    jobs = json.loads(client._paths["jobs"].read_text())
    added = next(j for j in jobs if j["company"] == "Notion")
    assert added["id"] == "notion-head-of-data"  # slug of company-title
    assert added["found_date"] and added["source"] == "manual"
    apps = json.loads(client._paths["apps"].read_text())
    app_row = next(a for a in apps if a.get("job_id") == "notion-head-of-data")
    assert app_row["stage"] == "draft"
    assert resp.request.path == "/"  # redirected to the board


def test_jobs_add_without_start_adds_job_only(client):
    resp = client.post("/jobs/add", data={
        "company": "Vercel", "title": "Analytics Lead", "url": "https://vercel.com/j",
        "location_type": "remote", "status": "new"},  # no "start" key = unchecked
        follow_redirects=True)
    assert resp.status_code == 200
    jobs = json.loads(client._paths["jobs"].read_text())
    assert any(j["company"] == "Vercel" for j in jobs)
    apps = json.loads(client._paths["apps"].read_text())
    assert not any(a.get("company") == "Vercel" for a in apps)
    assert resp.request.path == "/jobs"  # stays on the Jobs tab


def test_jobs_add_duplicate_url_is_flashed_noop(client):
    _seed_jobs(client, [
        {"id": "acme-eng", "title": "Engineer", "company": "Acme", "location_type": "remote",
         "url": "https://acme.com/eng", "found_date": "2026-06-01", "status": "new"}])
    resp = client.post("/jobs/add", data={
        "company": "Acme Corp", "title": "Staff Engineer", "url": "https://acme.com/eng",
        "location_type": "remote", "status": "interested", "start": "1"},
        follow_redirects=True)
    body = resp.get_data(as_text=True)
    assert "already tracked" in body.lower()
    jobs = json.loads(client._paths["jobs"].read_text())
    assert len(jobs) == 1  # nothing appended


def test_jobs_add_missing_field_is_flashed_noop(client):
    resp = client.post("/jobs/add", data={
        "company": "NoURL", "title": "Role", "url": ""}, follow_redirects=True)
    body = resp.get_data(as_text=True)
    assert "required" in body.lower()
    jobs = json.loads(client._paths["jobs"].read_text())
    assert not any(j["company"] == "NoURL" for j in jobs)


def test_jobs_add_generates_unique_id_on_slug_collision(client):
    _seed_jobs(client, [
        {"id": "acme-eng", "title": "Eng", "company": "Acme", "location_type": "remote",
         "url": "https://acme.com/1", "found_date": "2026-06-01", "status": "new"}])
    # "Acme!" + "Eng" slugs to the same "acme-eng" but is a distinct posting (new url)
    client.post("/jobs/add", data={
        "company": "Acme!", "title": "Eng", "url": "https://acme.com/2",
        "location_type": "remote", "status": "new"}, follow_redirects=True)
    jobs = json.loads(client._paths["jobs"].read_text())
    ids = {j["id"] for j in jobs}
    assert "acme-eng" in ids and "acme-eng-2" in ids


def test_jobs_tab_shows_add_form(client):
    body = client.get("/jobs").get_data(as_text=True)
    assert "Add job manually" in body
    assert 'action="/jobs/add"' in body


def test_advance_backward_is_flashed_noop(client):
    # first move to interview, then try to go back to applied
    client.post("/advance", data={"key": "etsy::director", "stage": "interview"},
                follow_redirects=True)
    resp = client.post("/advance", data={"key": "etsy::director", "stage": "applied"},
                       follow_redirects=True)
    body = resp.get_data(as_text=True)
    assert "backward" in body.lower() or "can't" in body.lower()
    saved = json.loads(client._paths["apps"].read_text())
    assert saved[0]["stage"] == "interview"  # unchanged


def test_board_card_links_to_job_description(client):
    # an application linked (via job_id) to a job with a url shows the posting link
    client._paths["jobs"].write_text(json.dumps([
        {"id": "figma-hoa-2026-06", "title": "Head of Analytics", "company": "Figma",
         "location_type": "remote", "url": "https://example.com/figma-jd",
         "found_date": "2026-06-28", "status": "new"}]))
    client._paths["apps"].write_text(json.dumps([
        {"company": "Figma", "title": "Head of Analytics", "stage": "draft",
         "applied_date": None, "job_id": "figma-hoa-2026-06",
         "stage_history": [{"stage": "draft", "date": "2026-07-01", "source": "tailor"}]}]))
    body = client.get("/").get_data(as_text=True)
    assert 'href="https://example.com/figma-jd"' in body


def test_board_card_without_job_id_has_no_link(client):
    # the seeded Etsy app has no job_id → renders with no JD link, no crash
    body = client.get("/").get_data(as_text=True)
    assert "Etsy" in body
    assert "Job description" not in body


def test_undo_steps_back_one_stage(client):
    client.post("/advance", data={"key": "etsy::director", "stage": "applied"},
                follow_redirects=True)
    client.post("/advance", data={"key": "etsy::director", "stage": "screening"},
                follow_redirects=True)
    client.post("/undo", data={"key": "etsy::director"}, follow_redirects=True)
    saved = json.loads(client._paths["apps"].read_text())
    assert saved[0]["stage"] == "applied"


def test_undo_with_only_creation_entry_flashes_nothing(client):
    resp = client.post("/undo", data={"key": "etsy::director"}, follow_redirects=True)
    body = resp.get_data(as_text=True)
    assert "nothing to undo" in body.lower()
    saved = json.loads(client._paths["apps"].read_text())
    assert saved[0]["stage"] == "draft"


def test_jobs_tab_links_title_to_posting(client):
    client._paths["jobs"].write_text(json.dumps([
        {"id": "figma-hoa-2026-06", "title": "Head of Analytics", "company": "Figma",
         "location_type": "remote", "url": "https://example.com/figma-jd",
         "found_date": "2026-06-28", "status": "new", "score": 88}]))
    body = client.get("/jobs").get_data(as_text=True)
    assert 'href="https://example.com/figma-jd"' in body


def test_reject_moves_card_to_closed(client):
    resp = client.post("/advance", data={"key": "etsy::director", "stage": "rejected"},
                       follow_redirects=True)
    assert resp.status_code == 200
    saved = json.loads(client._paths["apps"].read_text())
    assert saved[0]["stage"] == "rejected"
    body = resp.get_data(as_text=True)
    assert "Closed" in body and "Etsy" in body


def test_withdraw_sets_withdrawn(client):
    client.post("/advance", data={"key": "etsy::director", "stage": "withdrawn"},
                follow_redirects=True)
    saved = json.loads(client._paths["apps"].read_text())
    assert saved[0]["stage"] == "withdrawn"


def test_undo_restores_rejected_to_active(client):
    client.post("/advance", data={"key": "etsy::director", "stage": "rejected"},
                follow_redirects=True)
    client.post("/undo", data={"key": "etsy::director"}, follow_redirects=True)
    saved = json.loads(client._paths["apps"].read_text())
    assert saved[0]["stage"] == "draft"  # rejected popped, back to creation stage


def test_board_shows_undo_reject_withdraw_controls(client):
    # advance once so the app has >1 history entry (enables Undo) and is still open
    client.post("/advance", data={"key": "etsy::director", "stage": "applied"},
                follow_redirects=True)
    body = client.get("/").get_data(as_text=True)
    assert "Undo" in body
    assert "Reject" in body
    assert "Withdraw" in body


def test_jobs_dismiss_not_fit_sets_dismissed(client):
    _seed_jobs(client, [
        {"id": "acme-eng", "title": "Eng", "company": "Acme",
         "url": "https://acme.com/e", "found_date": "2026-06-01",
         "status": "new", "score": 50}])
    resp = client.post("/jobs/dismiss",
                       data={"job_id": "acme-eng", "reason": "not_fit"},
                       follow_redirects=True)
    assert resp.status_code == 200
    jobs = json.loads(client._paths["jobs"].read_text())
    assert jobs[0]["status"] == "dismissed"


def test_jobs_dismiss_closed_sets_closed(client):
    _seed_jobs(client, [
        {"id": "acme-eng", "title": "Eng", "company": "Acme",
         "url": "https://acme.com/e", "found_date": "2026-06-01",
         "status": "interested", "score": 50}])
    client.post("/jobs/dismiss",
                data={"job_id": "acme-eng", "reason": "closed"},
                follow_redirects=True)
    jobs = json.loads(client._paths["jobs"].read_text())
    assert jobs[0]["status"] == "closed"


def test_jobs_dismiss_unknown_id_is_noop(client):
    _seed_jobs(client, [
        {"id": "acme-eng", "title": "Eng", "company": "Acme",
         "url": "https://acme.com/e", "found_date": "2026-06-01",
         "status": "new", "score": 50}])
    client.post("/jobs/dismiss",
                data={"job_id": "nope", "reason": "closed"},
                follow_redirects=True)
    jobs = json.loads(client._paths["jobs"].read_text())
    assert jobs[0]["status"] == "new"  # unchanged


def test_jobs_dismiss_bad_reason_is_noop(client):
    _seed_jobs(client, [
        {"id": "acme-eng", "title": "Eng", "company": "Acme",
         "url": "https://acme.com/e", "found_date": "2026-06-01",
         "status": "new", "score": 50}])
    client.post("/jobs/dismiss",
                data={"job_id": "acme-eng", "reason": "banana"},
                follow_redirects=True)
    jobs = json.loads(client._paths["jobs"].read_text())
    assert jobs[0]["status"] == "new"  # unchanged


def test_jobs_restore_sets_interested(client):
    _seed_jobs(client, [
        {"id": "acme-eng", "title": "Eng", "company": "Acme",
         "url": "https://acme.com/e", "found_date": "2026-06-01",
         "status": "dismissed", "score": 50}])
    resp = client.post("/jobs/restore", data={"job_id": "acme-eng"},
                       follow_redirects=True)
    assert resp.status_code == 200
    jobs = json.loads(client._paths["jobs"].read_text())
    assert jobs[0]["status"] == "interested"


def test_jobs_restore_unknown_id_is_noop(client):
    _seed_jobs(client, [
        {"id": "acme-eng", "title": "Eng", "company": "Acme",
         "url": "https://acme.com/e", "found_date": "2026-06-01",
         "status": "closed", "score": 50}])
    client.post("/jobs/restore", data={"job_id": "nope"}, follow_redirects=True)
    jobs = json.loads(client._paths["jobs"].read_text())
    assert jobs[0]["status"] == "closed"  # unchanged


def test_jobs_add_form_has_description_field(client):
    body = client.get("/jobs").get_data(as_text=True)
    assert 'name="description"' in body


def test_jobs_add_with_description_scores_the_job(client):
    client.application.config["SCORE_JOB"] = lambda job: {
        "score": 72,
        "score_breakdown": {"differentiation": 8, "winnability": 6,
                            "comp": 5, "location": 8, "domain": 7},
        "strengths": ["semantic layer match"], "gaps": ["no Snowplow"]}
    resp = client.post("/jobs/add", data={
        "company": "Fjord", "title": "Head of Analytics", "url": "https://fjord.example/j",
        "location_type": "remote", "status": "interested",
        "description": "Own the semantic layer and ship AI reporting."},
        follow_redirects=True)
    assert resp.status_code == 200
    added = next(j for j in json.loads(client._paths["jobs"].read_text())
                if j["company"] == "Fjord")
    assert added["score"] == 72
    assert added["score_breakdown"]["differentiation"] == 8
    assert added["strengths"] == ["semantic layer match"]
    assert added["description_excerpt"] == "Own the semantic layer and ship AI reporting."


def test_jobs_add_unscored_when_scorer_returns_none(client):
    client.application.config["SCORE_JOB"] = lambda job: None
    resp = client.post("/jobs/add", data={
        "company": "Acme", "title": "Director of Analytics", "url": "https://acme/j",
        "location_type": "remote", "status": "new", "description": "some text"},
        follow_redirects=True)
    assert resp.status_code == 200
    added = next(j for j in json.loads(client._paths["jobs"].read_text())
                if j["company"] == "Acme")
    assert "score" not in added                       # added, just not scored
    assert added["description_excerpt"] == "some text"


def test_jobs_add_still_succeeds_when_scorer_raises(client):
    def boom(job):
        raise RuntimeError("scoring blew up")
    client.application.config["SCORE_JOB"] = boom
    resp = client.post("/jobs/add", data={
        "company": "Beta", "title": "Director of Data", "url": "https://beta/j",
        "location_type": "remote", "status": "new", "description": "text"},
        follow_redirects=True)
    assert resp.status_code == 200
    assert any(j["company"] == "Beta" for j in json.loads(client._paths["jobs"].read_text()))
    # the real error must be surfaced to the user, not silently swallowed
    body = resp.get_data(as_text=True)
    assert "SCORING FAILED" in body
    assert "RuntimeError" in body and "scoring blew up" in body


def test_pyproject_deploy_deps_include_anthropic():
    # The deployed tracker scores manual adds via `import anthropic` (SCORE_JOB path).
    # If anthropic isn't in the Vercel/uv dependency set, scoring dies at runtime with
    # ModuleNotFoundError. This guards that regression.
    import tomllib
    from pathlib import Path
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    assert any(d.lower().startswith("anthropic") for d in deps), deps


def _app_with_job(tmp_path, job, score_job=None):
    paths = _seed(tmp_path, jobs=[job])
    app = create_app(applications_path=paths["apps"], jobs_path=paths["jobs"],
                     config_path=_remote_cfg(tmp_path))
    app.config.update(TESTING=True)
    if score_job is not None:
        app.config["SCORE_JOB"] = score_job
    c = app.test_client()
    c._paths = paths
    return c


_A_JOB = {"id": "acme-dir", "title": "Director of Analytics", "company": "Acme",
          "location_type": "remote", "url": "https://acme/j", "found_date": "2026-08-01",
          "status": "interested", "source": "manual"}


def test_jobs_rescore_updates_description_and_score(tmp_path):
    c = _app_with_job(tmp_path, dict(_A_JOB), score_job=lambda job: {
        "score": 66,
        "score_breakdown": {"differentiation": 7, "winnability": 6,
                            "comp": 5, "location": 8, "domain": 7},
        "strengths": ["s"], "gaps": ["g"]})
    resp = c.post("/jobs/rescore",
                  data={"job_id": "acme-dir", "description": "New JD about the semantic layer."},
                  follow_redirects=True)
    assert resp.status_code == 200
    job = next(j for j in json.loads(c._paths["jobs"].read_text()) if j["id"] == "acme-dir")
    assert job["description_excerpt"] == "New JD about the semantic layer."
    assert job["score"] == 66
    assert job["score_breakdown"]["differentiation"] == 7
    assert job["strengths"] == ["s"]


def test_jobs_rescore_saves_description_unscored_when_scorer_none(tmp_path):
    c = _app_with_job(tmp_path, dict(_A_JOB), score_job=lambda job: None)
    resp = c.post("/jobs/rescore",
                  data={"job_id": "acme-dir", "description": "some text"},
                  follow_redirects=True)
    assert resp.status_code == 200
    job = next(j for j in json.loads(c._paths["jobs"].read_text()) if j["id"] == "acme-dir")
    assert job["description_excerpt"] == "some text"   # saved
    assert job.get("score") is None                    # but not scored


def test_jobs_rescore_surfaces_error_and_keeps_description(tmp_path):
    def boom(job):
        raise RuntimeError("rescore blew up")
    c = _app_with_job(tmp_path, dict(_A_JOB), score_job=boom)
    resp = c.post("/jobs/rescore",
                  data={"job_id": "acme-dir", "description": "kept text"},
                  follow_redirects=True)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "SCORING FAILED" in body and "rescore blew up" in body
    job = next(j for j in json.loads(c._paths["jobs"].read_text()) if j["id"] == "acme-dir")
    assert job["description_excerpt"] == "kept text"   # description still saved


def test_jobs_tab_renders_rescore_form_per_job(tmp_path):
    c = _app_with_job(tmp_path, dict(_A_JOB))
    body = c.get("/jobs").get_data(as_text=True)
    assert 'action="/jobs/rescore"' in body
    assert "acme-dir" in body  # job_id wired into the form


def test_jobs_tab_labels_reopen_for_closed_jobs(tmp_path):
    closed = dict(_A_JOB, id="old-co", company="OldCo", status="closed")
    c = _app_with_job(tmp_path, closed)
    body = c.get("/jobs").get_data(as_text=True)
    assert "Reopen" in body                      # clearer label than "Restore"
    assert 'action="/jobs/restore"' in body      # existing route unchanged


def test_jobs_tab_renders_score_why_with_breakdown_and_bullets(tmp_path):
    job = dict(_A_JOB, score=53,
               score_breakdown={"differentiation": 6, "winnability": 1.5,
                                "domain": 9, "comp": 5, "location": 8},
               strengths=["SaaS domain is a near-perfect match"],
               gaps=["VP title is a hard screen"])
    c = _app_with_job(tmp_path, job)
    body = c.get("/jobs").get_data(as_text=True)
    assert "score-why" in body                                   # the toggle is present
    assert "differentiation" in body and "winnability" in body   # sub-scores labelled
    assert "9/10" in body                                        # domain sub-score value
    assert "SaaS domain is a near-perfect match" in body         # strength bullet
    assert "VP title is a hard screen" in body                   # gap bullet


def test_jobs_tab_no_score_why_when_no_explanation(tmp_path):
    job = dict(_A_JOB)  # no score_breakdown / strengths / gaps / score_reasons
    job.pop("score", None)
    c = _app_with_job(tmp_path, job)
    body = c.get("/jobs").get_data(as_text=True)
    assert "score-why" not in body   # nothing to explain -> no toggle


def _cfg_app(tmp_path, locs):
    cfg = tmp_path / "search-config.json"
    cfg.write_text(json.dumps({"titles": ["x"], "locations": locs}))
    paths = _seed(tmp_path)
    app = create_app(applications_path=paths["apps"], jobs_path=paths["jobs"], config_path=cfg)
    app.config.update(TESTING=True)
    return app, paths


def test_jobs_page_location_options_come_from_config(tmp_path):
    app, _ = _cfg_app(tmp_path, [{"key": "remote", "label": "Remote", "score": 8},
                                 {"key": "denver", "label": "Denver", "score": 10}])
    html = app.test_client().get("/jobs").get_data(as_text=True)
    assert '<option value="denver">denver</option>' in html
    assert "austin" not in html


def test_jobs_add_rejects_an_unconfigured_location(tmp_path):
    app, paths = _cfg_app(tmp_path, [{"key": "remote", "label": "Remote", "score": 8}])
    app.config["SCORE_JOB"] = lambda job: None
    app.test_client().post("/jobs/add", data={"company": "Acme", "title": "Lead",
                           "url": "https://a.example", "location_type": "tokyo"})
    assert json.loads(paths["jobs"].read_text()) == []

LINKEDIN_FIXTURES = Path(__file__).parent / "fixtures" / "linkedin"


def _warm_jobs():
    return [
        {"id": "nw-dir", "title": "Director of Analytics", "company": "Northwind Traders",
         "url": "https://x.com/nw", "found_date": "2026-09-01", "status": "new", "score": 80},
        {"id": "um-1", "title": "Head of Data", "company": "Umbrella Biotech",
         "url": "https://x.com/um", "found_date": "2026-09-01", "status": "new", "score": 70},
    ]


def test_jobs_shows_warm_path_for_matched_company(client):
    import linkedin.warm_paths as wp
    wp._CACHE.clear()
    _seed_jobs(client, _warm_jobs())
    client.application.config["LINKEDIN_ARCHIVE_DIR"] = LINKEDIN_FIXTURES
    body = client.get("/jobs").get_data(as_text=True)
    assert body.count("🤝") == 1
    assert "🤝 2" in body
    assert "Ada Nolan" in body
    assert 'href="https://www.linkedin.com/in/adanolan"' in body
    assert "★ former colleague" in body
    assert "Employer is self-reported and may be stale; former colleague is inferred." in body


def test_jobs_without_archive_has_no_warm_path(client):
    _seed_jobs(client, _warm_jobs())
    body = client.get("/jobs").get_data(as_text=True)
    assert "🤝" not in body
    assert "Northwind Traders" in body


def test_jobs_never_renders_names_on_github_backend(client, monkeypatch):
    """Privacy: on Vercel (STORE_BACKEND=github) nothing from the archive renders,
    even when an archive is present at the configured path."""
    import linkedin.warm_paths as wp
    from scripts import store
    wp._CACHE.clear()
    monkeypatch.setenv("STORE_BACKEND", "github")
    monkeypatch.setattr(store, "load_store", lambda _path: _warm_jobs())
    client.application.config["LINKEDIN_ARCHIVE_DIR"] = LINKEDIN_FIXTURES
    body = client.get("/jobs").get_data(as_text=True)
    assert "Northwind Traders" in body  # the page itself still renders
    assert "🤝" not in body
    assert "Ada Nolan" not in body
    assert "linkedin.com/in/" not in body


def _stub_warm(monkeypatch, top):
    import datetime
    import linkedin.warm_paths as wp
    monkeypatch.setattr(wp, "warm_paths", lambda jobs, archive_dir, today: {
        "nw-dir": {"count": len(top), "top": top,
                   "archive_date": datetime.date(2026, 8, 20)}})


def test_warm_path_without_profile_url_renders_plain_name(client, monkeypatch, tmp_path):
    _seed_jobs(client, _warm_jobs())
    client.application.config["LINKEDIN_ARCHIVE_DIR"] = tmp_path
    _stub_warm(monkeypatch, [{"name": "Ivy Moss", "title": "Analyst", "profile_url": "",
                              "recency": "never messaged", "former_colleague": False,
                              "tie_score": 5.0}])
    body = client.get("/jobs").get_data(as_text=True)
    assert "Ivy Moss" in body
    assert 'href=""' not in body
    assert "(loaded 2026-08-20)" in body


def test_warm_path_names_are_escaped(client, monkeypatch, tmp_path):
    _seed_jobs(client, _warm_jobs())
    client.application.config["LINKEDIN_ARCHIVE_DIR"] = tmp_path
    _stub_warm(monkeypatch, [{"name": "<b>Eve</b>", "title": "<i>VP</i>",
                              "profile_url": "https://www.linkedin.com/in/eve",
                              "recency": "active", "former_colleague": False,
                              "tie_score": 5.0}])
    body = client.get("/jobs").get_data(as_text=True)
    assert "<b>Eve</b>" not in body
    assert "&lt;b&gt;Eve&lt;/b&gt;" in body


def test_vercelignore_keeps_personal_data_out_of_cli_uploads():
    """`vercel --prod` runs from this Mac, and Vercel does not skip gitignored paths.
    Only .vercelignore keeps the archive and personal outputs off Vercel."""
    path = Path(__file__).resolve().parent.parent / ".vercelignore"
    lines = {line.strip() for line in path.read_text().splitlines()
             if line.strip() and not line.startswith("#")}
    assert "data/linkedin_archive/" in lines
    assert "output/" in lines
    assert "/profile/*" in lines
    # vercel.json's includeFiles bundles profile/master.json for scoring: keep it.
    assert "!/profile/master.json" in lines
    # Worktrees and browser snapshots carry their own copies of personal data.
    assert ".claude/" in lines
    assert ".playwright-mcp/" in lines
    ordered = [line.strip() for line in path.read_text().splitlines()
               if line.strip() and not line.startswith("#")]
    # A negation only works when it comes after the exclusion it carves out of.
    assert ordered.index("/profile/*") < ordered.index("!/profile/master.json")


def test_create_app_never_defaults_to_the_real_archive_in_tests(tmp_path):
    from scripts.tracker import REPO_ROOT
    paths = _seed(tmp_path)
    app = create_app(applications_path=paths["apps"], jobs_path=paths["jobs"])
    archive = Path(app.config["LINKEDIN_ARCHIVE_DIR"])
    assert archive != REPO_ROOT / "data" / "linkedin_archive"
    assert not archive.exists()


def test_default_archive_path_matches_linkedin_package():
    import linkedin
    from scripts import tracker
    assert linkedin.ARCHIVE_DIR == tracker.REPO_ROOT / "data" / "linkedin_archive"
