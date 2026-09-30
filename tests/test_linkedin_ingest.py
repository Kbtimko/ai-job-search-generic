from datetime import date
from pathlib import Path

from linkedin.ingest import (
    _read_csv,
    load_applications,
    load_connections,
    load_endorsements,
    load_invitations,
    load_messages,
    load_positions,
    load_saved_jobs,
    manifest,
    parse_date,
    profile_slug,
)

FIXTURES = Path(__file__).parent / "fixtures" / "linkedin"


def test_parses_connections_format():
    assert parse_date("13 Aug 2026", "connections") == date(2026, 8, 13)


def test_parses_jobs_format():
    assert parse_date("5/20/26, 8:19 AM", "jobs") == date(2026, 5, 20)


def test_parses_messages_format():
    assert parse_date("2026-08-18 18:32:08 UTC", "messages") == date(2026, 8, 18)


def test_parses_positions_month_year_format():
    assert parse_date("Nov 2023", "positions") == date(2023, 11, 1)


def test_parses_endorsements_format():
    assert parse_date("2021/12/09 18:49:03 UTC", "endorsements") == date(2021, 12, 9)


def test_blank_date_returns_none():
    assert parse_date("", "positions") is None
    assert parse_date("   ", "jobs") is None


def test_unparseable_date_returns_none_and_records_failure():
    failures = []
    assert parse_date("not a date", "jobs", failures=failures) is None
    assert failures == [("jobs", "not a date")]


def test_wrong_format_for_file_does_not_silently_succeed():
    # A messages-format string handed to the jobs parser must not parse.
    assert parse_date("2026-08-18 18:32:08 UTC", "jobs") is None


def test_slug_from_full_url():
    assert profile_slug("https://www.linkedin.com/in/alexrivera") == "alexrivera"


def test_slug_from_schemeless_url():
    # Endorsements store URLs with no scheme.
    assert profile_slug("www.linkedin.com/in/jidonovan") == "jidonovan"


def test_slug_ignores_trailing_slash_and_query():
    assert profile_slug("https://www.linkedin.com/in/foo-bar/?trk=x") == "foo-bar"


def test_slug_is_lowercased():
    assert profile_slug("https://www.linkedin.com/in/AlexRivera") == "alexrivera"


def test_slug_missing_or_non_profile_url_returns_none():
    assert profile_slug("") is None
    assert profile_slug(None) is None
    assert profile_slug("https://www.linkedin.com/company/acme") is None


def test_load_connections_skips_the_notes_preamble():
    rows = load_connections(FIXTURES)
    assert len(rows) == 5
    assert rows[0]["first_name"] == "Ada"
    assert rows[0]["company"] == "Northwind Traders"


def test_load_connections_normalizes_headers_to_snake_case():
    rows = load_connections(FIXTURES)
    assert "connected_on" in rows[0]
    assert "email_address" in rows[0]


def test_load_connections_parses_dates_and_slugs():
    rows = load_connections(FIXTURES)
    assert rows[0]["connected_on_date"] == date(2019, 6, 10)
    assert rows[0]["slug"] == "adanolan"


def test_load_messages_parses_iso_dates_and_sender_slugs():
    rows = load_messages(FIXTURES)
    assert len(rows) == 6
    assert rows[0]["date_parsed"] == date(2026, 6, 1)
    assert rows[0]["sender_slug"] == "adanolan"


def test_load_messages_keeps_rows_with_empty_body_and_missing_slug():
    rows = load_messages(FIXTURES)
    empty = [r for r in rows if not r["content"].strip()]
    assert len(empty) == 1
    no_slug = [r for r in rows if r["sender_slug"] is None]
    assert len(no_slug) == 1
    assert no_slug[0]["from"] == "Unknown Sender"


def test_load_positions_treats_blank_finish_as_current():
    rows = load_positions(FIXTURES)
    current = [r for r in rows if r["finished_on_date"] is None]
    assert len(current) == 1
    assert current[0]["company_name"] == "Globex"
    assert current[0]["started_on_date"] == date(2022, 1, 1)


