"""Tests for the robots.txt gate on the browser-header fetch fallback.

The gate's whole job is to fail CLOSED: unless robots.txt affirmatively permits the
path, the retry must not happen. Most of these tests are about the ways a naive
implementation fails OPEN (soft-200 error pages, blank lines mid-record,
percent-encoded rules, tie-breaks) rather than about the happy path.
"""
import pytest

from scripts.robots_check import (
    BROWSER_UA,
    CLAUDE_UA,
    gate,
    is_allowed,
    looks_like_robots,
    main,
    rule_match,
)


def _fetcher(responses):
    """Build a fetcher over {user_agent: (body, status)} or a single (body, status).

    A value that is an Exception instance is raised instead of returned, so tests can
    model a host that hangs up on one user agent but answers another.
    """
    calls = []

    def fetch(url, user_agent):
        calls.append((url, user_agent))
        result = responses[user_agent] if isinstance(responses, dict) else responses
        if isinstance(result, Exception):
            raise result
        return result

    fetch.calls = calls
    return fetch


# --- rule matching -------------------------------------------------------------


def test_empty_pattern_never_matches():
    # "Disallow:" with no value is the RFC's explicit allow-all; it must not be
    # treated as the prefix "" that matches every path.
    assert rule_match("", "/jobs/view/123") == -1


def test_wildcard_matches_and_scores_by_pattern_length():
    assert rule_match("/jobs/*/apply", "/jobs/123/apply") == len("/jobs/*/apply")
    assert rule_match("/jobs/*/apply", "/careers/123/apply") == -1


def test_dollar_anchors_end_of_path():
    assert rule_match("/jobs$", "/jobs") > 0
    assert rule_match("/jobs$", "/jobs/view/1") == -1


def test_percent_encoded_pattern_matches_decoded_path():
    # Without decoding, "Disallow: /job%20search" silently never matches the decoded
    # path and the rule is skipped entirely — a fail-open on any site that encodes
    # its own rules.
    assert rule_match("/job%20search", "/job search/1") > 0


# --- group resolution ----------------------------------------------------------


def test_no_rules_means_allowed():
    assert is_allowed("", CLAUDE_UA, "/jobs/view/1") is True


def test_disallow_all_blocks():
    robots = "User-agent: *\nDisallow: /"
    assert is_allowed(robots, CLAUDE_UA, "/jobs/view/1") is False


def test_longest_match_wins_so_specific_allow_beats_broad_disallow():
    robots = "User-agent: *\nDisallow: /jobs\nAllow: /jobs/view"
    assert is_allowed(robots, CLAUDE_UA, "/jobs/view/1") is True
    assert is_allowed(robots, CLAUDE_UA, "/jobs/search") is False


def test_equal_specificity_prefers_disallow():
    robots = "User-agent: *\nAllow: /jobs\nDisallow: /jobs"
    assert is_allowed(robots, CLAUDE_UA, "/jobs") is False


def test_named_agent_group_overrides_wildcard_group():
    robots = "User-agent: *\nDisallow: /\n\nUser-agent: Claude-User\nAllow: /jobs"
    assert is_allowed(robots, CLAUDE_UA, "/jobs/view/1") is True


def test_an_empty_named_group_wins_over_the_wildcard_group():
    # RFC 9309: only the first matching group applies. Naming an agent with no rules
    # under it grants it everything; it must not fall through to the stricter * group.
    robots = "User-agent: *\nDisallow: /\n\nUser-agent: Claude-User\n"
    assert is_allowed(robots, CLAUDE_UA, "/jobs/view/1") is True


def test_named_agent_match_is_case_insensitive():
    robots = "User-agent: claude-user\nDisallow: /jobs"
    assert is_allowed(robots, "Claude-User", "/jobs/view/1") is False


def test_blank_line_inside_a_record_does_not_end_it():
    # Python's stdlib robotparser drops the rules after the blank line here, which
    # reads the file as allow-all — the exact fail-open this gate exists to avoid.
    robots = "User-agent: Claude-User\n\nDisallow: /jobs"
    assert is_allowed(robots, CLAUDE_UA, "/jobs/view/1") is False


def test_comments_and_whitespace_are_ignored():
    robots = "# hello\nUser-agent: *   # everyone\n  Disallow: /jobs  # no scraping\n"
    assert is_allowed(robots, CLAUDE_UA, "/jobs/1") is False


# --- soft-200 detection --------------------------------------------------------


def test_empty_body_is_a_valid_allow_all_robots():
    assert looks_like_robots("   \n\n") is True


def test_html_error_page_is_not_a_robots_file():
    # A host answering /robots.txt with 200 + an HTML error page parses to zero rules,
    # and zero rules read as "allowed" — a soft-200 granting permission never given.
    assert looks_like_robots("<html><body>404 Not Found</body></html>") is False


