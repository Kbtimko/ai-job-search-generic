"""The file that enforces the privacy boundary, held to what it actually copies.

A real LinkedIn export ships login IP addresses, payment receipts, ad-click history
and security challenges alongside the handful of files this package reads. Nothing
asserted which of them reached the repo tree, so a change to the glob would have
sailed through. Every test here builds a throwaway export and a throwaway
destination -- never the real export, never the real data/ directory.
"""

from pathlib import Path

from linkedin.ingest import NEEDED_FILES
from scripts.copy_linkedin_archive import main

# Mirrors the real export's shape: the jobs files sit in a Jobs/ subdirectory and
# everything else at the top level.
TOP_LEVEL = ["Connections.csv", "messages.csv", "Positions.csv",
             "Endorsement_Received_Info.csv", "Invitations.csv"]
IN_JOBS_DIR = ["Job Applications.csv", "Saved Jobs.csv", "Saved Jobs_1.csv"]

# Named after the real export's genuinely sensitive files. None may be copied.
DECOYS = ["Logins.csv", "Receipts_v2.csv", "Ads Clicked.csv", "Security Challenges.csv",
          "Rich Media.csv", "PhoneNumbers.csv"]


def _fake_export(tmp_path: Path) -> Path:
    source = tmp_path / "export"
    (source / "Jobs").mkdir(parents=True)
    for name in TOP_LEVEL + DECOYS:
        (source / name).write_text(f"header\n{name} row\n", encoding="utf-8")
    for name in IN_JOBS_DIR:
        (source / "Jobs" / name).write_text(f"header\n{name} row\n", encoding="utf-8")
    return source


def test_copies_exactly_the_needed_files_and_nothing_else(tmp_path):
    destination = tmp_path / "dest"
    assert main([str(_fake_export(tmp_path))], destination=destination) == 0
    assert {p.name for p in destination.iterdir()} == set(NEEDED_FILES)


def test_no_sensitive_export_file_reaches_the_destination(tmp_path):
    """Stated separately from the equality above so a future widening of NEEDED_FILES
    cannot quietly take these along with it."""
    destination = tmp_path / "dest"
    main([str(_fake_export(tmp_path))], destination=destination)
    copied = {p.name for p in destination.iterdir()}
    for name in DECOYS:
        assert name not in copied, f"{name} must never enter the repo tree"


def test_saved_jobs_are_present_in_the_export_but_not_copied(tmp_path):
    """The export has them and manifest can report them; the copy set excludes them
    because nothing in production reads them."""
    source = _fake_export(tmp_path)
    destination = tmp_path / "dest"
    main([str(source)], destination=destination)
    for name in ("Saved Jobs.csv", "Saved Jobs_1.csv"):
        assert (source / "Jobs" / name).exists(), "the fake export must actually have it"
        assert not (destination / name).exists()


def test_files_nested_in_a_subdirectory_are_found(tmp_path):
    """Job Applications.csv lives under Jobs/ in a real export; missing it would leave
    workstream 4 silently empty."""
    destination = tmp_path / "dest"
    main([str(_fake_export(tmp_path))], destination=destination)
    assert (destination / "Job Applications.csv").read_text().startswith("header")


def test_an_export_missing_a_file_still_copies_the_rest(tmp_path):
    """Not every account has every file. A partial export is not a failure."""
    source = _fake_export(tmp_path)
    (source / "Invitations.csv").unlink()
    destination = tmp_path / "dest"
    assert main([str(source)], destination=destination) == 0
    assert {p.name for p in destination.iterdir()} == set(NEEDED_FILES) - {"Invitations.csv"}


def test_a_source_that_is_not_a_directory_is_refused(tmp_path):
    destination = tmp_path / "dest"
    assert main([str(tmp_path / "nope")], destination=destination) == 1
    assert not destination.exists(), "nothing may be created for a bad source"


def test_wrong_argument_count_prints_usage_and_does_not_copy(tmp_path):
    destination = tmp_path / "dest"
    assert main([], destination=destination) == 2
    assert not destination.exists()
