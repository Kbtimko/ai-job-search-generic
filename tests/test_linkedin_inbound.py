import csv
import json
from datetime import date
from pathlib import Path

import linkedin.inbound as inbound
from linkedin.inbound import (
    RECRUITER_SIGNAL_THRESHOLD,
    bucket_title,
    classify_all,
    classify_message,
    extract_title,
    message_hash,
    run,
    thread_reply_stats,
)

FIXTURES = Path(__file__).parent / "fixtures" / "linkedin"

RECRUITER = ("Hi Alex, I am a recruiter reaching out about a Director of Analytics "
             "opportunity at Northwind. Would you be open to a conversation?")
SOCIAL = "Congrats on the new role! Great to see."


def test_classifies_obvious_recruiter_outreach():
    assert classify_message(RECRUITER)["is_recruiter"] is True


def test_classifies_social_message_as_not_recruiter():
    assert classify_message(SOCIAL)["is_recruiter"] is False


def test_empty_body_is_not_recruiter():
    assert classify_message("")["is_recruiter"] is False


def test_classification_reports_matched_signals_for_auditability():
    """The signals are the whole basis for reviewing precision by hand, so they have
    to actually account for the verdict rather than merely be present."""
    fired = classify_message(RECRUITER)
    assert "recruiter" in fired["signals"]
    assert len(fired["signals"]) >= RECRUITER_SIGNAL_THRESHOLD, \
        "a positive verdict must be explained by at least as many signals as it needs"

    quiet = classify_message(SOCIAL)
    assert len(quiet["signals"]) < RECRUITER_SIGNAL_THRESHOLD
    assert quiet["is_recruiter"] is False


def test_message_hash_is_stable_and_content_only():
    assert message_hash(RECRUITER) == message_hash(RECRUITER)
    assert message_hash(RECRUITER) != message_hash(SOCIAL)


def test_classify_all_uses_and_populates_the_cache(tmp_path):
    cache_path = tmp_path / "cache.json"
    messages = [{"content": RECRUITER}, {"content": SOCIAL}]

    first = classify_all(messages, cache_path)
    assert first[0]["is_recruiter"] is True
    assert cache_path.exists()

    cached = json.loads(cache_path.read_text())
    assert len(cached["entries"]) == 2

    # Poison the cache: a second run must read it rather than reclassify.
    cached["entries"][message_hash(RECRUITER)]["is_recruiter"] = False
    cache_path.write_text(json.dumps(cached))
    second = classify_all(messages, cache_path)
    assert second[0]["is_recruiter"] is False, "cache was not consulted"


def test_bucket_title_maps_to_configured_buckets():
    assert bucket_title("Senior Manager, Analytics") == "current_archetype"
    assert bucket_title("Head of Data") == "head_of_data"
    assert bucket_title("Director of Analytics") == "director_plus_analytics"
    assert bucket_title("Product Manager, Data Platform") == "analytical_product"
    assert bucket_title("Strategic Finance Lead") == "bizops_finance"
    assert bucket_title("Underwater Basket Weaver") == "other"
    assert bucket_title("") == "other"



def test_extract_title_finds_a_role_before_a_role_noun():
    assert extract_title("about a Director of Analytics opportunity at Northwind") \
        == "Director of Analytics"


def test_extract_title_handles_the_role_phrasing():
    assert extract_title("we have an opening for a Head of Data role") == "Head of Data"


def test_extract_title_returns_none_when_absent():
    assert extract_title("Congrats on the new gig!") is None
    assert extract_title("") is None


def test_thread_reply_stats_detects_my_reply_and_latency():
    messages = [
        {"conversation_id": "c1", "from": "Ada Nolan",
         "date_parsed": date(2026, 6, 1), "content": "x"},
        {"conversation_id": "c1", "from": "Alex Rivera",
         "date_parsed": date(2026, 6, 3), "content": "y"},
    ]
    stats = thread_reply_stats(messages, me="Alex Rivera")
    assert stats["c1"]["inbound_first"] is True
    assert stats["c1"]["i_replied"] is True
    assert stats["c1"]["reply_latency_days"] == 2


def test_thread_with_no_reply_is_recorded_as_unanswered():
    messages = [{"conversation_id": "c2", "from": "Bo Reyes",
                 "date_parsed": date(2026, 6, 1), "content": "x"}]
    stats = thread_reply_stats(messages, me="Alex Rivera")
    assert stats["c2"]["i_replied"] is False
    assert stats["c2"]["reply_latency_days"] is None


def test_outbound_thread_tracks_whether_they_replied():
    messages = [
        {"conversation_id": "c3", "from": "Alex Rivera",
         "date_parsed": date(2026, 5, 1), "content": "x"},
        {"conversation_id": "c3", "from": "Dee Okafor",
         "date_parsed": date(2026, 5, 4), "content": "y"},
    ]
    stats = thread_reply_stats(messages, me="Alex Rivera")
    assert stats["c3"]["inbound_first"] is False
    assert stats["c3"]["they_replied"] is True
    assert stats["c3"]["reply_latency_days"] == 3



