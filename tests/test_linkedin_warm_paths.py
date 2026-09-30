import shutil
from datetime import date
from pathlib import Path

import pytest

import linkedin.warm_paths as wp
from linkedin.warm_paths import warm_paths

FIXTURES = Path(__file__).parent / "fixtures" / "linkedin"
TODAY = date(2026, 8, 19)


@pytest.fixture(autouse=True)
def _clear_cache():
    wp._CACHE.clear()
    yield
    wp._CACHE.clear()


def _append(path: Path, lines: list[str]) -> None:
    text = path.read_text()
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text + "".join(line + "\n" for line in lines))


def _extended_archive(tmp_path: Path) -> Path:
    """The shared fixture plus two Northwind ties: Gus (recent, weak) and Hal
    (dormant former colleague, strong)."""
    archive = tmp_path / "archive"
    shutil.copytree(FIXTURES, archive)
    _append(archive / "Connections.csv", [
        "Gus,Hale,https://www.linkedin.com/in/gushale,,Northwind Traders,Data Analyst,01 Jan 2025",
        "Hal,Ito,https://www.linkedin.com/in/halito,,Northwind Traders,Director of Data,01 Jun 2020",
    ])
    _append(archive / "messages.csv", [
        '"conv-g","","Gus Hale","https://www.linkedin.com/in/gushale","Alex Rivera",'
        '"https://www.linkedin.com/in/alexrivera","2026-08-01 10:00:00 UTC","","hey","INBOX",""',
        '"conv-h","","Hal Ito","https://www.linkedin.com/in/halito","Alex Rivera",'
        '"https://www.linkedin.com/in/alexrivera","2020-07-01 10:00:00 UTC","","hi","INBOX",""',
    ])
    return archive


def _job(job_id, company):
    return {"id": job_id, "company": company, "title": "Director of Analytics"}


def test_matched_job_gets_count_and_ranked_top():
    result = warm_paths([_job("nw", "Northwind Traders")], FIXTURES, TODAY)
    path = result["nw"]
    assert path["count"] == 2
    assert [t["name"] for t in path["top"]] == ["Ada Nolan", "Dee Okafor"]
    ada = path["top"][0]
    assert ada["profile_url"] == "https://www.linkedin.com/in/adanolan"
    assert ada["title"] == "Director of Analytics"
    assert ada["recency"] == "active"
    assert ada["former_colleague"] is True
    assert path["top"][1]["recency"] == "never messaged"


def test_top_is_capped_at_three_and_count_is_everyone(tmp_path):
    result = warm_paths([_job("nw", "Northwind Traders")], _extended_archive(tmp_path), TODAY)
    assert result["nw"]["count"] == 4
    assert len(result["nw"]["top"]) == 3


def test_dormant_former_colleague_outranks_recent_weak_tie(tmp_path):
    """Recency is a tag, never a rank input: Hal (dormant, 61) beats Gus (active, 11)."""
    result = warm_paths([_job("nw", "Northwind Traders")], _extended_archive(tmp_path), TODAY)
    top = result["nw"]["top"]
    assert [t["name"] for t in top] == ["Ada Nolan", "Hal Ito", "Gus Hale"]
    assert [t["recency"] for t in top] == ["active", "dormant", "active"]


def test_review_band_company_is_not_matched():
    # "Northwind Trading Co" scores ~82 against "Northwind Traders": review, not auto.
    result = warm_paths([_job("nt", "Northwind Trading Co")], FIXTURES, TODAY)
    assert result == {}


def test_unmatched_company_is_absent():
    result = warm_paths([_job("um", "Umbrella Biotech"), _job("nw", "Northwind Traders")],
                        FIXTURES, TODAY)
    assert set(result) == {"nw"}


def test_blank_job_company_is_not_matched():
    result = warm_paths([_job("a", ""), {"id": "b", "title": "x"}], FIXTURES, TODAY)
    assert result == {}


def test_job_without_id_is_skipped():
    result = warm_paths([{"company": "Northwind Traders", "title": "x"}], FIXTURES, TODAY)
    assert result == {}


def test_two_jobs_at_same_company_both_get_paths():
    result = warm_paths([_job("nw1", "Northwind Traders"), _job("nw2", "Northwind Traders")],
                        FIXTURES, TODAY)
    assert result["nw1"] == result["nw2"]


