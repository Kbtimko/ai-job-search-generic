import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.store import _cli

REPO = Path(__file__).resolve().parent.parent  # job-search/


@pytest.fixture
def cfg(tmp_path: Path) -> Path:
    """A self-contained config so these tests do not depend on the repo owner's metros."""
    path = tmp_path / "search-config.json"
    path.write_text(json.dumps({"locations": [
        {"key": "remote", "label": "Remote", "score": 10},
        {"key": "denver", "label": "Denver, CO", "score": 8},
    ]}))
    return path


def _run(args, config: Path):
    return subprocess.run(
        [sys.executable, "-m", "scripts.store", "--config", str(config), *args],
        cwd=REPO, capture_output=True, text=True,
    )


def test_cli_append_jobs_writes_and_reports(tmp_path: Path, cfg: Path):
    data = tmp_path / "jobs.json"
    incoming = tmp_path / "incoming.json"
    incoming.write_text(json.dumps([{
        "id": "acme-1", "title": "Senior Data Analyst", "company": "Acme",
        "location_type": "remote", "url": "https://x.com/1",
        "found_date": "2026-06-28", "status": "new"
    }]))
    result = _run(["append-jobs", "--data", str(data), "--incoming", str(incoming)], cfg)
    assert result.returncode == 0, result.stderr
    assert "Added 1" in result.stdout
    saved = json.loads(data.read_text())
    assert len(saved) == 1


def test_cli_append_jobs_rejects_invalid(tmp_path: Path, cfg: Path):
    data = tmp_path / "jobs.json"
    incoming = tmp_path / "incoming.json"
    incoming.write_text(json.dumps([{"id": "broken", "status": "nope"}]))
    result = _run(["append-jobs", "--data", str(data), "--incoming", str(incoming)], cfg)
    assert result.returncode != 0
    assert "invalid" in (result.stdout + result.stderr).lower()


def test_cli_append_jobs_handles_malformed_json(tmp_path: Path, cfg: Path):
    data = tmp_path / "jobs.json"
    incoming = tmp_path / "incoming.json"
    incoming.write_text("{not valid json")
    result = _run(["append-jobs", "--data", str(data), "--incoming", str(incoming)], cfg)
    assert result.returncode != 0
    assert "json" in (result.stdout + result.stderr).lower()
    assert "Traceback" not in (result.stdout + result.stderr)


def test_cli_upsert_applications_creates_and_advances(tmp_path: Path, capsys):
    data = tmp_path / "applications.json"
    inc = tmp_path / "inc.json"
    inc.write_text(json.dumps([{"company": "Acme", "title": "Director", "stage": "applied", "source": "manual"}]))
    assert _cli(["upsert-applications", "--data", str(data), "--incoming", str(inc)]) == 0
    saved = json.loads(data.read_text())
    assert saved[0]["stage"] == "applied" and saved[0]["applied_date"]

    inc.write_text(json.dumps([{"company": "Acme", "title": "Director", "stage": "interview", "source": "gmail", "evidence": "Onsite"}]))
    assert _cli(["upsert-applications", "--data", str(data), "--incoming", str(inc)]) == 0
    saved = json.loads(data.read_text())
    assert saved[0]["stage"] == "interview"
    assert len(saved) == 1  # upsert, not append


def test_cli_upsert_applications_rejects_malformed_json(tmp_path: Path):
    data = tmp_path / "applications.json"
    inc = tmp_path / "inc.json"
    inc.write_text("{not valid json")
    assert _cli(["upsert-applications", "--data", str(data), "--incoming", str(inc)]) == 2
    assert not data.exists()


def test_cli_upsert_applications_rejects_non_array(tmp_path: Path):
    data = tmp_path / "applications.json"
    inc = tmp_path / "inc.json"
    inc.write_text('{"company": "Acme"}')
    assert _cli(["upsert-applications", "--data", str(data), "--incoming", str(inc)]) == 2
    assert not data.exists()


def test_cli_upsert_applications_returns_1_on_invalid_result(tmp_path: Path):
    data = tmp_path / "applications.json"
    inc = tmp_path / "inc.json"
    # stage "consulting" is not in the applications schema enum
    inc.write_text(json.dumps([{"company": "Acme", "title": "Dir", "stage": "consulting", "source": "manual"}]))
    assert _cli(["upsert-applications", "--data", str(data), "--incoming", str(inc)]) == 1
    assert not data.exists()


def test_cli_update_jobs_marks_closed(tmp_path: Path, cfg: Path):
    data = tmp_path / "jobs.json"
    data.write_text(json.dumps([{
        "id": "acme-1", "title": "Analyst", "company": "Acme",
        "location_type": "remote", "url": "https://x.com/1",
        "found_date": "2026-06-28", "status": "new"
    }]))
    inc = tmp_path / "patch.json"
    inc.write_text(json.dumps([{"id": "acme-1", "status": "closed",
                                "posting_checked_date": "2026-07-02"}]))
    assert _cli(["--config", str(cfg), "update-jobs", "--data", str(data), "--incoming", str(inc)]) == 0
    saved = json.loads(data.read_text())
    assert saved[0]["status"] == "closed"
    assert saved[0]["posting_checked_date"] == "2026-07-02"


