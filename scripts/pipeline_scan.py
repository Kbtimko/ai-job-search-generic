"""Deterministic Gmail status scan for the application pipeline (no LLM, $0).

Reads Gmail over IMAP, classifies templated recruiter/confirmation emails with
rules, auto-applies high-confidence forward stage changes to data/applications.json,
and emails a digest (changes / ambiguous-to-confirm / stalled). Idempotent.
"""
from __future__ import annotations

import datetime
import json
import os
import email as _emaillib
import imaplib
from pathlib import Path

from scripts.store import is_forward_transition
from scripts import store, notify

REPO_ROOT = Path(__file__).resolve().parent.parent
APPLICATIONS_PATH = REPO_ROOT / "data" / "applications.json"
JOBS_PATH = REPO_ROOT / "data" / "jobs.json"
STATE_PATH = REPO_ROOT / "data" / "pipeline-state.json"

STALL_DAYS = int(os.environ.get("JOBSEARCH_STALL_DAYS", "14"))
LOOKBACK_DAYS = int(os.environ.get("JOBSEARCH_PIPELINE_LOOKBACK_DAYS", "30"))
_ACTIVE = {"draft", "applied", "screening", "interview", "offer"}

# Ordered strongest->weakest so terminal/late stages win when several phrases match.
STAGE_PRIORITY = ["rejected", "offer", "interview", "screening", "applied"]

STRONG_PHRASES: dict[str, list[str]] = {
    "rejected": ["unfortunately", "not moving forward", "won't be moving forward",
                 "with other candidates", "pursue other candidates",
                 "decided not to proceed", "position has been filled",
                 "will not be moving forward"],
    "offer": ["pleased to offer", "offer letter", "extend an offer", "excited to offer"],
    "interview": ["schedule an interview", "interview invitation", "onsite interview",
                  "meet the team", "next round", "technical interview", "invite you to interview"],
    "screening": ["phone screen", "recruiter call", "initial call", "schedule a call",
                  "introductory call", "quick chat", "set up a call"],
    "applied": ["thank you for applying", "we received your application",
                "application received", "thanks for your application",
                "your application has been received"],
}

WEAK_PHRASES: dict[str, list[str]] = {
    "applied": ["update on your application", "your application status", "application update"],
}

STATE_DEFAULT = {"last_scan": None, "processed_message_ids": []}


def classify_email(subject: str, snippet: str) -> tuple[str | None, str]:
    """Return (stage, confidence) from templated email text.

    confidence: 'high' (strong phrase), 'low' (weak phrase), 'none' (no match).
    Strong phrases win over weak; stages checked in STAGE_PRIORITY order.
    """
    text = f"{subject} {snippet}".lower()
    for stage in STAGE_PRIORITY:
        if any(p in text for p in STRONG_PHRASES.get(stage, [])):
            return stage, "high"
    for stage in STAGE_PRIORITY:
        if any(p in text for p in WEAK_PHRASES.get(stage, [])):
            return stage, "low"
    return None, "none"


def _norm(s: str) -> str:
    """Lowercase alphanumeric-only, for loose company/domain comparison."""
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def match_application(msg: dict, applications: list[dict]) -> dict | None:
    """Return the application whose company matches this message, or None.

    Match if the normalized company name is a substring of the sender's
    normalized address, OR the raw company name appears in subject/snippet.
    """
    from_norm = _norm(msg.get("from", ""))
    hay = f"{msg.get('subject', '')} {msg.get('snippet', '')}".lower()
    for app in applications:
        company = (app.get("company") or "").strip()
        cn = _norm(company)
        if len(cn) < 2:  # skip missing/degenerate names that would match almost anything
            continue
        if cn in from_norm or company.lower() in hay:
            return app
    return None