def test_load_endorsements_parses_slashed_utc_dates_and_schemeless_urls():
    rows = load_endorsements(FIXTURES)
    assert rows[0]["endorsement_date_parsed"] == date(2021, 12, 9)
    assert rows[0]["slug"] == "adanolan"


def test_load_invitations_exposes_direction_and_counterparty_slug():
    rows = load_invitations(FIXTURES)
    incoming = [r for r in rows if r["direction"] == "INCOMING"]
    assert len(incoming) == 1
    assert incoming[0]["slug"] == "boreyes"


def test_load_applications_parses_short_year_dates():
    rows = load_applications(FIXTURES)
    assert len(rows) == 2
    assert rows[0]["application_date_parsed"] == date(2019, 5, 31)


def test_load_saved_jobs_merges_split_files_when_present():
    rows = load_saved_jobs(FIXTURES)
    assert len(rows) == 2
    assert rows[0]["saved_date_parsed"] == date(2026, 6, 24)


def test_manifest_reports_found_files_and_row_counts():
    report = manifest(FIXTURES)
    assert report["Connections.csv"] == 5
    assert report["messages.csv"] == 6
    assert "Saved Jobs_1.csv" not in report  # absent from fixtures


def test_manifest_reports_missing_files_as_none():
    report = manifest(FIXTURES, include_missing=True)
    assert report["Saved Jobs_1.csv"] is None


def test_read_csv_preserves_newlines_inside_quoted_fields(tmp_path):
    """LinkedIn message bodies are multi-line; 26% of the real export's messages
    contain embedded newlines. Losing them welds words together across the break."""
    path = tmp_path / "multiline.csv"
    path.write_text('a,b\n"line one\nline two",x\n', encoding="utf-8")
    rows = _read_csv(path)
    assert len(rows) == 1
    assert rows[0]["a"] == "line one\nline two"


def test_read_csv_preserves_newlines_after_a_notes_preamble(tmp_path):
    """The preamble search must not re-break multi-line fields."""
    path = tmp_path / "preamble.csv"
    path.write_text('Notes:\n"blah"\n\nFirst Name,bio\nAda,"one\ntwo"\n', encoding="utf-8")
    rows = _read_csv(path, header_starts_with="First Name,")
    assert rows[0]["bio"] == "one\ntwo"


def test_read_csv_finds_the_header_at_any_preamble_length(tmp_path):
    """The header is *searched* for, not read at a fixed offset: the preamble length
    varies between exports, so a hardcoded skip would silently shift every column."""
    for preamble in ("", "Notes:\n", "Notes:\n\"blah\"\n\n", "a\nb\nc\nd\ne\n"):
        path = tmp_path / f"p{len(preamble)}.csv"
        path.write_text(preamble + "First Name,Company\nAda,Northwind\n", encoding="utf-8")
        rows = _read_csv(path, header_starts_with="First Name,")
        assert rows == [{"first_name": "Ada", "company": "Northwind"}], repr(preamble)


def test_saved_jobs_are_listed_but_never_copied_into_the_repo():
    """ARCHIVE_FILES served double duty as both the manifest list and the copy list,
    so 221 rows of job-interest history were staged into the repo tree for a reader
    that does not exist: workstream 3 was dropped and workstream 4 reduced to Job
    Applications only. Only what is needed crosses the privacy boundary."""
    from linkedin.ingest import ARCHIVE_FILES, NEEDED_FILES

    for name in ("Saved Jobs.csv", "Saved Jobs_1.csv"):
        assert name in ARCHIVE_FILES, "a user must still be able to see the export has it"
        assert name not in NEEDED_FILES, f"{name} has no production reader"
    assert set(NEEDED_FILES) <= set(ARCHIVE_FILES)
    assert "Job Applications.csv" in NEEDED_FILES, "workstream 4 does read this one"


def test_manifest_still_reports_saved_jobs_when_the_export_has_them(tmp_path):
    """Excluding a file from the copy set must not blind the manifest to it."""
    (tmp_path / "Saved Jobs.csv").write_text(
        "Saved Date,Job Url\n\"6/24/26, 4:07 PM\",https://example.com/1\n",
        encoding="utf-8",
    )
    assert manifest(tmp_path)["Saved Jobs.csv"] == 1
