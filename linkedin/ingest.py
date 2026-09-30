"""Load and normalize the LinkedIn archive CSVs.

Each archive file uses its own date format, so parsing is per-file and explicit.
Inferring a format globally silently corrupts dates: ``5/20/26`` and ``13 Aug 2026``
both appear in the same export.
"""

from __future__ import annotations

import csv
import re
from datetime import date, datetime
from pathlib import Path

# One date format per source file. Never infer these.
DATE_FORMATS = {
    "connections": "%d %b %Y",              # 13 Aug 2026
    "jobs": "%m/%d/%y, %I:%M %p",           # 5/20/26, 8:19 AM
    "messages": "%Y-%m-%d %H:%M:%S UTC",    # 2026-08-18 18:32:08 UTC
    "positions": "%b %Y",                   # Nov 2023
    "endorsements": "%Y/%m/%d %H:%M:%S UTC",  # 2021/12/09 18:49:03 UTC
}


def parse_date(raw: str, source: str, failures: list | None = None) -> date | None:
    """Parse ``raw`` using the format registered for ``source``.

    Returns None for blank input. On a parse failure, appends ``(source, raw)`` to
    ``failures`` if given and returns None -- rows are logged, never dropped silently.
    """
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, DATE_FORMATS[source]).date()
    except ValueError:
        if failures is not None:
            failures.append((source, text))
        return None


_SLUG_RE = re.compile(r"linkedin\.com/in/([^/?#]+)", re.IGNORECASE)


def profile_slug(url: str | None) -> str | None:
    """Extract the profile slug from a LinkedIn member URL.

    The join key across archive files. Tolerates missing scheme (endorsements store
    bare ``www.linkedin.com/in/x``), trailing slashes, and tracking query strings.
    """
    match = _SLUG_RE.search((url or "").strip())
    return match.group(1).lower().rstrip("/") if match else None


# Archive files whose real header row is preceded by a notes preamble, mapped to that
# row's prefix. One definition, so load_connections and manifest cannot disagree.
PREAMBLE_HEADERS = {"Connections.csv": "First Name,"}

# Everything ``manifest`` reports on, so a user can see what their export contains.
ARCHIVE_FILES = [
    "Connections.csv",
    "messages.csv",
    "Positions.csv",
    "Endorsement_Received_Info.csv",
    "Invitations.csv",
    "Job Applications.csv",
    "Saved Jobs.csv",
    "Saved Jobs_1.csv",
]

# Files with a production reader -- the only ones copy_linkedin_archive.py stages into
# the repo tree. The saved-jobs files are deliberately absent: workstream 3 was dropped
# and workstream 4 reduced to Job Applications, leaving 221 rows of job-interest history
# with nothing reading them. They stay in ARCHIVE_FILES above so ``manifest`` can still
# report them, but only what is needed crosses the privacy boundary.
UNREAD_ARCHIVE_FILES = ["Saved Jobs.csv", "Saved Jobs_1.csv"]
NEEDED_FILES = [name for name in ARCHIVE_FILES if name not in UNREAD_ARCHIVE_FILES]


def _snake(header: str) -> str:
    """Normalize a CSV header to snake_case. Export headers vary between accounts."""
    text = re.sub(r"[^0-9a-zA-Z]+", "_", (header or "").strip())
    return re.sub(r"_+", "_", text).strip("_").lower()


def _read_csv(path: Path, header_starts_with: str | None = None) -> list[dict]:
    r"""Read a CSV into snake_cased dicts.

    ``header_starts_with`` locates the real header row when the file opens with a
    notes preamble (Connections.csv does). Searching for it beats assuming a fixed
    offset, because the preamble length varies between exports.

    Uses ``readlines()``, which keeps each line's terminator, rather than
    ``read().split("\n")``, which discards it: 26% of the real export's messages
    contain newlines inside the quoted body, and a separator-less split leaves
    ``csv.reader`` nothing to put back, welding words together across the break.
    """
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()
    start = 0
    if header_starts_with:
        start = next(
            (i for i, line in enumerate(lines) if line.startswith(header_starts_with)), 0
        )
    reader = csv.reader(lines[start:])
    try:
        header = [_snake(h) for h in next(reader)]
    except StopIteration:
        return []
    rows = []
    for values in reader:
        if not any(v.strip() for v in values):
            continue
        row = dict(zip(header, values))
        for key in header[len(values):]:
            row.setdefault(key, "")
        rows.append(row)
    return rows


