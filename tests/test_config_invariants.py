"""Invariants every user's search config must satisfy. Shipped in the generic export
and run against whichever configs exist (the owner's real config, and — in this repo —
the export's starter config)."""
import json
from pathlib import Path

import pytest

from scripts.scan import build_prompt

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATHS = [p for p in (
    REPO_ROOT / "config" / "search-config.json",
    REPO_ROOT / "export" / "overlay" / "config" / "search-config.json",
) if p.exists()]


def _load(path):
    return json.loads(Path(path).read_text())


def _profile():
    return {"basics": {"name": "Alex Rivera", "headline": "Analytics leader"},
            "summary": "15+ years analytics leadership.",
            "experience": [{"company": "Lumen Commerce", "title": "Senior Manager"}],
            "skills": ["SQL", "dbt", "Looker"]}


def test_there_is_at_least_one_config_to_check():
    assert CONFIG_PATHS


configs = pytest.mark.parametrize("config_path", CONFIG_PATHS, ids=lambda p: p.parent.name)


@configs
def test_shipped_config_has_no_robots_refused_linkedin_feeds(config_path):
    """LinkedIn's robots.txt names Anthropic's fetcher directly --
    `User-agent: Claude-SearchBot` / `Disallow: /jobs-guest/`, under a catch-all
    `User-agent: * / Disallow: /`. The server-side web_fetch honours that and
    returns `url_not_allowed`, so these feeds never produced a single job; the
    29 LinkedIn rows in the store came from the 4c0c903 history import, not a
    scan. Each one still burned a fetch from the budget on every run.
    """
    for feed in _load(config_path)["manual_feeds"]:
        assert "linkedin.com" not in feed["url"].lower(), (
            f"{feed.get('label')} is robots-refused and cannot yield jobs"
        )


@configs
def test_configured_feeds_are_on_vetted_hosts(config_path):
    """Every host here passed both gates: robots.txt allows the path for `*`,
    Claude-User, Claude-SearchBot and ClaudeBot, and the URL returns
    server-rendered listings rather than a JS shell. Adding a host without that
    vetting is how the LinkedIn feeds got in."""
    import urllib.parse
    vetted = {"builtin.com", "www.builtinnyc.com"}
    for feed in _load(config_path)["manual_feeds"]:
        host = urllib.parse.urlparse(feed["url"]).netloc.lower()
        assert host in vetted, f"{host} has not been robots- and fetch-vetted"


@configs
def test_build_prompt_carries_every_signal_keyword_family(config_path):
    """`FAVOR KEYWORDS:` rendered EMPTY on every run from 2026-07-30 to 2026-09-01.

    The retarget replaced the flat `keywords` list with `signal_keywords`
    {differentiation, core, shape}, but build_prompt still read `config["keywords"]`
    — a key that no longer existed — so `.get(..., [])` silently yielded "". Only
    `differentiation` was wired through separately. The other ~26 recall terms
    (Looker, BigQuery, ARR, churn, forecasting, player-coach, first analytics hire,
    0 to 1) never reached the model.
    """
    prompt = build_prompt(_load(config_path), _profile(), seen_ids=[])
    signals = _load(config_path)["signal_keywords"]
    for family in ("differentiation", "core", "shape"):
        for term in signals[family]:
            assert term in prompt, f"signal_keywords.{family} term {term!r} missing from prompt"


@configs
def test_build_prompt_never_emits_an_empty_keyword_line(config_path):
    """The failure mode was invisible because the label still rendered. Assert on
    the label carrying content, not merely on the label existing."""
    prompt = build_prompt(_load(config_path), _profile(), seen_ids=[])
    for line in prompt.splitlines():
        label, sep, rest = line.partition(":")
        if sep and label.isupper() and "KEYWORD" in label:
            assert rest.strip(), f"{label!r} renders with no content"


@configs
def test_signal_keyword_comment_keys_are_not_sent_as_keywords(config_path):
    """Every signal_keywords family carries a `_comment`. It is documentation for
    the human, not a search term."""
    from scripts.scan import signal_terms
    terms = signal_terms(_load(config_path))
    assert terms, "no signal terms extracted"
    assert not any(t.startswith("_") for t in terms)


@configs
def test_no_excluded_title_swallows_a_targeted_title(config_path):
    """EXCLUDE_TITLES held "Analytics Manager" and "Business Intelligence Manager"
    while `titles` targeted "Senior Manager, Analytics", "Senior Manager, Business
    Intelligence" and "Analytics Engineering Manager". The prompt ends with a HARD
    FILTER on EXCLUDE_TITLES, so any loose or semantic match killed three targets at
    triage — before a fetch was ever spent. Contiguous-substring matching does not
    catch this pair; token containment does, which is how a model reads it.
    """
    import re
    cfg = _load(config_path)
    def tokens(s):
        return set(re.sub(r"[^a-z0-9 ]", " ", s.lower()).split())
    for bad in cfg["exclude_titles"]:
        for target in cfg["titles"]:
            assert not tokens(bad) <= tokens(target), (
                f"exclude_titles {bad!r} swallows target title {target!r}"
            )


@configs
def test_config_validates_against_its_schema(config_path):
    from jsonschema import Draft202012Validator
    schema = json.loads((REPO_ROOT / "schema" / "search-config.schema.json").read_text())
    Draft202012Validator(schema).validate(_load(config_path))
