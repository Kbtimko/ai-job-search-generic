import csv
import inspect
import re
from datetime import date, timedelta
from pathlib import Path

import linkedin.inbound as inbound
import linkedin.warm_intros as warm_intros
from linkedin import ARCHIVE_OWNER
from linkedin.companies import MATCH_CONFIG, match_company, normalize
from linkedin.ingest import (
    load_connections,
    load_endorsements,
    load_invitations,
    load_messages,
    load_positions,
)
from linkedin.warm_intros import (
    OUTPUT_FILES,
    TIE_WEIGHTS,
    build_ties,
    run,
    seniority_bucket,
    split_by_recency,
    tie_score,
)

FIXTURES = Path(__file__).parent / "fixtures" / "linkedin"


TODAY = date(2026, 8, 19)


def _rows(path):
    """Every file this module writes leads with a ``#`` caveat; consumers skip it."""
    lines = path.read_text().splitlines(keepends=True)
    assert lines[0].startswith("#"), f"{path.name} must lead with a caveat"
    return [line for line in lines if not line.startswith("#")]


def _caveat_text(path):
    """The leading ``#`` block as one lowercase line, so assertions are not hostage
    to where the caveat happens to wrap."""
    lines = [line.lstrip("#").strip() for line in path.read_text().splitlines()
             if line.startswith("#")]
    return re.sub(r"\s+", " ", " ".join(lines)).lower()


def _tie(**over):
    base = {
        "former_colleague": False,
        "endorsed_me": False,
        "invite_incoming": False,
        "message_count": 0,
        "connected_on_date": date(2024, 1, 1),
        "connection_title": "Data Analyst",
    }
    base.update(over)
    return base


def test_seniority_buckets_from_title_string():
    assert seniority_bucket("Senior Data Analyst") == "ic"
    assert seniority_bucket("Engineering Manager") == "manager"
    assert seniority_bucket("Director of Analytics") == "director"
    assert seniority_bucket("VP of Engineering") == "vp_plus"
    assert seniority_bucket("Chief Data Officer") == "vp_plus"
    assert seniority_bucket("") == "unknown"


def test_prefixed_vp_titles_are_senior():
    """\\bvp\\b cannot match SVP/EVP/AVP, which left 23 clearly senior titles on the
    real archive bucketed below vp_plus."""
    assert seniority_bucket("SVP, Analytics") == "vp_plus"
    assert seniority_bucket("EVP Operations") == "vp_plus"
    assert seniority_bucket("AVP, Data Science") == "vp_plus"
    assert seniority_bucket("Senior Vice President, Marketing") == "vp_plus"


def test_business_function_partners_are_not_executives():
    """"Talent Partner"/"Business Partner" are recruiting and HR IC roles; the bare
    "partner" needle filed 11 of them as VP+ on the real archive."""
    assert seniority_bucket("Talent Partner") != "vp_plus"
    assert seniority_bucket("Business Partner") != "vp_plus"
    assert seniority_bucket("HR Business Partner") != "vp_plus"
    assert seniority_bucket("Client Partner, Media") != "vp_plus"
    assert seniority_bucket("Senior Talent Partner") != "vp_plus"
    assert seniority_bucket("Senior Talent Acquisition Partner") != "vp_plus"


def test_a_genuine_partner_is_still_an_executive():
    assert seniority_bucket("Partner") == "vp_plus"
    assert seniority_bucket("Partner at Sequoia Capital") == "vp_plus"
    assert seniority_bucket("Managing Partner") == "vp_plus"


def test_former_colleague_is_the_heaviest_single_term():
    others = ["endorsed_me", "invite_incoming"]
    assert all(TIE_WEIGHTS["former_colleague"] > TIE_WEIGHTS[k] for k in others)


def test_former_colleague_outranks_heavy_message_volume():
    colleague = tie_score(_tie(former_colleague=True), TODAY)
    chatty = tie_score(_tie(message_count=40), TODAY)
    assert colleague > chatty


