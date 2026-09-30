"""Workstream 1 -- rank first-degree connections by how askable they are.

Two directions are produced. Forward (job -> people) covers job-store companies.
Reverse (people -> companies) ranks every company in the network, surfacing places
with a foothold and no tracked req.

Recency is deliberately absent from tie_score. Dormant ties are often the highest
value asks, so recency splits the output into three ranked views (active, dormant,
uncontacted) rather than subtracting from one blended ranking that would bury them.
"""

from __future__ import annotations

import math
import re
from datetime import date
from pathlib import Path

from linkedin import ARCHIVE_OWNER

# Every weight lives here. No magic numbers inline.
TIE_WEIGHTS = {
    "former_colleague": 40.0,   # overlapping tenure -- the strongest available signal
    "endorsed_me": 12.0,        # publicly vouched for a skill
    "invite_incoming": 6.0,     # they reached out to you, not the reverse
    "message_volume": 10.0,     # multiplied by a log-scaled, capped factor
    "connection_age": 1.0,      # per year, capped
    "seniority": 1.0,           # multiplied by the bucket value below
}

MESSAGE_VOLUME_CAP = 50    # messages beyond this add nothing
CONNECTION_AGE_CAP = 15    # years beyond this add nothing

SENIORITY_VALUES = {"unknown": 0.0, "ic": 2.0, "manager": 5.0, "director": 8.0, "vp_plus": 10.0}

# Checked in order; the first hit wins, so the most senior patterns come first.
# "partner" is deliberately absent here -- it is handled below, because it only
# implies seniority in its bare form.
_SENIORITY_PATTERNS = [
    ("vp_plus", ("chief", "cto", "cdo", "ceo", "coo", "cfo", "vp", "svp", "evp", "avp",
                 "vice president", "president", "founder", "head of")),
    ("director", ("director",)),
    ("manager", ("manager", "lead", "supervisor")),
    ("ic", ("analyst", "engineer", "scientist", "designer", "developer",
            "consultant", "specialist", "associate")),
]

# Needles match on word boundaries, never as bare substrings: "director" contains
# "cto", so substring matching would file every director under VP+.
_SENIORITY_MATCHERS = [
    (bucket, re.compile(r"\b(?:%s)\b" % "|".join(re.escape(n) for n in needles)))
    for bucket, needles in _SENIORITY_PATTERNS
]


# "Partner" alone is an executive at a firm, but "<business function> Partner" is a
# recruiting, HR or client-service IC role -- the bare needle put 11 of those in
# vp_plus on the real archive. Qualified forms are excluded; "Managing Partner" is not.
_PARTNER = re.compile(r"\bpartners?\b")
# One optional word may sit between the qualifier and the noun, so "Talent
# Acquisition Partner" is caught alongside "Talent Partner".
_QUALIFIED_PARTNER = re.compile(
    r"\b(?:talent|business|client|customer|people|hr|human resources|recruiting|"
    r"recruitment|delivery|channel|solution|solutions|account|sales|marketing|"
    r"finance|success|alliance|implementation|learning|technology|engagement)"
    r"\s+(?:\w+\s+)?partners?\b"
)


def seniority_bucket(title: str | None) -> str:
    """Bucket a title string into IC / manager / director / VP+.

    A weak proxy: it reads title strings, which vary wildly between companies, and
    it feeds ``tie_score``, so the ranking inherits the weakness. Both facts are
    stated in the caveat header of every file this module writes -- see ``CAVEATS``.
    """
    text = (title or "").strip().lower()
    if not text:
        return "unknown"
    if _PARTNER.search(text) and not _QUALIFIED_PARTNER.search(text):
        return "vp_plus"
    for bucket, matcher in _SENIORITY_MATCHERS:
        if matcher.search(text):
            return bucket
    return "unknown"


def tie_score(tie: dict, today: date) -> float:
    """Additive score for how askable a connection is. Higher is better."""
    score = 0.0

    if tie.get("former_colleague"):
        score += TIE_WEIGHTS["former_colleague"]
    if tie.get("endorsed_me"):
        score += TIE_WEIGHTS["endorsed_me"]
    if tie.get("invite_incoming"):
        score += TIE_WEIGHTS["invite_incoming"]

    count = min(tie.get("message_count") or 0, MESSAGE_VOLUME_CAP)
    score += TIE_WEIGHTS["message_volume"] * math.log1p(count)

    connected = tie.get("connected_on_date")
    if connected:
        years = min((today - connected).days / 365.25, CONNECTION_AGE_CAP)
        score += TIE_WEIGHTS["connection_age"] * max(years, 0.0)

    bucket = seniority_bucket(tie.get("connection_title"))
    score += TIE_WEIGHTS["seniority"] * SENIORITY_VALUES[bucket]

    return round(score, 2)


