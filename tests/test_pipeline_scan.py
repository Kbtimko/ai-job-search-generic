from scripts.pipeline_scan import classify_email


def test_classify_rejection_high():
    assert classify_email("Update on your application",
                          "Unfortunately we have decided to move forward with other candidates.") == ("rejected", "high")


def test_classify_interview_high():
    assert classify_email("Interview invitation — Director, Analytics",
                          "We'd like to schedule an interview.") == ("interview", "high")


def test_classify_applied_confirmation_high():
    assert classify_email("We received your application", "Thank you for applying to Acme.") == ("applied", "high")


def test_classify_screening_high():
    assert classify_email("Quick call?", "I'd love to set up a recruiter call.") == ("screening", "high")


def test_classify_priority_rejection_beats_interview():
    # A rejection that mentions the interview process must classify as rejected.
    assert classify_email("Re: interview",
                          "After your interview, unfortunately we won't be moving forward.")[0] == "rejected"


def test_classify_weak_is_low_confidence():
    assert classify_email("Your application", "We have an update on your application status.") == ("applied", "low")


def test_classify_none_for_newsletter():
    assert classify_email("This week in analytics jobs", "Here are 10 new roles for you.") == (None, "none")


from scripts.pipeline_scan import match_application

_APPS = [
    {"company": "Acme Analytics", "title": "Director", "stage": "applied"},
    {"company": "Beta Corp", "title": "Lead", "stage": "screening"},
]


def test_match_by_sender_domain():
    msg = {"from": "recruiting@acmeanalytics.com", "subject": "Next steps", "snippet": ""}
    assert match_application(msg, _APPS)["company"] == "Acme Analytics"


def test_match_by_company_in_subject():
    msg = {"from": "no-reply@greenhouse.io", "subject": "Your Beta Corp application", "snippet": ""}
    assert match_application(msg, _APPS)["company"] == "Beta Corp"


def test_match_none_when_no_company_signal():
    msg = {"from": "no-reply@greenhouse.io", "subject": "An update", "snippet": "hello"}
    assert match_application(msg, _APPS) is None


def test_match_skips_degenerate_short_company():
    # A 1-char company must not cross-match arbitrary addresses/text.
    apps = [{"company": "X", "title": "Eng", "stage": "applied"}]
    msg = {"from": "no-reply@greenhouse.io", "subject": "Your application", "snippet": "hello"}
    assert match_application(msg, apps) is None


from scripts.pipeline_scan import decide_updates

_APPS2 = [{"job_id": "acme-dir-2026-06", "company": "Acme Analytics", "title": "Director", "stage": "applied"}]
_JOBS = [{"id": "beta-lead-2026-06", "company": "Beta Corp", "title": "Lead"}]


def _msg(mid, frm, subj, snip=""):
    return {"id": mid, "from": frm, "subject": subj, "snippet": snip}


def test_decide_high_confidence_forward_change():
    msgs = [_msg("m1", "team@acmeanalytics.com", "Interview invitation", "schedule an interview")]
    out = decide_updates(msgs, _APPS2, _JOBS, [], "2026-07-01")
    assert len(out["applied"]) == 1 and out["applied"][0]["stage"] == "interview"
    assert out["applied"][0]["job_id"] == "acme-dir-2026-06"
    assert out["processed_ids"] == ["m1"]


def test_decide_skips_already_processed():
    msgs = [_msg("m1", "team@acmeanalytics.com", "Interview invitation", "schedule an interview")]
    out = decide_updates(msgs, _APPS2, _JOBS, ["m1"], "2026-07-01")
    assert out["applied"] == [] and out["processed_ids"] == []


def test_decide_backward_is_noop_but_processed():
    msgs = [_msg("m2", "team@acmeanalytics.com", "We received your application", "thank you for applying")]
    out = decide_updates(msgs, _APPS2, _JOBS, [], "2026-07-01")  # already 'applied'
    assert out["applied"] == [] and out["ambiguous"] == [] and out["processed_ids"] == ["m2"]


def test_decide_low_confidence_is_ambiguous_not_applied():
    msgs = [_msg("m3", "team@acmeanalytics.com", "Your application", "we have an update on your application")]
    out = decide_updates(msgs, [{"job_id": "x", "company": "Acme Analytics", "title": "Director", "stage": "draft"}],
                         _JOBS, [], "2026-07-01")
    assert out["applied"] == [] and len(out["ambiguous"]) == 1


def test_decide_infers_new_entry_from_confirmation_matching_jobs():
    msgs = [_msg("m4", "no-reply@greenhouse.io", "Beta Corp — application received", "thank you for applying")]
    out = decide_updates(msgs, [], _JOBS, [], "2026-07-01")
    assert len(out["inferred_new"]) == 1
    assert out["inferred_new"][0]["company"] == "Beta Corp" and out["inferred_new"][0]["stage"] == "applied"