def test_message_volume_is_log_scaled_and_capped():
    low = tie_score(_tie(message_count=1), TODAY)
    mid = tie_score(_tie(message_count=10), TODAY)
    high = tie_score(_tie(message_count=1000), TODAY)
    assert mid - low > high - mid, "growth must decelerate"
    assert high <= tie_score(_tie(message_count=10_000), TODAY)


def test_older_connections_score_higher_on_age():
    old = tie_score(_tie(connected_on_date=date(2011, 1, 1)), TODAY)
    new = tie_score(_tie(connected_on_date=date(2026, 1, 1)), TODAY)
    assert old > new


def test_endorsement_and_incoming_invite_add_score():
    base = tie_score(_tie(), TODAY)
    assert tie_score(_tie(endorsed_me=True), TODAY) > base
    assert tie_score(_tie(invite_incoming=True), TODAY) > base


def test_seniority_contributes_monotonically():
    ic = tie_score(_tie(connection_title="Data Analyst"), TODAY)
    director = tie_score(_tie(connection_title="Director of Analytics"), TODAY)
    assert director > ic


def test_dormancy_is_never_penalized():
    """Dormant ties are often the highest-value asks; recency must not subtract.

    The two ties differ ONLY in their recency fields, so any recency term inside
    tie_score -- penalty or bonus -- separates the scores and fails this.
    """
    recent = _tie(message_count=5, days_since_contact=10,
                  last_message_date=date(2026, 8, 9))
    dormant = _tie(message_count=5, days_since_contact=3000,
                   last_message_date=date(2018, 6, 2))
    assert recent != dormant, "the ties must actually differ on recency"
    assert tie_score(recent, TODAY) == tie_score(dormant, TODAY)


def test_a_stronger_dormant_tie_outranks_a_weaker_recent_one():
    """A former colleague last spoken to in 2018 must still beat a recent acquaintance
    by the full former-colleague weight -- dormancy erodes none of it."""
    recent = _tie(message_count=5, days_since_contact=10,
                  last_message_date=date(2026, 8, 9))
    dormant = _tie(message_count=5, days_since_contact=3000,
                   last_message_date=date(2018, 6, 2), former_colleague=True)
    assert tie_score(dormant, TODAY) > tie_score(recent, TODAY)
    margin = round(tie_score(dormant, TODAY) - tie_score(recent, TODAY), 2)
    assert margin == TIE_WEIGHTS["former_colleague"]


def test_missing_connection_date_does_not_crash():
    assert tie_score(_tie(connected_on_date=None), TODAY) >= 0


def _load_all():
    return {
        "connections": load_connections(FIXTURES),
        "messages": load_messages(FIXTURES),
        "positions": load_positions(FIXTURES),
        "endorsements": load_endorsements(FIXTURES),
        "invitations": load_invitations(FIXTURES),
    }


def test_build_ties_returns_one_row_per_connection():
    ties = build_ties(**_load_all(), today=TODAY)
    assert len(ties) == 5


def test_message_counts_are_joined_by_profile_slug():
    ties = {t["slug"]: t for t in build_ties(**_load_all(), today=TODAY)}
    # Ada sent one inbound message; the reply from Alex is not counted as hers.
    assert ties["adanolan"]["message_count"] == 1
    assert ties["boreyes"]["message_count"] == 1
    assert ties["eliprasad"]["message_count"] == 0


def test_last_message_date_and_days_since_contact_are_derived():
    ties = {t["slug"]: t for t in build_ties(**_load_all(), today=TODAY)}
    assert ties["adanolan"]["last_message_date"] == date(2026, 6, 1)
    assert ties["adanolan"]["days_since_contact"] == (TODAY - date(2026, 6, 1)).days
    assert ties["eliprasad"]["last_message_date"] is None
    assert ties["eliprasad"]["days_since_contact"] is None


def test_former_colleague_requires_overlapping_tenure_at_a_shared_company():
    ties = {t["slug"]: t for t in build_ties(**_load_all(), today=TODAY)}
    # Ada is at Northwind and connected Jun 2019, inside Alex's Mar 2018-Dec 2021
    # tenure window there -- but Dee connected in 2024, after Alex left.
    assert ties["adanolan"]["former_colleague"] is True
    assert ties["deeokafor"]["former_colleague"] is False


