import json
import re
from pathlib import Path

from scripts.scan import build_prompt


def _config():
    return {
        "titles": ["Director of Analytics", "Analytics Manager"],
        "locations": [{"key": "remote", "label": "Remote", "match": "US-remote", "score": 8},
                      {"key": "denver", "label": "Denver", "match": "Denver metro", "score": 10}],
        "keywords": ["SQL", "dbt"],
        "dealbreakers": ["unpaid", "internship"],
        "salary_floor": 200000,
        "manual_feeds": [
            {"label": "LinkedIn — Remote", "location_type": "remote",
             "url": "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=Director%20Analytics"}
        ],
    }


def _profile():
    return {"basics": {"name": "Alex Rivera", "headline": "Analytics leader"},
            "summary": "15+ years analytics leadership.",
            "experience": [{"company": "Lumen Commerce", "title": "Senior Manager"}],
            "skills": ["SQL", "dbt", "Looker"]}


def test_build_prompt_includes_titles_locations_feeds_and_seen_ids():
    prompt = build_prompt(_config(), _profile(), seen_ids=["acme-director-2026-06"])
    assert "Director of Analytics" in prompt
    assert "Denver" in prompt
    assert "linkedin.com/jobs-guest" in prompt
    assert "unpaid" in prompt
    assert "acme-director-2026-06" in prompt
    assert "JSON" in prompt or "json" in prompt
    assert "Alex Rivera" in prompt


from types import SimpleNamespace
from scripts.scan import (
    parse_jobs, run_scan, build_web_tools, MAX_WEB_SEARCHES,
    MAX_POSTING_FETCHES, MAX_FETCH_CONTENT_TOKENS,
)


def test_web_search_has_hard_max_uses_cap():
    # max_uses makes the search budget an API-enforced ceiling, not a prompt
    # suggestion (the model overran the "~8" prompt bound to 14 in prod).
    search = next(t for t in build_web_tools(_config()) if t["name"] == "web_search")
    assert search["max_uses"] == MAX_WEB_SEARCHES
    assert isinstance(MAX_WEB_SEARCHES, int) and MAX_WEB_SEARCHES > 0


def test_web_fetch_budget_is_the_feeds_plus_a_bounded_posting_allowance():
    # web_fetch was left uncapped because it carries no per-query fee. That
    # reasoning was wrong: every fetched page stays in context and is re-read on
    # each later tool round, so fetches — not searches — drove 55% of the
    # 2026-08-11 bill (3.1M cache-read tokens for $0.93 against $0.06 of search).
    cfg = _config()  # one feed
    fetch = next(t for t in build_web_tools(cfg) if t["name"] == "web_fetch")
    assert fetch["max_uses"] == len(cfg["manual_feeds"]) + MAX_POSTING_FETCHES


def test_web_fetch_budget_scales_with_the_configured_feed_count():
    cfg = dict(_config())
    cfg["manual_feeds"] = [{"location_type": "remote", "url": f"https://x/{i}"} for i in range(7)]
    fetch = next(t for t in build_web_tools(cfg) if t["name"] == "web_fetch")
    assert fetch["max_uses"] == 7 + MAX_POSTING_FETCHES


def test_web_fetch_caps_content_pulled_into_context_per_page():
    # Bounds a single pathological page (a Workday shell, a huge careers index)
    # from dominating the context that every later tool round re-reads.
    fetch = next(t for t in build_web_tools(_config()) if t["name"] == "web_fetch")
    assert fetch["max_content_tokens"] == MAX_FETCH_CONTENT_TOKENS
    assert isinstance(MAX_FETCH_CONTENT_TOKENS, int) and MAX_FETCH_CONTENT_TOKENS > 0


def test_web_fetch_budget_is_the_posting_allowance_when_no_feeds_configured():
    fetch = next(t for t in build_web_tools({}) if t["name"] == "web_fetch")
    assert fetch["max_uses"] == MAX_POSTING_FETCHES


def _text_block(text):
    return SimpleNamespace(type="text", text=text)


class _FakeStream:
    """Context-manager stand-in for client.messages.stream().

    Faithful to the real SDK: the container id is delivered on message_delta
    *events* (delta.container), and the stream accumulator drops it — so
    get_final_message().container stays None. Code that needs the container must
    read it off the iterated events, not the final message.
    """

    def __init__(self, message, events=()):
        self._message = message
        self._events = list(events)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._events)

    def get_final_message(self):
        return self._message