ACTIVE_DAYS = 365       # contact within a year -> "active"; older -> "dormant"


def _own_tenures(positions: list[dict]) -> list[tuple[str, date, date]]:
    """Own employment windows as (normalized company, start, end).

    A blank ``Finished On`` means the position is current, so the window is left
    open by using ``date.max`` as the end.
    """
    from linkedin.companies import normalize

    tenures = []
    for row in positions:
        start = row.get("started_on_date")
        if not start:
            continue
        end = row.get("finished_on_date") or date.max
        tenures.append((normalize(row.get("company_name")), start, end))
    return tenures


def build_ties(connections: list[dict], messages: list[dict], positions: list[dict],
               endorsements: list[dict], invitations: list[dict], today: date,
               me: str = ARCHIVE_OWNER) -> list[dict]:
    """Assemble one scored tie per connection, joining every source by profile slug."""
    from linkedin.companies import normalize

    inbound = [m for m in messages if (m.get("from") or "").strip() != me]
    counts: dict[str, int] = {}
    last_seen: dict[str, date] = {}
    for message in inbound:
        slug = message.get("sender_slug")
        if not slug:
            continue
        counts[slug] = counts.get(slug, 0) + 1
        when = message.get("date_parsed")
        if when and (slug not in last_seen or when > last_seen[slug]):
            last_seen[slug] = when

    endorsed = {e["slug"] for e in endorsements if e.get("slug")}
    invited_me = {
        i["slug"] for i in invitations
        if i.get("direction") == "INCOMING" and i.get("slug")
    }
    tenures = _own_tenures(positions)

    ties = []
    for connection in connections:
        slug = connection.get("slug")
        connected = connection.get("connected_on_date")
        company = normalize(connection.get("company"))
        # A former colleague is currently at a company the user worked at AND was
        # connected *during* that tenure: connecting while you both worked there is
        # what makes it likely you actually worked together. A bare shared-employer
        # test would wrongly include people met only after the user left (Dee), and
        # dropping the start bound would wrongly include people connected years
        # before the user ever joined.
        former_colleague = bool(
            connected
            and any(c == company and start <= connected <= end for c, start, end in tenures)
        )
        last = last_seen.get(slug)
        tie = {
            "slug": slug,
            "connection_name": f"{connection.get('first_name', '')} {connection.get('last_name', '')}".strip(),
            "connection_title": connection.get("position", ""),
            "company": connection.get("company", ""),
            "company_normalized": company,
            "connected_on_date": connected,
            "message_count": counts.get(slug, 0),
            "last_message_date": last,
            "days_since_contact": (today - last).days if last else None,
            "former_colleague": former_colleague,
            "endorsed_me": slug in endorsed,
            "invite_incoming": slug in invited_me,
        }
        tie["seniority_bucket"] = seniority_bucket(tie["connection_title"])
        tie["tie_score"] = tie_score(tie, today)
        ties.append(tie)
    return ties