def test_former_colleague_is_false_for_unshared_company():
    ties = {t["slug"]: t for t in build_ties(**_load_all(), today=TODAY)}
    assert ties["cymarsh"]["former_colleague"] is False


def test_endorsement_and_invite_flags_are_joined_by_slug():
    ties = {t["slug"]: t for t in build_ties(**_load_all(), today=TODAY)}
    assert ties["adanolan"]["endorsed_me"] is True
    assert ties["cymarsh"]["endorsed_me"] is False
    assert ties["boreyes"]["invite_incoming"] is True
    assert ties["deeokafor"]["invite_incoming"] is False


def test_every_tie_carries_a_score_that_orders_against_the_others():
    """Every output file ranks on tie_score, so what matters is not its type but that
    it discriminates. Ada is a former colleague who endorsed Alex and has messaged
    him; Cy has none of that and must land below her."""
    ties = {t["slug"]: t for t in build_ties(**_load_all(), today=TODAY)}
    assert all(t["tie_score"] >= 0 for t in ties.values())
    assert ties["adanolan"]["tie_score"] > ties["cymarsh"]["tie_score"]
    assert len({t["tie_score"] for t in ties.values()}) > 1, \
        "a score that is the same for everyone ranks nothing"


def test_split_by_recency_uses_contact_recency():
    ties = build_ties(**_load_all(), today=TODAY)
    active, dormant, _uncontacted = split_by_recency(ties)
    active_slugs = {t["slug"] for t in active}
    dormant_slugs = {t["slug"] for t in dormant}
    assert "adanolan" in active_slugs       # contacted Jun 2026
    assert "boreyes" in dormant_slugs       # last contact 2019, had substance
    assert active_slugs.isdisjoint(dormant_slugs)


def test_never_contacted_ties_get_their_own_view():
    """No message history means no dormancy to describe -- but the tie is still a
    tie, and a former colleague with no LinkedIn messages is the best ask there is."""
    ties = build_ties(**_load_all(), today=TODAY)
    active, dormant, uncontacted = split_by_recency(ties)
    contacted = {t["slug"] for t in active} | {t["slug"] for t in dormant}
    assert "eliprasad" not in contacted, "no message history means no dormancy claim"
    assert "eliprasad" in {t["slug"] for t in uncontacted}


def test_the_three_views_partition_every_tie():
    ties = build_ties(**_load_all(), today=TODAY)
    views = split_by_recency(ties)
    placed = [t["slug"] for view in views for t in view]
    assert len(placed) == len(ties), "no tie may be dropped or duplicated"
    assert set(placed) == {t["slug"] for t in ties}
    for i, first in enumerate(views):
        for second in views[i + 1:]:
            assert {t["slug"] for t in first}.isdisjoint({t["slug"] for t in second})


def test_views_are_sorted_by_tie_score_descending():
    ties = build_ties(**_load_all(), today=TODAY)
    for view in split_by_recency(ties):
        scores = [t["tie_score"] for t in view]
        assert scores == sorted(scores, reverse=True)


def _jobs():
    return [
        {"id": "nw-dir-analytics", "company": "Northwind Traders",
         "title": "Director of Analytics", "status": "new"},
        {"id": "umbrella-1", "company": "Umbrella Biotech",
         "title": "Head of Data", "status": "new"},
    ]


def test_run_writes_all_five_output_files(tmp_path):
    result = run(FIXTURES, tmp_path, jobs=_jobs(), today=TODAY, me="Alex Rivera")
    for name in ("warm_intros_active.csv", "warm_intros_dormant.csv",
                 "warm_intros_uncontacted.csv", "network_companies.csv",
                 "match_review.csv"):
        assert (tmp_path / name).exists(), name
    assert result["coverage_zero_companies"] == ["Umbrella Biotech"]


def test_forward_view_pairs_jobs_with_connections_at_that_company(tmp_path):
    run(FIXTURES, tmp_path, jobs=_jobs(), today=TODAY, me="Alex Rivera")
    rows = list(csv.DictReader(_rows(tmp_path / "warm_intros_active.csv")))
    northwind = [r for r in rows if r["company"] == "Northwind Traders"]
    assert northwind, "Ada is at Northwind and was contacted recently"
    assert northwind[0]["job_id"] == "nw-dir-analytics"
    assert northwind[0]["role_title"] == "Director of Analytics"