def _message_delta_event(container_id):
    """A raw message_delta stream event carrying a code-execution container id."""
    return SimpleNamespace(
        type="message_delta",
        delta=SimpleNamespace(container=SimpleNamespace(id=container_id)),
    )


def test_parse_jobs_extracts_fenced_array():
    text = 'Here are results:\n```json\n[{"id": "a"}, {"id": "b"}]\n```\nDone.'
    assert parse_jobs(text) == [{"id": "a"}, {"id": "b"}]


def test_parse_jobs_raises_on_no_array():
    try:
        parse_jobs("no json here")
        assert False, "expected ValueError"
    except ValueError:
        pass


class _FakeMessages:
    def __init__(self):
        self.calls = 0

    def stream(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            msg = SimpleNamespace(stop_reason="pause_turn",
                                  content=[_text_block("searching...")])
        else:
            msg = SimpleNamespace(
                stop_reason="end_turn",
                content=[_text_block('```json\n[{"id": "acme-1", "title": "Director"}]\n```')],
            )
        return _FakeStream(msg)


def test_run_scan_handles_pause_turn_and_parses():
    client = SimpleNamespace(messages=_FakeMessages())
    jobs, stats = run_scan(client, "prompt")
    assert jobs == [{"id": "acme-1", "title": "Director"}]
    assert client.messages.calls == 2
    assert stats["calls"] == 2


class _ProseBracketsMessages:
    """Model ignores the fence instruction and returns prose containing brackets
    (e.g. a no-results day). Reproduces the 2026-07-12 daily-scan crash."""

    def stream(self, **kwargs):
        return _FakeStream(SimpleNamespace(
            stop_reason="end_turn",
            content=[_text_block("I searched the feeds but found no new roles [today].")],
        ))


def test_run_scan_degrades_to_empty_on_unparseable_output():
    client = SimpleNamespace(messages=_ProseBracketsMessages())
    jobs, stats = run_scan(client, "prompt")
    assert jobs == []
    assert stats["calls"] == 1


class _ContainerMessages:
    """First call pauses after code execution spawns a container; second completes.

    Mirrors the real SDK: the container id arrives on a message_delta event, and
    the final message's `container` is None (the accumulator drops delta.container).
    """

    def __init__(self):
        self.calls = 0
        self.kwargs_log = []

    def stream(self, **kwargs):
        self.calls += 1
        self.kwargs_log.append(kwargs)
        if self.calls == 1:
            msg = SimpleNamespace(
                stop_reason="pause_turn",
                content=[_text_block("filtering...")],
                container=None,
            )
            return _FakeStream(msg, events=[_message_delta_event("cntr_abc123")])
        msg = SimpleNamespace(
            stop_reason="end_turn",
            content=[_text_block('```json\n[{"id": "acme-1"}]\n```')],
            container=None,
        )
        return _FakeStream(msg, events=[_message_delta_event("cntr_abc123")])


def test_run_scan_threads_container_id_into_continuation():
    client = SimpleNamespace(messages=_ContainerMessages())
    jobs, _ = run_scan(client, "prompt")
    assert jobs == [{"id": "acme-1"}]
    # First call must NOT send container; the continuation MUST echo it (else API 400s).
    assert "container" not in client.messages.kwargs_log[0]
    assert client.messages.kwargs_log[1]["container"] == "cntr_abc123"


def test_run_scan_raises_if_never_completes():
    class _AlwaysPause:
        def stream(self, **kwargs):
            return _FakeStream(SimpleNamespace(stop_reason="pause_turn", content=[_text_block("x")]))
    client = SimpleNamespace(messages=_AlwaysPause())
    try:
        run_scan(client, "prompt", max_continuations=2)
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


from scripts.scan import persist


def _valid_job(id_, url, company="Acme", title="Director of Analytics"):
    return {"id": id_, "title": title, "company": company, "location_type": "remote",
            "url": url, "found_date": "2026-06-28", "status": "new"}


def test_persist_dedupes_and_returns_new(tmp_path: Path):
    data = tmp_path / "jobs.json"
    data.write_text(json.dumps([_valid_job("a", "https://x.com/1")]))
    incoming = [_valid_job("a", "https://x.com/1"),
                _valid_job("b", "https://x.com/2", company="Beta")]
    added, new = persist(incoming, data)
    assert added == 1
    assert len(new) == 1 and new[0]["company"] == "Beta"


def test_persist_skips_invalid(tmp_path: Path):
    data = tmp_path / "jobs.json"
    incoming = [_valid_job("ok", "https://x.com/9"),
                {"id": "broken", "status": "nope"}]
    added, new = persist(incoming, data)
    assert added == 1
    saved = json.loads(data.read_text())
    assert len(saved) == 1


from scripts.scan import build_digest, cost_summary


def test_build_digest_ranks_and_groups():
    jobs = [
        {"title": "A", "company": "C1", "location": "Remote", "location_type": "remote",
         "url": "u1", "source": "linkedin", "score": 70, "score_reasons": ["r"]},
        {"title": "B", "company": "C2", "location": "NYC", "location_type": "nyc",
         "url": "u2", "source": "greenhouse", "score": 95, "score_reasons": ["r2"]},
    ]
    subject, html, text = build_digest(jobs)
    assert "2" in subject
    assert text.index("95") < text.index("70")
    assert "C2" in html and "u2" in html


def test_build_digest_zero_new():
    subject, html, text = build_digest([])
    assert "no new" in (subject + text).lower()


from unittest import mock
from scripts.scan import compute_overall


def test_compute_overall_weighted_average():
    weights = {"seniority": 0.5, "skills": 0.5}
    assert compute_overall({"seniority": 8, "skills": 6}, weights) == 70


def test_compute_overall_missing_dimension_is_zero():
    weights = {"seniority": 0.5, "skills": 0.5}
    assert compute_overall({"seniority": 10}, weights) == 50


from scripts.scan import apply_scores


def test_apply_scores_sets_score_from_breakdown():
    weights = {"seniority": 0.5, "skills": 0.5}
    jobs = [{"id": "a", "score": 0, "score_breakdown": {"seniority": 8, "skills": 6}}]
    out = apply_scores(jobs, weights)
    assert out[0]["score"] == 70
    assert jobs[0]["score"] == 0


def test_apply_scores_leaves_breakdownless_jobs():
    weights = {"seniority": 0.5, "skills": 0.5}
    jobs = [{"id": "b", "score": 42}]
    out = apply_scores(jobs, weights)
    assert out[0]["score"] == 42


from scripts.scan import apply_penalties


def test_apply_penalties_soft_keyword_matches_whole_word():
    config = {"negative_keywords": {"soft": ["Scala"], "hard": []}}
    job = {"title": "Data Analyst", "description_excerpt": "Build with Scala and Spark."}
    bd = apply_penalties(job, {"differentiation": 5, "winnability": 5}, config)
    assert bd == {"differentiation": 4, "winnability": 4}


def test_apply_penalties_soft_keyword_ignores_substring():
    # "scalable" must NOT trip the "Scala" negative keyword.
    config = {"negative_keywords": {"soft": ["Scala"], "hard": []}}
    job = {"title": "Analytics Lead", "description_excerpt": "globally scalable systems"}
    bd = apply_penalties(job, {"differentiation": 5, "winnability": 5}, config)
    assert bd == {"differentiation": 5, "winnability": 5}


from scripts.scan import filter_by_salary


def test_filter_by_salary_drops_below_floor():
    jobs = [{"id": "low", "salary_max": 150000}]
    assert filter_by_salary(jobs, 200000) == []


def test_filter_by_salary_keeps_at_or_above_floor():
    jobs = [{"id": "ok", "salary_max": 220000}]
    assert len(filter_by_salary(jobs, 200000)) == 1


def test_filter_by_salary_keeps_unlisted():
    jobs = [{"id": "none", "salary_max": None}, {"id": "missing"}]
    assert len(filter_by_salary(jobs, 200000)) == 2


def test_send_email_builds_message_and_sends():
    from scripts import notify
    cfg = {"to": "me@example.com", "from": "me@example.com", "password": "secret-pw"}
    with mock.patch("scripts.notify.smtplib.SMTP_SSL") as smtp_cls:
        server = smtp_cls.return_value.__enter__.return_value
        notify.send_email("Subj", "<p>hi</p>", "hi", cfg)
        server.login.assert_called_once_with("me@example.com", "secret-pw")
        server.send_message.assert_called_once()
        sent = server.send_message.call_args[0][0]
        assert sent["Subject"] == "Subj"
        assert sent["To"] == "me@example.com"


def test_build_prompt_includes_salary_floor_and_scoring_instructions():
    prompt = build_prompt(_config(), _profile(), seen_ids=[])
    assert "200000" in prompt or "$200" in prompt
    assert "salary_min" in prompt and "salary_max" in prompt
    assert "score_breakdown" in prompt
    assert "strengths" in prompt and "gaps" in prompt


def test_build_digest_renders_breakdown_and_salary():
    jobs = [{
        "title": "Director, Analytics", "company": "Acme", "location": "Remote",
        "location_type": "remote", "url": "u1", "source": "linkedin", "score": 83,
        "salary_min": 180000, "salary_max": 220000, "salary_raw": "$180k-$220k",
        "score_breakdown": {"differentiation": 9, "winnability": 8, "comp": 6, "location": 8, "domain": 7},
        "strengths": ["marketplace + dbt match"], "gaps": ["no causal-inference mention"],
    }]
    subject, html, text = build_digest(jobs)
    assert "Differentiation 9" in text and "Winnability 8" in text
    assert "marketplace + dbt match" in text
    assert "no causal-inference mention" in text
    assert "180" in text and "220" in text


from scripts.scan import UNTRUSTED_CONTENT_RULE


def test_build_prompt_declares_fetched_postings_untrusted():
    # The scan's web tools pull third-party HTML straight into the model's context.
    # A posting can carry hidden text crafted to steer the run, so the prompt has to
    # say plainly that postings are data, never instructions.
    prompt = build_prompt(_config(), _profile(), seen_ids=[])
    assert UNTRUSTED_CONTENT_RULE in prompt


def test_untrusted_rule_forbids_following_and_fetching_from_postings():
    rule = UNTRUSTED_CONTENT_RULE.lower()
    assert "never follow" in rule
    assert "never fetch" in rule
    assert "instruction" in rule


from datetime import date
from scripts.scan import (
    MAX_TOKENS, EXCERPT_MAX_CHARS, SEEN_ID_WINDOW_DAYS,
    recent_seen_ids, truncate_excerpts,
)


def test_max_tokens_has_headroom_for_a_full_digest():
    # 2026-08-08 ran to completion then died mid-emit at the 6000 cap
    # ("Now I have enough data ... Let me assemble it:"), returning 0 jobs for
    # $0.78. Output tokens bill on what is produced, so a high cap costs nothing
    # unless used — it is purely a truncation guard.
    assert MAX_TOKENS >= 16000


def test_build_prompt_bounds_the_excerpt_length():
    # description_excerpt averaged ~912 chars in the store — the single largest
    # field the model emits, and the reason the final array overran max_tokens.
    prompt = build_prompt(_config(), _profile(), seen_ids=[])
    assert str(EXCERPT_MAX_CHARS) in prompt
    assert "description_excerpt" in prompt


def test_truncate_excerpts_caps_long_excerpt():
    jobs = [{"id": "a", "description_excerpt": "x" * (EXCERPT_MAX_CHARS + 200)}]
    out = truncate_excerpts(jobs)
    assert len(out[0]["description_excerpt"]) <= EXCERPT_MAX_CHARS + 1  # + ellipsis
    assert out[0]["description_excerpt"].endswith("…")


def test_truncate_excerpts_leaves_short_excerpt_and_other_fields_alone():
    jobs = [{"id": "a", "title": "Director", "description_excerpt": "short text"}]
    out = truncate_excerpts(jobs)
    assert out[0] == {"id": "a", "title": "Director", "description_excerpt": "short text"}


def test_truncate_excerpts_tolerates_missing_excerpt():
    assert truncate_excerpts([{"id": "a"}]) == [{"id": "a"}]


def test_truncate_excerpts_does_not_mutate_input():
    jobs = [{"id": "a", "description_excerpt": "y" * (EXCERPT_MAX_CHARS + 50)}]
    truncate_excerpts(jobs)
    assert len(jobs[0]["description_excerpt"]) == EXCERPT_MAX_CHARS + 50


def test_recent_seen_ids_drops_ids_outside_the_window():
    today = date(2026, 8, 12)
    jobs = [
        {"id": "old-role-2026-01", "found_date": "2026-01-05"},
        {"id": "fresh-role-2026-08", "found_date": "2026-08-01"},
    ]
    ids = recent_seen_ids(jobs, window_days=90, today=today)
    assert ids == ["fresh-role-2026-08"]


def test_recent_seen_ids_keeps_ids_with_unparseable_found_date():
    # Unknown age is cheaper to keep than to re-surface a posting already seen.
    today = date(2026, 8, 12)
    jobs = [{"id": "no-date"}, {"id": "bad-date", "found_date": "not-a-date"}]
    assert recent_seen_ids(jobs, window_days=90, today=today) == ["no-date", "bad-date"]


def test_recent_seen_ids_skips_entries_without_an_id():
    today = date(2026, 8, 12)
    assert recent_seen_ids([{"found_date": "2026-08-01"}], window_days=90, today=today) == []


def test_seen_id_window_is_bounded():
    assert isinstance(SEEN_ID_WINDOW_DAYS, int) and 0 < SEEN_ID_WINDOW_DAYS <= 365


def test_run_scan_flags_degraded_when_output_unparseable():
    # The silent-failure mode: 2026-08-08 and 2026-08-09 both returned [] here and
    # emailed a normal "no new matches" digest, indistinguishable from a real
    # zero-match day. The stats must carry the reason so the digest can say so.
    client = SimpleNamespace(messages=_ProseBracketsMessages())
    jobs, stats = run_scan(client, "prompt")
    assert jobs == []
    assert stats["degraded"]


def test_run_scan_leaves_degraded_unset_on_a_clean_parse():
    client = SimpleNamespace(messages=_FakeMessages())
    _, stats = run_scan(client, "prompt")
    assert not stats["degraded"]


def test_build_digest_degraded_is_distinct_from_a_real_zero_day():
    clean_subject, _, _ = build_digest([])
    degraded_subject, degraded_html, degraded_text = build_digest(
        [], degraded="output not parseable as JSON"
    )
    assert degraded_subject != clean_subject
    assert "no new matches" not in degraded_subject.lower()
    assert "output not parseable as JSON" in degraded_text
    assert "output not parseable as JSON" in degraded_html


def test_run_scan_sends_the_configured_web_tools():
    class _Capture:
        def __init__(self):
            self.kwargs_log = []

        def stream(self, **kwargs):
            self.kwargs_log.append(kwargs)
            return _FakeStream(SimpleNamespace(
                stop_reason="end_turn",
                content=[_text_block('```json\n[]\n```')],
            ))

    tools = build_web_tools(_config())
    client = SimpleNamespace(messages=_Capture())
    run_scan(client, "prompt", tools=tools)
    assert client.messages.kwargs_log[0]["tools"] == tools


def test_build_prompt_triages_from_the_feed_listing_before_deep_fetching():
    # The model previously fetched every posting and then applied EXCLUDE_TITLES.
    # Title/company/location are already in the feed listing, so exclusions must
    # happen before the fetch — the fetch is the expensive step.
    prompt = build_prompt(_config(), _profile(), seen_ids=[])
    assert "TRIAGE" in prompt
    assert str(MAX_POSTING_FETCHES) in prompt


def test_build_prompt_states_why_an_unneeded_fetch_is_expensive():
    prompt = build_prompt(_config(), _profile(), seen_ids=[])
    assert "context" in prompt.lower()


def _fetch_result_block():
    return SimpleNamespace(type="web_fetch_tool_result")


def test_stats_count_web_fetches_from_result_blocks():
    # usage.server_tool_use reports web_search_requests only, so the fetch count —
    # the number that says whether the 19-fetch budget is too tight — has to come
    # from counting web_fetch_tool_result blocks in the response content.
    class _WithFetches:
        def stream(self, **kwargs):
            return _FakeStream(SimpleNamespace(
                stop_reason="end_turn",
                content=[_fetch_result_block(), _fetch_result_block(),
                         _text_block('```json\n[]\n```')],
            ))
    _, stats = run_scan(SimpleNamespace(messages=_WithFetches()), "prompt")
    assert stats["web_fetches"] == 2


def test_web_fetches_accumulate_across_continuations():
    class _TwoRounds:
        def __init__(self): self.calls = 0
        def stream(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return _FakeStream(SimpleNamespace(
                    stop_reason="pause_turn",
                    content=[_fetch_result_block(), _text_block("fetching...")]))
            return _FakeStream(SimpleNamespace(
                stop_reason="end_turn",
                content=[_fetch_result_block(), _text_block('```json\n[]\n```')]))
    _, stats = run_scan(SimpleNamespace(messages=_TwoRounds()), "prompt")
    assert stats["web_fetches"] == 2


def test_cost_summary_reports_the_fetch_count():
    stats = _new_stats_for_test()
    stats.update(web_searches=6, web_fetches=14)
    line = cost_summary(stats)
    assert "14 web fetch(es)" in line
    assert "6 web search(es)" in line


def _new_stats_for_test():
    from scripts.scan import _new_stats
    return _new_stats()


import collections
import urllib.parse

import pytest
from scripts.scan import title_multiplier


def test_prompt_drops_the_feed_step_when_no_feeds_are_configured():
    cfg = dict(_config())
    cfg["manual_feeds"] = []
    prompt = build_prompt(cfg, _profile(), seen_ids=[])
    assert "jobs-guest" not in prompt
    # The feed step must not survive as a dangling instruction with nothing under it.
    assert "ONCE on each" not in prompt
    # ...and search must be promoted from last-resort to the starting point.
    assert "ONLY if the feeds yield almost nothing" not in prompt


def test_prompt_keeps_the_feed_step_when_feeds_are_configured():
    """The feed mechanism itself is sound -- it is LinkedIn that refuses. Keep it
    working so an allowed listing source can be added back later."""
    prompt = build_prompt(_config(), _profile(), seen_ids=[])
    assert "ONCE on each" in prompt
    assert _config()["manual_feeds"][0]["url"] in prompt


def _fetch_error_block(code):
    """A refused fetch: the API returns HTTP 200 and the block type is still
    `web_fetch_tool_result` -- only the nested content marks the failure."""
    return SimpleNamespace(
        type="web_fetch_tool_result",
        content=SimpleNamespace(type="web_fetch_tool_result_error", error_code=code),
    )


def test_count_fetch_errors_tallies_failures_by_code():
    from scripts.scan import count_fetch_errors
    blocks = [_fetch_error_block("url_not_allowed"),
              _fetch_error_block("url_not_allowed"),
              _fetch_error_block("url_not_accessible"),
              _fetch_result_block(),
              _text_block("hi")]
    assert count_fetch_errors(blocks) == {"url_not_allowed": 2, "url_not_accessible": 1}


def test_count_fetch_errors_is_empty_when_every_fetch_succeeded():
    from scripts.scan import count_fetch_errors
    assert count_fetch_errors([_fetch_result_block(), _text_block("hi")]) == {}


def test_count_web_fetches_still_counts_failed_fetches():
    """Failed fetches count against the API's max_uses budget, so they belong in
    the total as well as in the error tally."""
    from scripts.scan import count_web_fetches
    assert count_web_fetches([_fetch_error_block("url_not_allowed"),
                              _fetch_result_block()]) == 2


def test_stats_accumulate_fetch_errors_across_continuations():
    class _TwoRounds:
        def __init__(self): self.calls = 0
        def stream(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return _FakeStream(SimpleNamespace(
                    stop_reason="pause_turn",
                    content=[_fetch_error_block("url_not_allowed"),
                             _text_block("fetching...")]))
            return _FakeStream(SimpleNamespace(
                stop_reason="end_turn",
                content=[_fetch_error_block("url_not_allowed"),
                         _text_block('```json\n[]\n```')]))
    _, stats = run_scan(SimpleNamespace(messages=_TwoRounds()), "prompt")
    assert stats["fetch_errors"] == {"url_not_allowed": 2}


def test_cost_summary_surfaces_failed_fetches():
    """Eight weeks of silent robots refusals is the bug this line exists to
    prevent: the run logged '18 web fetch(es)' whether or not any succeeded."""
    stats = _new_stats_for_test()
    stats.update(web_searches=6, web_fetches=18,
                 fetch_errors={"url_not_allowed": 7})
    line = cost_summary(stats)
    assert "18 web fetch(es)" in line
    assert "7 failed" in line
    assert "url_not_allowed" in line


def test_cost_summary_stays_quiet_when_no_fetch_failed():
    stats = _new_stats_for_test()
    stats.update(web_searches=6, web_fetches=14)
    line = cost_summary(stats)
    assert "14 web fetch(es)" in line
    assert "failed" not in line


def test_workflow_web_search_cap_matches_the_code_default():
    """The scheduled run gets its cap from the YAML literal, not from scan.py.

    `.github/workflows/daily-scan.yml` sets
    `JOBSEARCH_MAX_WEB_SEARCHES: ${{ inputs.max_web_searches || 'N' }}`, and on a
    schedule `inputs.*` is empty, so N wins. Raising MAX_WEB_SEARCHES in scan.py
    without touching the workflow is a silent no-op: every test passes, the local
    default is right, and the daily scan keeps running the old cap.
    """
    import re
    from scripts.scan import MAX_WEB_SEARCHES
    yml = Path(".github/workflows/daily-scan.yml").read_text()
    fallback = re.search(r"JOBSEARCH_MAX_WEB_SEARCHES:.*?\|\|\s*'(\d+)'", yml)
    assert fallback, "workflow no longer sets JOBSEARCH_MAX_WEB_SEARCHES"
    assert int(fallback.group(1)) == MAX_WEB_SEARCHES, (
        f"workflow fallback {fallback.group(1)} != scan.py default {MAX_WEB_SEARCHES}"
    )
    dispatch = re.search(r"max_web_searches:.*?default:\s*\"(\d+)\"", yml, re.S)
    assert dispatch and int(dispatch.group(1)) == MAX_WEB_SEARCHES, (
        "workflow_dispatch default drifted from scan.py"
    )


def test_duplicate_trigger_cannot_run_two_scans_in_one_day():
    """Two triggers on the same UTC day must not produce two billed scans.

    The scan is triggered twice by design: cron-job.org fires a `workflow_dispatch`
    at 07:00 Eastern, and the `schedule:` cron stays on as a backstop for
    when that external trigger fails. `concurrency.cancel-in-progress` is false, so
    a second trigger QUEUES rather than being dropped — without a guard, a day where
    both fire runs two full scans, sends two digest emails, and bills the Anthropic
    API twice (~$1.40 instead of ~$0.70).

    The guard job resolves that. This test pins the three pieces it needs, each of
    which fails silently rather than loudly if it drifts:
      - the `scan` job is gated on the guard's verdict,
      - the guard's verdict is actually consulted in an `if:`,
      - `actions: read` is granted. `permissions:` is an allowlist, so naming only
        `contents: write` sets every other scope to `none` and the guard's
        `gh run list` 403s — which would fail closed into never scanning.
    """
    import re
    yml = Path(".github/workflows/daily-scan.yml").read_text()

    assert re.search(r"^\s*guard:\s*$", yml, re.M), "guard job is gone"
    assert re.search(r"needs:\s*guard\b", yml), "scan job no longer depends on guard"
    assert re.search(
        r"if:\s*\$\{\{\s*needs\.guard\.outputs\.should_run\s*==\s*'true'\s*\}\}", yml
    ), "scan job does not gate on the guard's should_run output"
    assert re.search(r"^\s*actions:\s*read\b", yml, re.M), (
        "permissions is an allowlist — without `actions: read` the guard cannot "
        "call `gh run list` and the scan never runs"
    )


# --- 2026-09-01: recall repairs. Three levers were configured but never reached the
# model, and two config lists contradicted each other. See the diagnosis in the commit
# message; each test below pins one of the repairs so it cannot silently regress.


def test_search_budget_was_raised_off_the_binding_cap():
    """Every run from 2026-08-27 to 2026-09-01 spent 12/12 searches and returned
    1-9 candidates: the cap was binding. Search is the cheapest recall lever on the
    board — ~$0.12 of the $0.80 Sep-1 bill against cache-read from fetches, which
    was the bulk. Raising it is the one budget change that buys breadth cheaply."""
    assert MAX_WEB_SEARCHES >= 18




def test_job_shape_enumerates_configured_location_keys():
    from scripts.scan import job_shape
    cfg = {"locations": [{"key": "remote", "label": "Remote", "score": 8},
                         {"key": "denver", "label": "Denver", "score": 10}]}
    assert '"location_type": "remote|denver|other"' in job_shape(cfg)


def test_prompt_locations_line_comes_from_config():
    cfg = dict(_config())
    cfg["locations"] = [{"key": "denver", "label": "Denver", "match": "Denver metro", "score": 10}]
    prompt = build_prompt(cfg, _profile(), seen_ids=[])
    assert "LOCATIONS: Denver (Denver metro)" in prompt
    assert "denver|other" in prompt


def test_apply_scores_scores_location_from_config_and_keeps_unknown_as_other():
    from scripts.scan import apply_scores
    cfg = {"locations": [{"key": "denver", "label": "Denver", "score": 10}]}
    jobs = [{"title": "Director of Analytics", "location_type": "denver",
             "score_breakdown": {"differentiation": 5, "winnability": 5, "domain": 5}},
            {"title": "Director of Analytics", "location_type": "tokyo",
             "score_breakdown": {"differentiation": 5, "winnability": 5, "domain": 5}}]
    weights = {"differentiation": 0.3, "winnability": 0.25, "comp": 0.2, "location": 0.15, "domain": 0.1}
    out = apply_scores(jobs, weights, cfg)
    assert out[0]["score_breakdown"]["location"] == 10
    assert out[1]["location_type"] == "other"
    assert out[1]["score_breakdown"]["location"] == 0


def test_digest_buckets_follow_configured_locations():
    cfg = {"locations": [{"key": "denver", "label": "Denver", "score": 10}]}
    jobs = [{"title": "A", "company": "C1", "location_type": "denver", "url": "u1", "score": 60},
            {"title": "B", "company": "C2", "location_type": "tokyo", "url": "u2", "score": 50}]
    subject, html, text = build_digest(jobs, config=cfg)
    assert "== Denver ==" in text
    assert "== Other ==" in text          # unknown types land in Other, not dropped
    assert "C2" in text


def test_digest_without_config_still_lists_every_job():
    jobs = [{"title": "A", "company": "C1", "location_type": "remote", "url": "u1", "score": 60}]
    _, _, text = build_digest(jobs)
    assert "C1" in text


ALEX_CONTEXT = {
    "differentiator": "running pricing experiments end to end on a warehouse-native stack",
    "differentiator_10": "the posting asks for experimentation platform ownership",
    "differentiator_5": "a solid analytics role any strong candidate could fill",
    "differentiator_0": "pure data engineering",
    "current_level": "Analytics Manager; has managed ICs only",
    "winnability_note": "A VP posting is rarely above 3.",
    "domain_home": "e-commerce / retail",
    "domain_adjacent": "consumer fintech",
    "domain_none": "defense",
}


def test_rubric_renders_every_scoring_context_value():
    from scripts.scan import build_scoring_rubric
    rubric = build_scoring_rubric({"scoring_context": ALEX_CONTEXT})
    for value in ALEX_CONTEXT.values():
        assert value in rubric


def test_rubric_uses_neutral_pronouns():
    from scripts.scan import build_scoring_rubric
    rubric = build_scoring_rubric({"scoring_context": ALEX_CONTEXT})
    assert not re.search(r"\b(he|his|him)\b", rubric)


def test_rubric_without_context_is_generic_and_warns(capsys):
    from scripts.scan import build_scoring_rubric, GENERIC_SCORING_CONTEXT
    rubric = build_scoring_rubric({})
    assert GENERIC_SCORING_CONTEXT["differentiator"] in rubric
    assert "dbt" not in rubric and "Looker" not in rubric
    assert "scoring_context" in capsys.readouterr().err


def test_prompt_profile_json_omits_scoring_context():
    prompt = build_prompt(_config(), {**_profile(), "scoring_context": ALEX_CONTEXT}, seen_ids=[])
    assert '"scoring_context"' not in prompt
    assert ALEX_CONTEXT["differentiator"] in prompt  # still reaches the rubric


def test_scan_checks_email_config_before_calling_the_api(monkeypatch, tmp_path):
    from scripts import scan
    monkeypatch.delenv("JOBSEARCH_EMAIL_TO", raising=False)
    called = []
    monkeypatch.setattr(scan.anthropic, "Anthropic", lambda *a, **k: called.append(1))
    cfg = tmp_path / "c.json"
    cfg.write_text('{"titles": ["x"], "locations": [{"key": "remote", "label": "Remote", "score": 8}]}')
    monkeypatch.setattr(scan, "CONFIG_PATH", cfg)
    monkeypatch.setattr(scan, "PROFILE_PATH", tmp_path / "no-profile.json")
    monkeypatch.setattr(scan, "DATA_PATH", tmp_path / "jobs.json")
    (tmp_path / "jobs.json").write_text("[]")
    with pytest.raises(RuntimeError, match="JOBSEARCH_EMAIL_TO"):
        scan.main([])
    assert called == []


def test_print_prompt_renders_without_api_or_email(monkeypatch, tmp_path, capsys):
    from scripts import scan
    for v in ("JOBSEARCH_EMAIL_TO", "JOBSEARCH_EMAIL_FROM", "GMAIL_APP_PASSWORD", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(scan.anthropic, "Anthropic", lambda *a, **k: pytest.fail("API called"))
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps(_config()))
    monkeypatch.setattr(scan, "CONFIG_PATH", cfg)
    monkeypatch.setattr(scan, "PROFILE_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(scan, "DATA_PATH", tmp_path / "jobs.json")
    (tmp_path / "jobs.json").write_text("[]")
    assert scan.main(["--print-prompt"]) == 0
    assert "TARGET TITLES:" in capsys.readouterr().out
