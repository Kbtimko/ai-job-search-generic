import json
import urllib.error
from pathlib import Path

from scripts.openness_check import classify_posting, fetch, main


def _job(**over):
    base = {"id": "x", "title": "T", "company": "C", "location_type": "remote",
            "url": "https://x.com/x", "found_date": "2026-06-28", "status": "new"}
    base.update(over)
    return base


def test_main_marks_closed_and_stamps_checked_date(tmp_path: Path, monkeypatch, capsys):
    jobs = [
        _job(id="dead", status="new", url="https://x.com/dead"),
        _job(id="live", status="interested", url="https://x.com/live"),
        _job(id="applied-dead", status="applied", url="https://x.com/ad"),  # no-clobber
        _job(id="no-url", status="new", url=""),                            # skipped
    ]
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps(jobs))

    responses = {
        "https://x.com/dead": (404, ""),
        "https://x.com/live": (200, "Apply now"),
        "https://x.com/ad": (404, ""),
    }
    monkeypatch.setattr("scripts.openness_check.fetch",
                        lambda url, timeout=10: responses[url])

    rc = main(jobs_path=path, today="2026-07-02")
    assert rc == 0
    saved = {j["id"]: j for j in json.loads(path.read_text())}
    assert saved["dead"]["status"] == "closed"
    assert saved["dead"]["posting_checked_date"] == "2026-07-02"
    assert saved["live"]["status"] == "interested"               # open, status unchanged
    assert saved["live"]["posting_checked_date"] == "2026-07-02"  # but stamped
    assert saved["applied-dead"]["status"] == "applied"          # no-clobber
    assert "posting_checked_date" not in saved["applied-dead"]   # not even checked
    assert "posting_checked_date" not in saved["no-url"]         # skipped
    out = capsys.readouterr().out
    assert "checked 2" in out and "closed 1" in out


def test_fetch_network_error_returns_none(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("no route")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    status, body = fetch("https://example.com/job")
    assert status is None
    assert body == ""


def test_404_is_closed():
    assert classify_posting(404, "") == "closed"


def test_410_is_closed():
    assert classify_posting(410, "whatever") == "closed"


def test_200_with_dead_phrase_is_closed():
    for phrase in ["no longer accepting", "position has been filled",
                   "no longer available", "job posting closed"]:
        assert classify_posting(200, f"<html>This job is {phrase}.</html>") == "closed"


def test_dead_phrase_is_case_insensitive():
    assert classify_posting(200, "NO LONGER ACCEPTING applications") == "closed"


def test_200_plain_is_open():
    assert classify_posting(200, "<html>Apply now! Great role.</html>") == "open"


def test_403_is_unknown():
    assert classify_posting(403, "") == "unknown"


def test_none_status_is_unknown():
    assert classify_posting(None, "") == "unknown"