def test_forward_view_carries_the_documented_columns(tmp_path):
    run(FIXTURES, tmp_path, jobs=_jobs(), today=TODAY, me="Alex Rivera")
    header = next(csv.reader(_rows(tmp_path / "warm_intros_active.csv")))
    for column in ("job_id", "company", "role_title", "connection_name",
                   "connection_title", "connected_on", "last_message_date",
                   "message_count", "days_since_contact", "former_colleague",
                   "endorsed_me", "seniority_bucket", "tie_score", "match_confidence"):
        assert column in header, column


def test_reverse_view_covers_companies_with_no_tracked_job(tmp_path):
    run(FIXTURES, tmp_path, jobs=_jobs(), today=TODAY, me="Alex Rivera")
    rows = list(csv.DictReader(_rows(tmp_path / "network_companies.csv")))
    companies = {r["company"] for r in rows}
    assert "Fabrikam LLC" in companies, "no job tracked there, but a tie exists"
    assert "Northwind Traders" in companies


def test_reverse_view_is_ranked_by_aggregate_tie_strength(tmp_path):
    run(FIXTURES, tmp_path, jobs=_jobs(), today=TODAY, me="Alex Rivera")
    rows = list(csv.DictReader(_rows(tmp_path / "network_companies.csv")))
    scores = [float(r["total_tie_score"]) for r in rows]
    assert scores == sorted(scores, reverse=True)


def test_run_reports_coverage_without_halting_on_it(tmp_path):
    # Only one of two job-store companies has any connection: 50% coverage.
    result = run(FIXTURES, tmp_path, jobs=_jobs(), today=TODAY, me="Alex Rivera")
    assert result["match_report"]["coverage"] < 0.7
    assert result["files_written"], "low coverage must not stop the run"


def test_coverage_is_job_companies_that_have_a_connection(tmp_path):
    """Coverage answers "can I get an intro to this employer?", so the denominator is
    the job store: 1 of 2 job companies (Northwind, not Umbrella) has a connection.

    The reverse reading -- network companies that have a tracked req -- is 1 of the 4
    distinct companies in the fixture network, i.e. 0.25, so a regression to it fails.
    """
    result = run(FIXTURES, tmp_path, jobs=_jobs(), today=TODAY, me="Alex Rivera")
    assert result["match_report"]["coverage"] == 0.5


def test_coverage_denominator_ignores_duplicate_job_companies(tmp_path):
    jobs = _jobs() + [{"id": "nw-2", "company": "Northwind Traders",
                       "title": "Analytics Lead", "status": "new"}]
    result = run(FIXTURES, tmp_path, jobs=jobs, today=TODAY, me="Alex Rivera")
    assert result["match_report"]["coverage"] == 0.5


def test_former_colleague_is_false_when_connected_before_the_tenure_started():
    """Connecting years before you joined a company does not make someone a former
    colleague there -- you had not worked together yet. Only a connection formed
    *during* the tenure is evidence you actually overlapped."""
    positions = [{
        "company_name": "Northwind Traders",
        "started_on_date": date(2018, 3, 1),
        "finished_on_date": date(2021, 12, 1),
    }]
    connections = [{
        "first_name": "Pre", "last_name": "Dates",
        "url": "https://www.linkedin.com/in/predates",
        "company": "Northwind Traders", "position": "Data Analyst",
        "connected_on_date": date(2010, 5, 4),
        "slug": "predates",
    }]
    ties = build_ties(connections=connections, messages=[], positions=positions,
                      endorsements=[], invitations=[], today=TODAY)
    assert ties[0]["former_colleague"] is False


