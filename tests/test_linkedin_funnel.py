import csv
from pathlib import Path

from linkedin.funnel import CAVEAT, run

FIXTURES = Path(__file__).parent / "fixtures" / "linkedin"


def _jobs():
    return [{"id": "nw-dir-analytics", "company": "Northwind Traders",
             "title": "Director of Analytics", "status": "new"}]


def _rows(tmp_path):
    return list(csv.DictReader(
        line for line in (tmp_path / "applications_enriched.csv").open()
        if not line.startswith("#")
    ))


def test_run_writes_the_enriched_csv(tmp_path):
    run(FIXTURES, tmp_path, jobs=_jobs())
    assert (tmp_path / "applications_enriched.csv").exists()


def test_output_leads_with_the_caveat_in_the_file_itself(tmp_path):
    run(FIXTURES, tmp_path, jobs=_jobs())
    text = (tmp_path / "applications_enriched.csv").read_text()
    assert text.startswith("#")
    assert "Easy Apply" in text
    assert "no response" in text.lower()


def test_rows_carry_the_documented_columns(tmp_path):
    run(FIXTURES, tmp_path, jobs=_jobs())
    rows = _rows(tmp_path)
    assert len(rows) == 2
    for column in ("application_date", "company", "job_title", "job_url",
                   "resume_name", "matched_job_id", "match_confidence"):
        assert column in rows[0], column


def test_matching_application_is_linked_to_the_job_store(tmp_path):
    run(FIXTURES, tmp_path, jobs=_jobs())
    northwind = next(r for r in _rows(tmp_path) if r["company"] == "Northwind Traders")
    assert northwind["matched_job_id"] == "nw-dir-analytics"


def test_unmatched_application_has_empty_job_id_not_a_guess(tmp_path):
    run(FIXTURES, tmp_path, jobs=_jobs())
    contoso = next(r for r in _rows(tmp_path) if r["company"] == "Contoso")
    assert contoso["matched_job_id"] == ""


def test_caveat_constant_states_the_sample_limits():
    assert "Easy Apply" in CAVEAT
    assert "not the funnel" in CAVEAT


def test_run_reports_row_count(tmp_path):
    result = run(FIXTURES, tmp_path, jobs=_jobs())
    assert result["rows"] == 2


def test_review_band_application_gets_no_job_id(tmp_path):
    """companies.Match guarantees a caller reading only ``matched`` cannot consume an
    unconfirmed match. The Easy Apply join must honour it: "Northwind Traders" against
    a job store holding only "Northwind Trading" scores 82.35 -- review band -- so
    falling back to ``result.candidate`` would stamp an unrelated req onto a 2026
    application on the strength of a fuzzy name alone."""
    from linkedin.companies import MATCH_CONFIG, match_company

    jobs = [{"id": "nwt-review", "company": "Northwind Trading",
             "title": "Data Lead", "status": "new"}]
    probe = match_company("Northwind Traders", [jobs[0]["company"]], {})
    assert probe.band == "review", "the fixture must actually land in the review band"
    assert MATCH_CONFIG["review_floor"] <= probe.score < MATCH_CONFIG["auto_accept"]

    run(FIXTURES, tmp_path, jobs=jobs)
    northwind = next(r for r in _rows(tmp_path) if r["company"] == "Northwind Traders")
    assert northwind["matched_job_id"] == ""
    assert northwind["match_confidence"] == ""