def test_decide_unmatched_confirmation_is_ambiguous():
    msgs = [_msg("m5", "no-reply@greenhouse.io", "Gamma Inc — application received", "thank you for applying")]
    out = decide_updates(msgs, [], _JOBS, [], "2026-07-01")  # Gamma not in jobs
    assert out["inferred_new"] == [] and len(out["ambiguous"]) == 1


def test_decide_low_confidence_backward_is_silent_noop():
    # low-confidence AND backward: forward-transition gate fires first, so the
    # message is neither applied nor queued as ambiguous, but IS marked processed.
    apps = [{"company": "Acme Analytics", "title": "Director", "stage": "interview"}]
    msgs = [_msg("m6", "team@acmeanalytics.com", "Your application", "we have an update on your application")]
    out = decide_updates(msgs, apps, _JOBS, [], "2026-07-01")
    assert out["applied"] == [] and out["ambiguous"] == []
    assert out["processed_ids"] == ["m6"]


def test_decide_inferred_new_prefers_longest_company_match():
    jobs = [{"id": "alpha", "company": "Alpha"}, {"id": "alpha-corp", "company": "Alpha Corp"}]
    msgs = [_msg("m7", "no-reply@greenhouse.io", "Alpha Corp — application received", "thank you for applying")]
    out = decide_updates(msgs, [], jobs, [], "2026-07-01")
    assert len(out["inferred_new"]) == 1
    assert out["inferred_new"][0]["job_id"] == "alpha-corp"


from scripts.pipeline_scan import build_pipeline_digest


def test_digest_lists_changes_ambiguous_and_stalled():
    applied = [{"company": "Acme", "title": "Director", "stage": "interview", "evidence": "Interview invitation"}]
    ambiguous = [{"company": "Beta", "subject": "update", "guessed_stage": "rejected", "current_stage": "applied", "reason": "low"}]
    apps = [
        {"company": "Acme", "title": "Director", "stage": "interview", "applied_date": "2026-06-30",
         "stage_history": [{"stage": "interview", "date": "2026-07-01", "source": "gmail"}]},
        {"company": "Stale Co", "title": "Analyst", "stage": "applied", "applied_date": "2026-06-01",
         "stage_history": [{"stage": "applied", "date": "2026-06-01", "source": "manual"}]},
    ]
    subject, html, text = build_pipeline_digest(applied, ambiguous, apps, "2026-07-01", stall_days=14)
    assert "Acme" in text and "interview" in text
    assert "Beta" in text and "rejected" in text          # ambiguous section
    assert "Stale Co" in text                             # stalled (>14d since 2026-06-01)
    assert "Stale Co" in html


def test_digest_zero_changes_note():
    subject, html, text = build_pipeline_digest([], [], [], "2026-07-01")
    assert "no pipeline updates" in (subject + text).lower()


def test_digest_stalled_only_reflected_in_subject():
    # A stalled-only digest must not read "0 updates" — the recipient needs a reason to open it.
    apps = [{"company": "Stale Co", "title": "Analyst", "stage": "applied", "applied_date": "2026-06-01",
             "stage_history": [{"stage": "applied", "date": "2026-06-01", "source": "manual"}]}]
    subject, html, text = build_pipeline_digest([], [], apps, "2026-07-01", stall_days=14)
    assert "stalled" in subject.lower()
    assert "Stale Co" in text


import json
from pathlib import Path
from scripts.pipeline_scan import load_state, save_state, fetch_candidate_messages


def test_state_roundtrip_defaults(tmp_path: Path):
    p = tmp_path / "pipeline-state.json"
    assert load_state(p) == {"last_scan": None, "processed_message_ids": []}
    save_state(p, {"last_scan": "2026-07-01", "processed_message_ids": ["m1"]})
    assert load_state(p)["processed_message_ids"] == ["m1"]


class _FakeIMAP:
    """Minimal imaplib-like stub: one message from Acme."""
    def search(self, charset, *criteria):
        return "OK", [b"1"]

    def fetch(self, num, spec):
        raw = (b"From: team@acmeanalytics.com\r\n"
               b"Subject: Interview invitation\r\n"
               b"Message-ID: <abc@acme>\r\n\r\n"
               b"We'd like to schedule an interview.\r\n")
        return "OK", [(b"1 (RFC822 {len})", raw)]


def test_fetch_candidate_messages_parses_headers():
    apps = [{"company": "Acme Analytics", "title": "Director", "stage": "applied"}]
    msgs = fetch_candidate_messages(_FakeIMAP(), apps, "2026-06-01")
    assert len(msgs) == 1
    m = msgs[0]
    assert "acmeanalytics.com" in m["from"]
    assert m["subject"] == "Interview invitation"
    assert m["id"] == "<abc@acme>"
    assert "schedule an interview" in m["snippet"].lower()