def test_uncontacted_former_colleague_at_a_job_company_is_surfaced(tmp_path):
    """The heaviest weight in TIE_WEIGHTS must be able to reach the output. Eli is a
    former Initech colleague with zero messages: previously invisible everywhere."""
    jobs = _jobs() + [{"id": "initech-1", "company": "Initech",
                       "title": "Staff Data Engineer", "status": "new"}]
    result = run(FIXTURES, tmp_path, jobs=jobs, today=TODAY, me="Alex Rivera")
    assert "warm_intros_uncontacted.csv" in result["files_written"]
    rows = list(csv.DictReader(_rows(tmp_path / "warm_intros_uncontacted.csv")))
    initech = [r for r in rows if r["company"] == "Initech"]
    assert initech, "a former colleague with no messages must still be surfaced"
    assert initech[0]["former_colleague"] == "True"
    assert initech[0]["message_count"] == "0"
    assert initech[0]["job_id"] == "initech-1"


def test_the_three_forward_views_are_disjoint_and_cover_every_matched_tie(tmp_path):
    jobs = _jobs() + [{"id": "initech-1", "company": "Initech",
                       "title": "Staff Data Engineer", "status": "new"}]
    run(FIXTURES, tmp_path, jobs=jobs, today=TODAY, me="Alex Rivera")
    views = {}
    for name in ("active", "dormant", "uncontacted"):
        path = tmp_path / f"warm_intros_{name}.csv"
        views[name] = {(r["job_id"], r["connection_name"])
                       for r in csv.DictReader(_rows(path))}
    names = list(views)
    for i, first in enumerate(names):
        for second in names[i + 1:]:
            assert views[first].isdisjoint(views[second]), f"{first} vs {second}"

    ties = {t["connection_name"]: t for t in build_ties(**_load_all(), today=TODAY)}
    expected = {(job["id"], tie["connection_name"])
                for job in jobs for tie in ties.values()
                if tie["company_normalized"] == normalize(job["company"])}
    assert set().union(*views.values()) == expected


def _tie_last_contacted(days_ago: int) -> list[dict]:
    """One connection whose only inbound message landed ``days_ago`` days back."""
    connections = [{
        "first_name": "Gap", "last_name": "Case",
        "url": "https://www.linkedin.com/in/gapcase", "company": "Contoso, Inc.",
        "position": "Data Analyst", "connected_on_date": date(2019, 1, 1),
        "slug": "gapcase",
    }]
    messages = [{"from": "Gap Case", "sender_slug": "gapcase",
                 "date_parsed": TODAY - timedelta(days=days_ago)}]
    return build_ties(connections=connections, messages=messages, positions=[],
                      endorsements=[], invitations=[], today=TODAY)


def test_a_tie_contacted_500_days_ago_is_not_silently_dropped():
    """366-729 days used to fall between ACTIVE_DAYS and DORMANT_DAYS and vanish from
    every output file with no record. Anything past active is dormant."""
    ties = _tie_last_contacted(500)
    active, dormant, uncontacted = split_by_recency(ties)
    assert [t["slug"] for t in dormant] == ["gapcase"]
    assert active == [] and uncontacted == []


def test_every_contacted_tie_lands_in_exactly_one_view():
    for days in (0, 1, 364, 365, 366, 500, 729, 730, 4000):
        ties = _tie_last_contacted(days)
        placed = [t for view in split_by_recency(ties) for t in view]
        assert len(placed) == 1, f"{days} days since contact was dropped"


def test_split_by_recency_takes_only_ties():
    """The split reads days_since_contact, frozen by build_ties at ingest time. A
    ``today`` parameter would imply it is recomputed against the date passed here."""
    assert list(inspect.signature(split_by_recency).parameters) == ["ties"]
    ties = _tie_last_contacted(10)
    active, _dormant, _uncontacted = split_by_recency(ties)
    assert [t["slug"] for t in active] == ["gapcase"]


def test_forward_rows_carry_a_clickable_profile_url(tmp_path):
    """connection_name alone is not actionable -- the reviewer needs the profile."""
    run(FIXTURES, tmp_path, jobs=_jobs(), today=TODAY, me="Alex Rivera")
    rows = list(csv.DictReader(_rows(tmp_path / "warm_intros_active.csv")))
    assert rows, "fixture must produce at least one forward row"
    for row in rows:
        assert row["profile_url"].startswith("https://www.linkedin.com/in/")
    ada = [r for r in rows if r["connection_name"] == "Ada Nolan"][0]
    assert ada["profile_url"] == "https://www.linkedin.com/in/adanolan"