def test_body_with_a_directive_is_a_robots_file():
    assert looks_like_robots("Sitemap: https://example.com/sitemap.xml") is True


# --- the gate ------------------------------------------------------------------


def test_gate_allows_when_robots_permits():
    ok, reason = gate("https://boards.example.com/jobs/view/1",
                      fetcher=_fetcher(("User-agent: *\nAllow: /", 200)))
    assert ok is True
    assert "robots.txt" in reason


def test_gate_refuses_when_robots_disallows():
    ok, reason = gate("https://boards.example.com/jobs/view/1",
                      fetcher=_fetcher(("User-agent: *\nDisallow: /jobs", 200)))
    assert ok is False
    assert "disallow" in reason.lower()


def test_gate_allows_on_404_because_no_published_policy_is_permission():
    ok, reason = gate("https://boards.example.com/jobs/1", fetcher=_fetcher(("", 404)))
    assert ok is True
    assert "404" in reason


def test_gate_refuses_when_robots_is_unreadable():
    ok, reason = gate("https://boards.example.com/jobs/1", fetcher=_fetcher(("", 500)))
    assert ok is False
    assert "500" in reason


def test_gate_refuses_when_the_fetch_raises():
    ok, reason = gate("https://boards.example.com/jobs/1",
                      fetcher=_fetcher(TimeoutError("timed out")))
    assert ok is False
    assert "unconfirmed" in reason.lower()


def test_gate_refuses_on_a_soft_200_html_error_page():
    ok, reason = gate("https://boards.example.com/jobs/1",
                      fetcher=_fetcher(("<html>oops</html>", 200)))
    assert ok is False


def test_gate_retries_robots_with_a_browser_agent_when_claude_user_is_blocked():
    # The WAF that 403s WebFetch often 403s its request for robots.txt too. Reading
    # the published policy is not the same as overriding it, so a second read with a
    # browser agent is fair — the policy it returns is still obeyed.
    fetcher = _fetcher({
        CLAUDE_UA: ("", 403),
        BROWSER_UA: ("User-agent: *\nAllow: /", 200),
    })
    ok, _ = gate("https://boards.example.com/jobs/1", fetcher=fetcher)
    assert ok is True
    assert [ua for _, ua in fetcher.calls] == [CLAUDE_UA, BROWSER_UA]


def test_gate_reads_robots_from_the_site_root_not_the_posting_path():
    fetcher = _fetcher(("", 404))
    gate("https://boards.example.com/jobs/view/1?ref=x", fetcher=fetcher)
    assert fetcher.calls[0][0] == "https://boards.example.com/robots.txt"


def test_gate_matches_rules_against_the_query_string_too():
    robots = "User-agent: *\nDisallow: /*?*ref="
    ok, _ = gate("https://boards.example.com/jobs/1?ref=spam", fetcher=_fetcher((robots, 200)))
    assert ok is False


# --- CLI -----------------------------------------------------------------------


def test_main_exits_zero_when_the_retry_is_permitted(capsys):
    rc = main(["https://boards.example.com/jobs/1"], fetcher=_fetcher(("", 404)))
    assert rc == 0
    assert "ALLOWED" in capsys.readouterr().out


def test_main_exits_one_when_the_retry_is_refused(capsys):
    rc = main(["https://boards.example.com/jobs/1"],
              fetcher=_fetcher(("User-agent: *\nDisallow: /", 200)))
    assert rc == 1
    assert "REFUSED" in capsys.readouterr().out


def test_main_does_not_fetch_the_page_when_the_gate_refuses(capsys):
    fetcher = _fetcher(("User-agent: *\nDisallow: /", 200))
    rc = main(["--fetch", "https://boards.example.com/jobs/1"], fetcher=fetcher)
    assert rc == 1
    # Only robots.txt was ever requested — the posting itself was never fetched.
    assert {url for url, _ in fetcher.calls} == {"https://boards.example.com/robots.txt"}


def test_main_fetch_prints_the_page_body_when_permitted(capsys):
    def fetch(url, user_agent):
        if url.endswith("/robots.txt"):
            return "", 404
        return "<h1>Staff Analytics Engineer</h1>", 200

    rc = main(["--fetch", "https://boards.example.com/jobs/1"], fetcher=fetch)
    assert rc == 0
    assert "Staff Analytics Engineer" in capsys.readouterr().out


def test_main_fetch_reports_a_failed_page_fetch_as_nonzero(capsys):
    def fetch(url, user_agent):
        return ("", 404) if url.endswith("/robots.txt") else ("", 403)

    rc = main(["--fetch", "https://boards.example.com/jobs/1"], fetcher=fetch)
    assert rc == 1


@pytest.mark.parametrize("url", ["not-a-url", "ftp://example.com/x", ""])
def test_main_rejects_non_http_urls(url, capsys):
    assert main([url], fetcher=_fetcher(("", 404))) == 1