class _RecordingIMAP:
    """Captures search criteria; returns one message on the first company search."""
    def __init__(self):
        self.criteria = []
    def search(self, charset, *criteria):
        self.criteria.append(criteria)
        return "OK", [b"1"]
    def fetch(self, num, spec):
        raw = (b"From: team@acmeanalytics.com\r\nSubject: Interview invitation\r\n"
               b"Message-ID: <abc@acme>\r\n\r\nWe'd like to schedule an interview.\r\n")
        return "OK", [(b"1 (RFC822 {n}", raw), b")"]  # realistic: trailing literal-close token


def test_fetch_quotes_multiword_company_and_survives_literal_token():
    conn = _RecordingIMAP()
    msgs = fetch_candidate_messages(conn, [{"company": "Acme Analytics", "stage": "applied"}], "2026-06-01")
    assert len(msgs) == 1 and msgs[0]["id"] == "<abc@acme>"     # trailing b")" token did not crash
    assert any('"Acme Analytics"' in c for c in conn.criteria[0])  # multi-word company was quoted


class _ExpungedIMAP:
    """Message expunged between SEARCH and FETCH: fetch returns only the close token."""
    def search(self, charset, *criteria):
        return "OK", [b"1"]
    def fetch(self, num, spec):
        return "OK", [b")"]


def test_fetch_skips_expunged_message_without_crashing():
    msgs = fetch_candidate_messages(_ExpungedIMAP(), [{"company": "Acme Analytics", "stage": "applied"}], "2026-06-01")
    assert msgs == []


def test_load_state_recovers_from_non_dict_json(tmp_path: Path):
    p = tmp_path / "pipeline-state.json"
    p.write_text('["corrupted"]')
    assert load_state(p) == {"last_scan": None, "processed_message_ids": []}


from scripts import pipeline_scan


def test_main_applies_changes_and_sends_digest(tmp_path: Path, monkeypatch):
    apps_path = tmp_path / "applications.json"
    jobs_path = tmp_path / "jobs.json"
    state_path = tmp_path / "pipeline-state.json"
    apps_path.write_text(json.dumps([
        {"job_id": "acme-dir-2026-06", "company": "Acme Analytics", "title": "Director",
         "stage": "applied", "applied_date": "2026-06-20",
         "stage_history": [{"stage": "applied", "date": "2026-06-20", "source": "manual"}]}]))
    jobs_path.write_text("[]")

    monkeypatch.setattr(pipeline_scan, "APPLICATIONS_PATH", apps_path)
    monkeypatch.setattr(pipeline_scan, "JOBS_PATH", jobs_path)
    monkeypatch.setattr(pipeline_scan, "STATE_PATH", state_path)
    monkeypatch.setattr(pipeline_scan, "connect_imap", lambda cfg: object())
    monkeypatch.setattr(pipeline_scan, "fetch_candidate_messages",
                        lambda conn, apps, since: [
                            {"id": "<m1@acme>", "from": "team@acmeanalytics.com",
                             "subject": "Interview invitation", "snippet": "schedule an interview"}])
    monkeypatch.setattr(pipeline_scan.notify, "email_cfg",
                        lambda: {"to": "me@x.com", "from": "me@x.com", "password": "pw"})
    sent = {}
    monkeypatch.setattr(pipeline_scan.notify, "send_email",
                        lambda subj, html, text, cfg: sent.update(subject=subj, text=text))

    rc = pipeline_scan.main()
    assert rc == 0
    saved = json.loads(apps_path.read_text())
    assert saved[0]["stage"] == "interview"
    assert json.loads(state_path.read_text())["processed_message_ids"] == ["<m1@acme>"]
    assert "interview" in sent["text"].lower()


def test_main_alerts_and_returns_1_on_invalid_result(tmp_path: Path, monkeypatch):
    # A schema-invalid record must not silently stall the job: alert + exit 1, no save/state write.
    apps_path = tmp_path / "applications.json"
    jobs_path = tmp_path / "jobs.json"
    state_path = tmp_path / "pipeline-state.json"
    apps_path.write_text(json.dumps([{"company": "Bad Co", "stage": "consulting"}]))  # bad stage enum
    jobs_path.write_text("[]")
    monkeypatch.setattr(pipeline_scan, "APPLICATIONS_PATH", apps_path)
    monkeypatch.setattr(pipeline_scan, "JOBS_PATH", jobs_path)
    monkeypatch.setattr(pipeline_scan, "STATE_PATH", state_path)
    monkeypatch.setattr(pipeline_scan, "connect_imap", lambda cfg: object())
    monkeypatch.setattr(pipeline_scan, "fetch_candidate_messages", lambda conn, apps, since: [])
    monkeypatch.setattr(pipeline_scan.notify, "email_cfg",
                        lambda: {"to": "me@x.com", "from": "me@x.com", "password": "pw"})
    sent = {}
    monkeypatch.setattr(pipeline_scan.notify, "send_email", lambda s, h, t, c: sent.update(subject=s))
    rc = pipeline_scan.main()
    assert rc == 1
    assert "error" in sent["subject"].lower()
    assert not state_path.exists()  # state not advanced on abort