def load_connections(archive_dir: Path, failures: list | None = None) -> list[dict]:
    rows = _read_csv(
        archive_dir / "Connections.csv",
        header_starts_with=PREAMBLE_HEADERS["Connections.csv"],
    )
    for row in rows:
        row["connected_on_date"] = parse_date(
            row.get("connected_on", ""), "connections", failures
        )
        row["slug"] = profile_slug(row.get("url"))
    return rows


def load_messages(archive_dir: Path, failures: list | None = None) -> list[dict]:
    rows = _read_csv(archive_dir / "messages.csv")
    for row in rows:
        row["date_parsed"] = parse_date(row.get("date", ""), "messages", failures)
        row["sender_slug"] = profile_slug(row.get("sender_profile_url"))
    return rows


def load_positions(archive_dir: Path, failures: list | None = None) -> list[dict]:
    rows = _read_csv(archive_dir / "Positions.csv")
    for row in rows:
        row["started_on_date"] = parse_date(row.get("started_on", ""), "positions", failures)
        # A blank Finished On means the position is current, not a parse failure.
        row["finished_on_date"] = parse_date(row.get("finished_on", ""), "positions", failures)
    return rows


def load_endorsements(archive_dir: Path, failures: list | None = None) -> list[dict]:
    rows = _read_csv(archive_dir / "Endorsement_Received_Info.csv")
    for row in rows:
        row["endorsement_date_parsed"] = parse_date(
            row.get("endorsement_date", ""), "endorsements", failures
        )
        row["slug"] = profile_slug(row.get("endorser_public_url"))
    return rows


def load_invitations(archive_dir: Path, failures: list | None = None) -> list[dict]:
    """Invitations, with ``slug`` set to the *other* party's profile.

    For INCOMING that is the inviter; for OUTGOING it is the invitee.
    """
    rows = _read_csv(archive_dir / "Invitations.csv")
    for row in rows:
        row["sent_at_date"] = parse_date(row.get("sent_at", ""), "jobs", failures)
        url = (
            row.get("inviterprofileurl")
            if row.get("direction") == "INCOMING"
            else row.get("inviteeprofileurl")
        )
        row["slug"] = profile_slug(url)
    return rows


def load_applications(archive_dir: Path, failures: list | None = None) -> list[dict]:
    rows = _read_csv(archive_dir / "Job Applications.csv")
    for row in rows:
        row["application_date_parsed"] = parse_date(
            row.get("application_date", ""), "jobs", failures
        )
    return rows


def load_saved_jobs(archive_dir: Path, failures: list | None = None) -> list[dict]:
    """Saved jobs, merging the split ``Saved Jobs_1.csv`` when the export includes it.

    NO PRODUCTION CALLER. Workstream 3 was dropped and workstream 4 reduced to Job
    Applications only, so nothing in this package reads saved jobs. Kept because it is
    tested and harmless, but its inputs are excluded from ``NEEDED_FILES`` and are not
    copied into the repo -- pass a directory holding them explicitly to use this.
    """
    rows = _read_csv(archive_dir / "Saved Jobs.csv") + _read_csv(
        archive_dir / "Saved Jobs_1.csv"
    )
    for row in rows:
        row["saved_date_parsed"] = parse_date(row.get("saved_date", ""), "jobs", failures)
    return rows


def manifest(archive_dir: Path, include_missing: bool = False) -> dict:
    """Map archive filename -> row count. Missing files are omitted unless asked for."""
    report: dict[str, int | None] = {}
    for name in ARCHIVE_FILES:
        path = archive_dir / name
        if not path.exists():
            if include_missing:
                report[name] = None
            continue
        report[name] = len(
            _read_csv(path, header_starts_with=PREAMBLE_HEADERS.get(name))
        )
    return report