def decide_updates(messages: list[dict], applications: list[dict], jobs: list[dict],
                   processed_ids: list[str], today: str) -> dict:
    """Pure decision core. Returns upsert-ready changes; mutates nothing.

    Returns {"applied": [incoming...], "inferred_new": [incoming...],
             "ambiguous": [ {company, subject, guessed_stage, current_stage, reason} ],
             "processed_ids": [ids seen this run]}.
    """
    processed = set(processed_ids)
    applied: list[dict] = []
    inferred_new: list[dict] = []
    ambiguous: list[dict] = []
    seen: list[str] = []

    for msg in messages:
        mid = msg.get("id")
        if not mid or mid in processed:
            continue
        # Record every scanned id as processed — including unclassifiable mail — so
        # we don't re-scan the same old messages on every run.
        seen.append(mid)
        stage, conf = classify_email(msg.get("subject", ""), msg.get("snippet", ""))
        if stage is None:
            continue
        app = match_application(msg, applications)
        if app is not None:
            if not is_forward_transition(app.get("stage", "applied"), stage):
                continue  # no-op; already marked processed
            incoming = {"job_id": app.get("job_id"), "company": app["company"],
                        "title": app.get("title", ""), "stage": stage,
                        "source": "gmail", "evidence": msg.get("subject", "")}
            if conf == "high":
                applied.append(incoming)
            else:
                ambiguous.append({"company": app["company"], "subject": msg.get("subject", ""),
                                  "guessed_stage": stage, "current_stage": app.get("stage"),
                                  "reason": "low-confidence phrase"})
            continue
        # No existing application: only infer a NEW entry from a strong 'applied' confirmation.
        if stage == "applied" and conf == "high":
            hay = _norm(f"{msg.get('from','')} {msg.get('subject','')} {msg.get('snippet','')}")
            candidates = [j for j in jobs if _norm(j.get("company", "")) and _norm(j["company"]) in hay]
            job = max(candidates, key=lambda j: len(_norm(j["company"])), default=None)
            if job is not None:
                inferred_new.append({"job_id": job.get("id"), "company": job.get("company", ""),
                                     "title": job.get("title", ""), "stage": "applied",
                                     "source": "gmail", "evidence": msg.get("subject", "")})
            else:
                ambiguous.append({"company": None, "subject": msg.get("subject", ""),
                                  "guessed_stage": "applied", "current_stage": None,
                                  "reason": "confirmation email, no matching application or job"})

    return {"applied": applied, "inferred_new": inferred_new,
            "ambiguous": ambiguous, "processed_ids": seen}


def _days_since(date_str: str, today: str) -> int:
    try:
        d0 = datetime.date.fromisoformat(date_str)
        d1 = datetime.date.fromisoformat(today)
        return (d1 - d0).days
    except (ValueError, TypeError):
        return 0


def _last_movement(app: dict) -> str:
    hist = app.get("stage_history") or []
    if hist:
        return hist[-1].get("date", "")
    return app.get("applied_date") or ""


def build_pipeline_digest(applied: list[dict], ambiguous: list[dict], applications: list[dict],
                          today: str, stall_days: int = 14) -> tuple[str, str, str]:
    """Return (subject, html, text) summarizing this scan's pipeline state."""
    active = {"draft", "applied", "screening", "interview", "offer"}
    stalled = [a for a in applications
               if a.get("stage") in active and _days_since(_last_movement(a), today) >= stall_days]

    if not applied and not ambiguous and not stalled:
        subject = "Pipeline — no updates today"
        body = "No pipeline updates today."
        return subject, f"<p>{body}</p>", body

    subject = f"Pipeline — {len(applied)} update{'s' if len(applied) != 1 else ''}"
    if ambiguous:
        subject += f", {len(ambiguous)} to confirm"
    if stalled:
        subject += f", {len(stalled)} stalled"

    text, html = [subject, ""], [f"<h2>{subject}</h2>"]

    if applied:
        text.append("== Applied automatically ==")
        html.append("<h3>Applied automatically</h3><ul>")
        for c in applied:
            line = f"{c['company']} — {c.get('title','')}: → {c['stage']} ({c.get('evidence','')})"
            text.append(line)
            html.append(f"<li>{line}</li>")
        html.append("</ul>")

    if ambiguous:
        text.append("")
        text.append("== Confirm? (run the track-pipeline skill) ==")
        html.append("<h3>Confirm?</h3><ul>")
        for a in ambiguous:
            who = a.get("company") or "unknown"
            line = f"{who}: looks like {a['guessed_stage']} — \"{a.get('subject','')}\" [{a.get('reason','')}]"
            text.append(line)
            html.append(f"<li>{line}</li>")
        html.append("</ul>")

    if stalled:
        text.append("")
        text.append(f"== Stalled (no movement in {stall_days}+ days) ==")
        html.append(f"<h3>Stalled ({stall_days}+ days)</h3><ul>")
        for a in stalled:
            line = f"{a['company']} — {a.get('title','')}: {a.get('stage')} since {_last_movement(a)}"
            text.append(line)
            html.append(f"<li>{line}</li>")
        html.append("</ul>")

    return subject, "\n".join(html), "\n".join(text)


def load_state(path) -> dict:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return dict(STATE_DEFAULT)
    if not text:
        return dict(STATE_DEFAULT)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return dict(STATE_DEFAULT)
    if not isinstance(data, dict):
        return dict(STATE_DEFAULT)
    return {"last_scan": data.get("last_scan"),
            "processed_message_ids": list(data.get("processed_message_ids", []))}


def save_state(path, state: dict) -> None:
    Path(path).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _imap_date(iso_date: str) -> str:
    """Convert 'YYYY-MM-DD' to IMAP SINCE format 'DD-Mon-YYYY'."""
    d = datetime.date.fromisoformat(iso_date)
    return d.strftime("%d-%b-%Y")