def _jobs_with_duplicate_companies():
    """Two reqs at the same unmatched company, and two at the same review-band one."""
    return _jobs() + [
        {"id": "umbrella-2", "company": "Umbrella Biotech",
         "title": "Analytics Lead", "status": "new"},
        {"id": "nwt-1", "company": "Northwind Trading",
         "title": "Data Lead", "status": "new"},
        {"id": "nwt-2", "company": "Northwind Trading",
         "title": "Analytics Manager", "status": "new"},
    ]


def test_coverage_zero_companies_are_deduplicated(tmp_path):
    result = run(FIXTURES, tmp_path, jobs=_jobs_with_duplicate_companies(), today=TODAY, me="Alex Rivera")
    zeros = result["coverage_zero_companies"]
    assert zeros == list(dict.fromkeys(zeros)), "one company, one entry"
    assert zeros == ["Umbrella Biotech", "Northwind Trading"], "order is preserved"


def test_match_review_rows_are_deduplicated(tmp_path):
    run(FIXTURES, tmp_path, jobs=_jobs_with_duplicate_companies(), today=TODAY, me="Alex Rivera")
    rows = [tuple(r.values())
            for r in csv.DictReader(_rows(tmp_path / "match_review.csv"))]
    assert rows, "the fixture must produce at least one review-band pair"
    assert len(rows) == len(set(rows)), "one pair, one row for a human to confirm"


def _review_band_job():
    """A job company that matches a connection's company in the REVIEW band only.

    "Northwind Trading" vs the fixture's "Northwind Traders" scores 82.35, which sits
    inside [MATCH_CONFIG["review_floor"]=80, MATCH_CONFIG["auto_accept"]=92) -- an
    unconfirmed suggestion, never an accepted match.
    """
    return [{"id": "nwt-review", "company": "Northwind Trading",
             "title": "Data Lead", "status": "new"}]


def test_review_band_company_never_reaches_a_forward_row(tmp_path):
    """companies.Match guarantees a caller reading only ``matched`` cannot consume an
    unconfirmed match. This holds _forward_rows to it across the module boundary:
    falling back to ``result.candidate`` would attach Ada and Dee (Northwind Traders)
    to a req at "Northwind Trading" without a human ever confirming they are the same
    company."""
    jobs = _review_band_job()
    result = match_company(jobs[0]["company"], ["Northwind Traders"], {})
    assert result.band == "review", "the fixture must actually land in the review band"
    assert MATCH_CONFIG["review_floor"] <= result.score < MATCH_CONFIG["auto_accept"]

    run(FIXTURES, tmp_path, jobs=jobs, today=TODAY, me="Alex Rivera")
    for name in ("active", "dormant", "uncontacted"):
        rows = list(csv.DictReader(_rows(tmp_path / f"warm_intros_{name}.csv")))
        assert rows == [], f"warm_intros_{name}.csv consumed an unconfirmed match"


def test_review_band_company_is_routed_to_the_human_instead(tmp_path):
    """Suppressing the row is only half the contract -- the pair must still surface
    somewhere a human can confirm it, or the suggestion is silently lost."""
    run(FIXTURES, tmp_path, jobs=_review_band_job(), today=TODAY, me="Alex Rivera")
    rows = list(csv.DictReader(_rows(tmp_path / "match_review.csv")))
    assert [(r["source"], r["candidate"]) for r in rows] == [
        ("Northwind Trading", "Northwind Traders")
    ]


def test_every_output_file_leads_with_a_caveat(tmp_path):
    """These files rank real people by a score built entirely from proxies. A reader
    opening the CSV sees the header row, not the module docstring, so the limits have
    to be in the file. All five previously led with a bare header."""
    run(FIXTURES, tmp_path, jobs=_jobs(), today=TODAY, me="Alex Rivera")
    for name in OUTPUT_FILES:
        text = (tmp_path / name).read_text()
        assert text.startswith("#"), f"{name} ships uncaveated"


