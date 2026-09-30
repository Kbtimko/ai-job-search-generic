"""Warm paths for the tracker: who in the network is at each job's company.

Computed live from the gitignored archive so every job is covered, including ones
the scan found after the last ``linkedin.cli warm-intros`` run. Reuses the CLI's tie
building and company matching, so the tracker and the CSVs cannot disagree on who
counts. The result carries real names: it is for local rendering only, and callers
must not compute it where the output could leave this machine.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from linkedin.companies import load_aliases, match_company
from linkedin.ingest import (
    NEEDED_FILES, load_connections, load_endorsements, load_invitations,
    load_messages, load_positions,
)
from linkedin.warm_intros import _profile_url, build_ties, split_by_recency

log = logging.getLogger(__name__)

TOP_N = 3
_RECENCY_LABELS = ("active", "dormant", "never messaged")

# Holds only the latest (archive_dir, archive mtimes, today) entry; see ``_ties_by_company``.
_CACHE: dict = {}


def _build_index(archive_dir: Path, today: date) -> dict[str, list[dict]]:
    """Every tie, tagged with its recency view, grouped by raw company, best first."""
    ties = build_ties(
        connections=load_connections(archive_dir),
        messages=load_messages(archive_dir),
        positions=load_positions(archive_dir),
        endorsements=load_endorsements(archive_dir),
        invitations=load_invitations(archive_dir),
        today=today,
    )
    by_company: dict[str, list[dict]] = {}
    for label, view in zip(_RECENCY_LABELS, split_by_recency(ties)):
        for tie in view:
            if not tie["company"]:
                continue
            by_company.setdefault(tie["company"], []).append({
                "name": tie["connection_name"],
                "title": tie["connection_title"],
                "profile_url": _profile_url(tie.get("slug")),
                "recency": label,
                "former_colleague": tie["former_colleague"],
                "tie_score": tie["tie_score"],
            })
    for entries in by_company.values():
        entries.sort(key=lambda t: t["tie_score"], reverse=True)
    return by_company


def _ties_by_company(archive_dir: Path, today: date) -> dict[str, list[dict]]:
    """Cached ``_build_index``. A fresh export copied in bumps an mtime and is picked
    up without a restart; ``today`` is in the key because tie_score and the recency
    split both depend on it. Only the latest entry is kept."""
    stamps = tuple(
        (name, (archive_dir / name).stat().st_mtime_ns)
        for name in NEEDED_FILES if (archive_dir / name).exists()
    )
    key = (str(archive_dir), stamps, today)
    cached = _CACHE.get(key)
    if cached is None:
        cached = _build_index(archive_dir, today)
        _CACHE.clear()
        _CACHE[key] = cached
    return cached


def warm_paths(jobs: list[dict], archive_dir: Path, today: date,
               aliases_path: Path | None = None) -> dict[str, dict]:
    """{job_id: {"count", "top", "archive_date"}} for jobs at a matched company.

    Returns {} when the archive is absent or unreadable; never raises into a page.
    """
    archive_dir = Path(archive_dir)
    connections_csv = archive_dir / "Connections.csv"
    if not connections_csv.exists():
        return {}
    try:
        by_company = _ties_by_company(archive_dir, today)
        aliases = load_aliases(aliases_path)
        archive_date = date.fromtimestamp(connections_csv.stat().st_mtime)
        companies = sorted(by_company)
        matched_by_source: dict[str, str | None] = {}
        result: dict[str, dict] = {}
        for job in jobs:
            job_id, source = job.get("id"), (job.get("company") or "").strip()
            if not job_id or not source:
                continue
            if source not in matched_by_source:
                matched_by_source[source] = match_company(source, companies, aliases).matched
            matched = matched_by_source[source]
            if not matched or matched not in by_company:
                continue
            entries = by_company[matched]
            result[job_id] = {"count": len(entries), "top": entries[:TOP_N],
                              "archive_date": archive_date}
        return result
    except Exception as exc:  # the page must render without warm paths
        log.warning("warm paths unavailable: %s", type(exc).__name__)
        return {}