def _parse_message(raw: bytes) -> dict:
    m = _emaillib.message_from_bytes(raw)
    # Body snippet: first text/plain part, first ~500 chars.
    snippet = ""
    if m.is_multipart():
        for part in m.walk():
            if part.get_content_type() == "text/plain":
                snippet = part.get_payload(decode=True).decode(errors="ignore")
                break
    else:
        payload = m.get_payload(decode=True)
        snippet = payload.decode(errors="ignore") if payload else ""
    return {"id": (m.get("Message-ID") or "").strip(),
            "from": (m.get("From") or "").strip(),
            "subject": (m.get("Subject") or "").strip(),
            "snippet": snippet[:500].strip()}


def fetch_candidate_messages(conn, applications: list[dict], since_date: str) -> list[dict]:
    """Search Gmail for messages since since_date whose company matches an active
    application, parse headers+snippet. `conn` is an imaplib-like object (mockable)."""
    since = _imap_date(since_date)
    seen_ids: set[str] = set()
    out: list[dict] = []
    companies = sorted({(a.get("company") or "").strip() for a in applications if a.get("company")})
    for company in companies:
        term = f'"{company}"' if " " in company else company
        typ, data = conn.search(None, "SINCE", since, "FROM", term)  # broad; refined by matcher later
        if typ != "OK" or not data or not data[0]:
            # Fall back to a subject search if FROM yields nothing.
            typ, data = conn.search(None, "SINCE", since, "SUBJECT", term)
            if typ != "OK" or not data or not data[0]:
                continue
        for num in data[0].split():
            typ, fetched = conn.fetch(num, "(RFC822)")
            if typ != "OK" or not fetched or not fetched[0]:
                continue
            first = fetched[0]
            if not isinstance(first, tuple) or first[1] is None:
                continue
            msg = _parse_message(first[1])
            if msg["id"] and msg["id"] not in seen_ids:
                seen_ids.add(msg["id"])
                out.append(msg)
    return out


def connect_imap(cfg: dict):  # pragma: no cover - network I/O
    conn = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    conn.login(cfg["from"], cfg["password"])
    conn.select("INBOX")
    return conn


def main() -> int:
    cfg = notify.email_cfg()
    today = datetime.date.today().isoformat()
    applications = store.load_store(APPLICATIONS_PATH)
    jobs = store.load_store(JOBS_PATH)
    state = load_state(STATE_PATH)
    since = state["last_scan"] or (datetime.date.fromisoformat(today)
                                   - datetime.timedelta(days=LOOKBACK_DAYS)).isoformat()

    active_apps = [a for a in applications if a.get("stage") in _ACTIVE]
    conn = connect_imap(cfg)
    try:
        messages = fetch_candidate_messages(conn, active_apps, since)
    finally:
        try:
            conn.logout()
        except Exception:
            pass

    result = decide_updates(messages, applications, jobs, state["processed_message_ids"], today)

    for inc in result["applied"] + result["inferred_new"]:
        applications, _ = store.upsert_application(applications, inc, today)
    from jsonschema import ValidationError
    invalid = []
    for app in applications:
        try:
            store.validate_item(app, "applications")
        except ValidationError as exc:
            invalid.append((app.get("company", "?"), exc.message))
    if invalid:
        # Don't persist a schema-invalid store, and don't stall silently: alert the
        # user (an email + non-zero exit) so a bad record in data/applications.json
        # gets fixed instead of quietly killing the daily job.
        lines = "\n".join(f"- {co}: {msg}" for co, msg in invalid)
        print(f"Pipeline scan ABORTED: {len(invalid)} application(s) fail schema validation:\n{lines}")
        body = ("Pipeline scan could not save — these applications fail schema validation. "
                f"Fix data/applications.json:\n{lines}")
        notify.send_email("Pipeline — scan error (validation)", f"<pre>{body}</pre>", body, cfg)
        return 1
    store.save_store(APPLICATIONS_PATH, applications)

    state["processed_message_ids"] = (state["processed_message_ids"] + result["processed_ids"])[-5000:]
    state["last_scan"] = today
    save_state(STATE_PATH, state)

    n_applied = len(result["applied"]) + len(result["inferred_new"])
    print(f"Pipeline scan: {n_applied} applied, {len(result['ambiguous'])} to confirm; "
          f"{len(applications)} applications total.")

    subject, html, text = build_pipeline_digest(
        result["applied"] + result["inferred_new"], result["ambiguous"], applications, today, STALL_DAYS)
    notify.send_email(subject, html, text, cfg)
    print(f"Pipeline digest emailed to {cfg['to']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