def test_forward_caveat_names_the_specific_weak_proxies(tmp_path):
    """Naming them matters: a vague "use judgement" header does not tell a reader that
    the strongest term in the ranking is an inference, or that the employer may be
    years stale."""
    run(FIXTURES, tmp_path, jobs=_jobs(), today=TODAY, me="Alex Rivera")
    for name in ("warm_intros_active.csv", "warm_intros_dormant.csv",
                 "warm_intros_uncontacted.csv"):
        text = _caveat_text(tmp_path / name)
        assert "seniority_bucket is a regular expression" in text
        assert "free-text job titles" in text
        assert "it feeds tie_score" in text, "the ranking inherits the weakness"
        assert "former_colleague does not mean you worked together" in text
        assert "heaviest weight" in text
        assert "self-reported current employer" in text
        assert "may have left years ago" in text
        assert "measures spelling, not identity" in text
        assert "top n rows are not the top n people" in text, "one row per job x person"


def test_reverse_caveat_covers_its_own_columns_not_the_forward_ones(tmp_path):
    """network_companies.csv has no match_confidence and no job x connection fan-out,
    so pasting the forward caveat here would warn about columns that are not present."""
    run(FIXTURES, tmp_path, jobs=_jobs(), today=TODAY, me="Alex Rivera")
    text = _caveat_text(tmp_path / "network_companies.csv")
    assert "self-reported current employer" in text
    assert "connection_count includes anyone who has since left" in text
    assert "former_colleagues counts an inference" in text
    assert "total_tie_score" in text
    assert "match_confidence" not in text, "that column is not in this file"


def test_match_review_caveat_says_nothing_here_has_been_applied(tmp_path):
    """The whole point of the file is that these matches were withheld. A reader who
    assumes they were used would draw the opposite conclusion from every row."""
    run(FIXTURES, tmp_path, jobs=_jobs_with_duplicate_companies(), today=TODAY, me="Alex Rivera")
    text = _caveat_text(tmp_path / "match_review.csv")
    assert "nothing here has been applied" in text
    assert "string similarity only" in text
    assert "aliases.json" in text, "must say what to do with a confirmed pair"


def test_the_archive_owner_is_defined_once_for_every_site_that_needs_it():
    """"Alex Rivera" was a default arg in three places across two modules, and
    warm_intros.run had no way to override it at all. If the export's display name
    differs even slightly, the inbound/outbound split, message_count and reply
    direction all mis-attribute -- and correcting it meant finding all three."""
    sites = (warm_intros.build_ties, warm_intros.run,
             inbound.thread_reply_stats, inbound.run)
    for site in sites:
        default = inspect.signature(site).parameters["me"].default
        assert default == ARCHIVE_OWNER, f"{site.__qualname__} defaults elsewhere"
    for module in (warm_intros, inbound):
        assert ARCHIVE_OWNER not in inspect.getsource(module), \
            f"{module.__name__} still hardcodes the name"


def test_warm_intros_run_honours_an_overridden_owner(tmp_path):
    """The override has to reach build_ties, not just sit in run's signature. Ada's
    only message is one she sent; treat her as the archive owner and it stops counting
    as contact from her, moving her out of the active view entirely."""
    run(FIXTURES, tmp_path, jobs=_jobs(), today=TODAY, me="Ada Nolan")
    active = {r["connection_name"]
              for r in csv.DictReader(_rows(tmp_path / "warm_intros_active.csv"))}
    uncontacted = {r["connection_name"]
                   for r in csv.DictReader(_rows(tmp_path / "warm_intros_uncontacted.csv"))}
    assert "Ada Nolan" not in active
    assert "Ada Nolan" in uncontacted


def test_the_default_owner_still_treats_adas_message_as_contact(tmp_path):
    """The control for the test above -- otherwise it would pass on a run() that
    ignored ``me`` and produced no rows at all."""
    run(FIXTURES, tmp_path, jobs=_jobs(), today=TODAY, me="Alex Rivera")
    active = {r["connection_name"]
              for r in csv.DictReader(_rows(tmp_path / "warm_intros_active.csv"))}
    assert "Ada Nolan" in active