def test_run_writes_all_four_output_files(tmp_path):
    run(FIXTURES, tmp_path, me="Alex Rivera")
    for name in ("inbound_titles_by_year.csv", "inbound_reply_rate.csv",
                 "outbound_reply_rate.csv", "classifier_sample.csv"):
        assert (tmp_path / name).exists(), name


def _data_lines(path):
    """Every file this module writes leads with a ``#`` caveat; consumers skip it."""
    lines = path.read_text().splitlines(keepends=True)
    assert lines[0].startswith("#"), f"{path.name} must lead with the caveat"
    return [line for line in lines if not line.startswith("#")]


def test_titles_by_year_has_bucket_and_year_columns(tmp_path):
    run(FIXTURES, tmp_path, me="Alex Rivera")
    header = next(csv.reader(_data_lines(tmp_path / "inbound_titles_by_year.csv")))
    assert header[0] == "title_bucket"
    assert any(column.isdigit() for column in header[1:]), "year columns expected"


def test_classifier_sample_exposes_signals_for_precision_review(tmp_path):
    run(FIXTURES, tmp_path, me="Alex Rivera")
    rows = list(csv.DictReader(_data_lines(tmp_path / "classifier_sample.csv")))
    assert rows
    assert "signals" in rows[0] and "is_recruiter" in rows[0]


def test_reply_rate_files_carry_the_precision_caveat(tmp_path):
    run(FIXTURES, tmp_path, me="Alex Rivera")
    text = (tmp_path / "inbound_reply_rate.csv").read_text()
    assert text.startswith("#"), "caveat header must be in the file itself"
    assert "precision" in text.lower()


def test_run_returns_counts_for_reporting(tmp_path):
    result = run(FIXTURES, tmp_path, me="Alex Rivera")
    assert result["inbound_total"] == 4
    assert result["recruiter_total"] >= 1


def test_outbound_file_says_it_is_initiated_threads_not_cold_outreach(tmp_path):
    """The 0.8-0.9 reply rates read as spectacular cold outreach. They are not.

    The file counts threads I opened, overwhelmingly with people I already knew,
    so the header has to say so before a reader draws a conclusion from it.
    """
    run(FIXTURES, tmp_path, me="Alex Rivera")
    text = (tmp_path / "outbound_reply_rate.csv").read_text().lower()
    assert "not cold outreach" in text
    assert "first message" in text, "must say what it actually measures"
    assert "already know" in text, "must explain why the rate is high"


def test_outbound_caveat_does_not_leak_onto_the_inbound_file(tmp_path):
    """Per-file caveats must discriminate, not paste every warning everywhere."""
    run(FIXTURES, tmp_path, me="Alex Rivera")
    text = (tmp_path / "inbound_reply_rate.csv").read_text().lower()
    assert "not cold outreach" not in text
    assert "first message" not in text


def test_cache_is_invalidated_when_the_classification_rules_change(monkeypatch, tmp_path):
    """A content hash identifies the message, not the verdict. Keyed on content alone
    the cache can never be invalidated: edit the signals or the threshold and every
    message ever seen keeps its old answer, so the model-based classifier the module
    docstring calls "a drop-in" would be a no-op on the entire existing corpus."""
    cache_path = tmp_path / "cache.json"
    messages = [{"content": RECRUITER}]
    assert classify_all(messages, cache_path)[0]["is_recruiter"] is True

    # RECRUITER trips several signals; demanding more than exist must flip the verdict.
    monkeypatch.setattr(inbound, "RECRUITER_SIGNAL_THRESHOLD", 99)
    again = classify_all(messages, cache_path)
    assert again[0]["is_recruiter"] is False, "stale verdict served after a rules change"


def test_changed_rules_are_recorded_so_the_next_run_stays_warm(monkeypatch, tmp_path):
    """Invalidation must re-key the cache, not just bypass it once, or every later run
    reclassifies the whole corpus from scratch."""
    cache_path = tmp_path / "cache.json"
    messages = [{"content": RECRUITER}]
    classify_all(messages, cache_path)
    monkeypatch.setattr(inbound, "RECRUITER_SIGNAL_THRESHOLD", 99)
    classify_all(messages, cache_path)

    # Poisoning after the re-key proves the new entry is the one now being read.
    stored = json.loads(cache_path.read_text())
    entry = next(iter(stored["entries"].values()))
    entry["is_recruiter"] = "sentinel"
    cache_path.write_text(json.dumps(stored))
    assert classify_all(messages, cache_path)[0]["is_recruiter"] == "sentinel"


def test_a_cache_written_before_versioning_is_discarded(tmp_path):
    """An existing cache on disk carries no rules fingerprint, so its verdicts cannot
    be attributed to any ruleset. Recompute rather than trust it."""
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(json.dumps({message_hash(RECRUITER): {
        "is_recruiter": False, "signals": []}}))
    assert classify_all([{"content": RECRUITER}], cache_path)[0]["is_recruiter"] is True