def test_cli_update_jobs_rejects_malformed_json(tmp_path: Path):
    data = tmp_path / "jobs.json"
    data.write_text("[]")
    inc = tmp_path / "patch.json"
    inc.write_text("{not valid json")
    assert _cli(["update-jobs", "--data", str(data), "--incoming", str(inc)]) == 2


def test_cli_update_jobs_rejects_non_array(tmp_path: Path):
    data = tmp_path / "jobs.json"
    data.write_text("[]")
    inc = tmp_path / "patch.json"
    inc.write_text('{"id": "acme-1"}')
    assert _cli(["update-jobs", "--data", str(data), "--incoming", str(inc)]) == 2


def test_cli_update_jobs_returns_1_on_invalid_result(tmp_path: Path):
    data = tmp_path / "jobs.json"
    data.write_text(json.dumps([{
        "id": "acme-1", "title": "Analyst", "company": "Acme",
        "location_type": "remote", "url": "https://x.com/1",
        "found_date": "2026-06-28", "status": "new"
    }]))
    inc = tmp_path / "patch.json"
    inc.write_text(json.dumps([{"id": "acme-1", "status": "nope"}]))  # not in enum
    assert _cli(["update-jobs", "--data", str(data), "--incoming", str(inc)]) == 1
    # unchanged on validation failure
    assert json.loads(data.read_text())[0]["status"] == "new"


def test_append_jobs_cli_rejects_unconfigured_location(tmp_path, monkeypatch):
    from scripts import locations
    cfg = tmp_path / "search-config.json"
    cfg.write_text(json.dumps({"titles": ["x"], "locations": [
        {"key": "remote", "label": "Remote", "score": 8}]}))
    monkeypatch.setattr(locations, "CONFIG_PATH", cfg)
    data = tmp_path / "jobs.json"
    data.write_text("[]")
    incoming = tmp_path / "in.json"
    incoming.write_text(json.dumps([{"id": "a", "title": "t", "company": "c",
        "location_type": "tokyo", "url": "u", "found_date": "2026-09-30", "status": "new"}]))
    rc = _cli(["append-jobs", "--data", str(data), "--incoming", str(incoming)])
    assert rc != 0
    assert json.loads(data.read_text()) == []


def _loc_cfg(tmp_path, monkeypatch):
    from scripts import locations
    cfg = tmp_path / "search-config.json"
    cfg.write_text(json.dumps({"titles": ["x"], "locations": [
        {"key": "remote", "label": "Remote", "score": 8}]}))
    monkeypatch.setattr(locations, "CONFIG_PATH", cfg)


def _job(id_, loc):
    return {"id": id_, "title": "t", "company": "c", "location_type": loc,
            "url": "u", "found_date": "2026-09-30", "status": "new"}


def test_update_jobs_cli_rejects_patch_to_unconfigured_location(tmp_path, monkeypatch):
    _loc_cfg(tmp_path, monkeypatch)
    data = tmp_path / "jobs.json"
    data.write_text(json.dumps([_job("a", "remote")]))
    before = data.read_text()
    incoming = tmp_path / "in.json"
    incoming.write_text(json.dumps([{"id": "a", "location_type": "tokyo"}]))
    rc = _cli(["update-jobs", "--data", str(data), "--incoming", str(incoming)])
    assert rc != 0
    assert data.read_text() == before


def test_update_jobs_cli_ignores_stale_location_on_unpatched_job(tmp_path, monkeypatch):
    _loc_cfg(tmp_path, monkeypatch)
    data = tmp_path / "jobs.json"
    data.write_text(json.dumps([_job("a", "remote"), _job("b", "tokyo")]))
    incoming = tmp_path / "in.json"
    incoming.write_text(json.dumps([{"id": "a", "status": "closed"}]))
    rc = _cli(["update-jobs", "--data", str(data), "--incoming", str(incoming)])
    assert rc == 0


def test_config_option_overrides_the_repo_config(tmp_path, cfg):
    only_denver = tmp_path / "denver-only.json"
    only_denver.write_text(json.dumps({"locations": [
        {"key": "denver", "label": "Denver, CO", "score": 8}]}))
    data = tmp_path / "jobs.json"
    data.write_text("[]")
    incoming = tmp_path / "in.json"
    incoming.write_text(json.dumps([_job("a", "remote")]))
    args = ["append-jobs", "--data", str(data), "--incoming", str(incoming)]
    assert _cli(["--config", str(only_denver), *args]) != 0
    assert _cli(["--config", str(cfg), *args]) == 0