def test_tie_without_slug_has_empty_profile_url(tmp_path):
    archive = tmp_path / "archive"
    shutil.copytree(FIXTURES, archive)
    _append(archive / "Connections.csv",
            ["Ivy,Moss,,,Northwind Traders,Analyst,01 Jan 2025"])
    top = warm_paths([_job("nw", "Northwind Traders")], archive, TODAY)["nw"]["top"]
    ivy = [t for t in top if t["name"] == "Ivy Moss"]
    assert ivy and ivy[0]["profile_url"] == ""


def test_archive_date_is_connections_mtime(tmp_path):
    archive = tmp_path / "archive"
    shutil.copytree(FIXTURES, archive)
    import os, datetime as dt
    stamp = dt.datetime(2026, 8, 20, 12, 0).timestamp()
    os.utime(archive / "Connections.csv", (stamp, stamp))
    result = warm_paths([_job("nw", "Northwind Traders")], archive, TODAY)
    assert result["nw"]["archive_date"] == date(2026, 8, 20)


def test_missing_archive_returns_empty(tmp_path):
    assert warm_paths([_job("nw", "Northwind Traders")], tmp_path / "nope", TODAY) == {}


def test_archive_without_connections_returns_empty(tmp_path):
    (tmp_path / "archive").mkdir()
    assert warm_paths([_job("nw", "Northwind Traders")], tmp_path / "archive", TODAY) == {}


def test_load_failure_returns_empty_and_logs_no_row_content(monkeypatch, caplog):
    def boom(_archive_dir, *a, **k):
        raise ValueError("Ada Nolan adanolan Director of Analytics")
    monkeypatch.setattr(wp, "load_connections", boom)
    with caplog.at_level("WARNING"):
        assert warm_paths([_job("nw", "Northwind Traders")], FIXTURES, TODAY) == {}
    assert "ValueError" in caplog.text
    assert "Ada" not in caplog.text and "adanolan" not in caplog.text


def test_match_failure_returns_empty_and_logs_no_row_content(monkeypatch, caplog):
    def boom(*a, **k):
        raise ValueError("Ada Nolan adanolan")
    monkeypatch.setattr(wp, "match_company", boom)
    with caplog.at_level("WARNING"):
        assert warm_paths([_job("nw", "Northwind Traders")], FIXTURES, TODAY) == {}
    assert "ValueError" in caplog.text
    assert "Ada" not in caplog.text and "adanolan" not in caplog.text


def _count_builds(monkeypatch):
    calls = []
    real = wp._build_index
    def counting(archive_dir, today):
        calls.append(1)
        return real(archive_dir, today)
    monkeypatch.setattr(wp, "_build_index", counting)
    return calls


def test_second_call_with_unchanged_archive_does_not_rebuild(monkeypatch):
    calls = _count_builds(monkeypatch)
    jobs = [_job("nw", "Northwind Traders")]
    first = warm_paths(jobs, FIXTURES, TODAY)
    second = warm_paths(jobs, FIXTURES, TODAY)
    assert first == second
    assert len(calls) == 1


def test_touching_an_archive_file_rebuilds(monkeypatch, tmp_path):
    import os
    archive = tmp_path / "archive"
    shutil.copytree(FIXTURES, archive)
    calls = _count_builds(monkeypatch)
    jobs = [_job("nw", "Northwind Traders")]
    warm_paths(jobs, archive, TODAY)
    stat = (archive / "messages.csv").stat()
    os.utime(archive / "messages.csv", ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    warm_paths(jobs, archive, TODAY)
    assert len(calls) == 2


def test_new_day_rebuilds(monkeypatch):
    calls = _count_builds(monkeypatch)
    jobs = [_job("nw", "Northwind Traders")]
    warm_paths(jobs, FIXTURES, TODAY)
    warm_paths(jobs, FIXTURES, date(2026, 8, 20))
    assert len(calls) == 2


def test_cache_keeps_only_the_latest_entry():
    jobs = [_job("nw", "Northwind Traders")]
    warm_paths(jobs, FIXTURES, TODAY)
    warm_paths(jobs, FIXTURES, date(2026, 8, 20))
    assert len(wp._CACHE) == 1