def split_by_recency(ties: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Split into three ranked views instead of one blended ranking.

    The split is a partition: every tie lands in exactly one view, so nothing is
    dropped between the bands. It reads the ``days_since_contact`` frozen by
    ``build_ties``; there is no ``today`` to pass. Ties with no message history get their own view rather than being dropped --
    there is no dormancy to describe, but a former colleague you never messaged on
    LinkedIn is still one of the strongest asks available.
    """
    active, dormant, uncontacted = [], [], []
    for tie in ties:
        days = tie.get("days_since_contact")
        if days is None:
            uncontacted.append(tie)
        elif days <= ACTIVE_DAYS:
            active.append(tie)
        else:
            dormant.append(tie)
    return tuple(
        sorted(view, key=lambda t: t["tie_score"], reverse=True)
        for view in (active, dormant, uncontacted)
    )


FORWARD_COLUMNS = [
    "job_id", "company", "role_title", "connection_name", "profile_url",
    "connection_title",
    "connected_on", "last_message_date", "message_count", "days_since_contact",
    "former_colleague", "endorsed_me", "invite_incoming", "seniority_bucket",
    "tie_score", "match_confidence",
]

REVERSE_COLUMNS = [
    "company", "connection_count", "former_colleagues", "top_tie_score",
    "total_tie_score",
]

REVIEW_COLUMNS = ["source", "candidate", "score", "band"]

OUTPUT_FILES = [
    "warm_intros_active.csv", "warm_intros_dormant.csv",
    "warm_intros_uncontacted.csv", "network_companies.csv", "match_review.csv",
]

# Every number in these files is a proxy for something the archive does not record.
# The warning ships inside the file, because that is where it gets read.
FORWARD_CAVEAT = (
    "# READ THIS BEFORE ACTING ON A ROW. Every column below is a guess from an\n"
    "# export, not a fact that was verified.\n"
    "#\n"
    "# seniority_bucket is a regular expression run over free-text job titles.\n"
    "# Titles mean different things at different companies, so the bucket is often\n"
    "# wrong -- and it feeds tie_score, so the ranking is wrong in the same places.\n"
    "#\n"
    "# former_colleague does NOT mean you worked together. It means LinkedIn lists\n"
    "# this person at a company you worked at, and you connected during your time\n"
    "# there. That is an inference from two company strings and a date, and it\n"
    "# carries the heaviest weight in tie_score, so when it is wrong it lifts the\n"
    "# row a long way up this file.\n"
    "#\n"
    "# company is LinkedIn's self-reported CURRENT employer, updated whenever the\n"
    "# person last bothered. Someone listed here may have left years ago, so\n"
    "# \"connections at this company\" can be badly out of date.\n"
    "#\n"
    "# match_confidence is a fuzzy string score between the job's company name and\n"
    "# theirs. It measures spelling, not identity.\n"
    "#\n"
    "# One row per job x connection, so the same person repeats once per open req at\n"
    "# their company. The top N rows are NOT the top N people to ask.\n"
)

REVERSE_CAVEAT = (
    "# Company totals built from the same proxies as the warm-intro files, so they\n"
    "# inherit the same weaknesses.\n"
    "#\n"
    "# company is LinkedIn's self-reported CURRENT employer, so connection_count\n"
    "# includes anyone who has since left and never updated their profile.\n"
    "#\n"
    "# former_colleagues counts an inference, not confirmed co-employment: LinkedIn\n"
    "# lists them at a company you worked at and you connected during your time\n"
    "# there.\n"
    "#\n"
    "# tie_score includes a seniority bucket read by regular expression from\n"
    "# free-text titles, so total_tie_score and top_tie_score inherit every title\n"
    "# the regex read wrong. Treat the ranking as a rough hint about where to look\n"
    "# first, not a measurement.\n"
)

REVIEW_CAVEAT = (
    "# Fuzzy company-name matches that scored too low to accept and too high to\n"
    "# throw away. NOTHING HERE HAS BEEN APPLIED -- these pairs were deliberately\n"
    "# kept out of the warm-intro files until a human confirms them.\n"
    "#\n"
    "# score is string similarity only. \"Northwind Traders\" and \"Northwind\n"
    "# Trading\" score the same whether or not they are one company. Confirm each\n"
    "# pair yourself, then record the confirmed ones in linkedin/aliases.json.\n"
)

# Keyed by output filename so every entry in OUTPUT_FILES demonstrably has one.
CAVEATS = {
    "warm_intros_active.csv": FORWARD_CAVEAT,
    "warm_intros_dormant.csv": FORWARD_CAVEAT,
    "warm_intros_uncontacted.csv": FORWARD_CAVEAT,
    "network_companies.csv": REVERSE_CAVEAT,
    "match_review.csv": REVIEW_CAVEAT,
}


def _profile_url(slug: str | None) -> str:
    """Link to the profile so a row can be acted on without a name search."""
    return f"https://www.linkedin.com/in/{slug}" if slug else ""


def _forward_rows(view: list[dict], jobs: list[dict], aliases: dict) -> list[dict]:
    """Pair each job with connections at the matched company.

    Only ``result.matched`` is read. A review-band result leaves it None and parks
    the suggestion in ``candidate``, which is never consumed here -- it goes to
    match_review.csv for a human instead.
    """
    from linkedin.companies import match_company

    companies = sorted({t["company"] for t in view if t["company"]})
    rows = []
    for job in jobs:
        result = match_company(job.get("company", ""), companies, aliases)
        if not result.matched:
            continue
        for tie in view:
            if tie["company"] != result.matched:
                continue
            rows.append({
                **tie,
                "job_id": job.get("id", ""),
                "company": job.get("company", ""),
                "role_title": job.get("title", ""),
                "connected_on": tie["connected_on_date"],
                "profile_url": _profile_url(tie.get("slug")),
                "match_confidence": round(result.score, 1),
            })
    return sorted(rows, key=lambda r: r["tie_score"], reverse=True)


def run(archive_dir: Path, output_dir: Path, jobs: list[dict], today: date,
        aliases: dict | None = None, me: str = ARCHIVE_OWNER) -> dict:
    """Run workstream 1 and write its five output files.

    Coverage is reported, never enforced: on real data only a small share of
    job-store companies have any connection at all, which is a fact about the
    network rather than a defect, so a low value must not stop the run.
    """
    from linkedin.companies import build_match_report, load_aliases
    from linkedin.output import write_csv
    from linkedin.ingest import (
        load_connections, load_endorsements, load_invitations, load_messages,
        load_positions,
    )

    aliases = load_aliases() if aliases is None else aliases
    output_dir = Path(output_dir)

    ties = build_ties(
        connections=load_connections(archive_dir),
        messages=load_messages(archive_dir),
        positions=load_positions(archive_dir),
        endorsements=load_endorsements(archive_dir),
        invitations=load_invitations(archive_dir),
        today=today,
        me=me,
    )
    active, dormant, uncontacted = split_by_recency(ties)

    for name, view in (("active", active), ("dormant", dormant),
                       ("uncontacted", uncontacted)):
        write_csv(output_dir / f"warm_intros_{name}.csv", FORWARD_COLUMNS,
                  _forward_rows(view, jobs, aliases),
                  CAVEATS[f"warm_intros_{name}.csv"])

    # Reverse view: every company in the network, ranked by aggregate tie strength.
    by_company: dict[str, dict] = {}
    for tie in ties:
        if not tie["company"]:
            continue
        entry = by_company.setdefault(
            tie["company"],
            {"company": tie["company"], "connection_count": 0, "total_tie_score": 0.0,
             "top_tie_score": 0.0, "former_colleagues": 0},
        )
        entry["connection_count"] += 1
        entry["total_tie_score"] = round(entry["total_tie_score"] + tie["tie_score"], 2)
        entry["top_tie_score"] = max(entry["top_tie_score"], tie["tie_score"])
        entry["former_colleagues"] += int(tie["former_colleague"])
    reverse = sorted(by_company.values(), key=lambda r: r["total_tie_score"], reverse=True)
    write_csv(output_dir / "network_companies.csv", REVERSE_COLUMNS, reverse,
              CAVEATS["network_companies.csv"])

    # Match report: precision may gate, coverage never does.
    tie_companies = sorted({t["company"] for t in ties if t["company"]})
    job_companies = [j.get("company", "") for j in jobs]
    report = build_match_report(job_companies, tie_companies, aliases)
    # Two reqs at the same company are one decision for the human, not two.
    review_pairs = dict.fromkeys(
        (m.source, m.candidate, round(m.score, 1), m.band)
        for m in report["matches"] if m.band == "review"
    )
    write_csv(output_dir / "match_review.csv", REVIEW_COLUMNS,
              [dict(zip(REVIEW_COLUMNS, pair)) for pair in review_pairs],
              CAVEATS["match_review.csv"])

    # build_match_report reports coverage as the share of *targets* matched. With
    # jobs as sources that reads "network companies that have a tracked req" -- the
    # inverse of the question this feature answers. Report the share of job-store
    # companies with at least one connection instead, and leave precision measured
    # over the job -> network direction, which is the direction _forward_rows joins.
    matched = {m.source for m in report["matches"] if m.matched}
    job_company_set = {c for c in job_companies if c}
    match_report = {k: v for k, v in report.items() if k != "matches"}
    match_report["coverage"] = (
        len(matched & job_company_set) / len(job_company_set) if job_company_set else 0.0
    )
    return {
        "match_report": match_report,
        "coverage_zero_companies": [
            c for c in dict.fromkeys(job_companies) if c and c not in matched
        ],
        "files_written": list(OUTPUT_FILES),
    }
