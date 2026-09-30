import pytest

from scripts import locations

CFG = {"locations": [
    {"key": "remote", "label": "Remote", "match": "US-remote", "score": 8},
    {"key": "denver", "label": "Denver", "match": "Denver metro", "score": 10},
]}


def test_entries_appends_implicit_other_last():
    assert [e["key"] for e in locations.entries(CFG)] == ["remote", "denver", "other"]


def test_configured_other_replaces_the_default():
    cfg = {"locations": CFG["locations"] + [{"key": "other", "label": "Elsewhere", "score": 2}]}
    assert locations.entries(cfg)[-1]["label"] == "Elsewhere"
    assert locations.score("other", cfg) == 2
    assert locations.keys(cfg).count("other") == 1


def test_score_by_key_and_unknown_falls_back_to_other():
    assert locations.score("denver", CFG) == 10
    assert locations.score("tokyo", CFG) == 0
    assert locations.score(None, CFG) == 0


def test_normalize_type_maps_unknown_to_other():
    assert locations.normalize_type("remote", CFG) == "remote"
    assert locations.normalize_type("tokyo", CFG) == "other"
    assert locations.normalize_type(None, CFG) == "other"


def test_check_type_names_the_valid_keys():
    with pytest.raises(ValueError, match="valid: remote, denver, other"):
        locations.check_type("tokyo", CFG)
    locations.check_type("denver", CFG)  # no raise


def test_old_string_list_shape_is_rejected_with_a_pointer():
    with pytest.raises(ValueError, match="key/label/score"):
        locations.entries({"locations": ["remote", "Denver"]})


def test_prompt_line_renders_labels_with_match_hints():
    assert locations.prompt_line(CFG) == "Remote (US-remote), Denver (Denver metro)"


def test_prompt_line_defaults_the_match_hint():
    cfg = {"locations": [{"key": "remote", "label": "Remote", "score": 8},
                         {"key": "austin", "label": "Austin", "score": 9}]}
    assert locations.prompt_line(cfg) == "Remote (US-remote), Austin (Austin metro)"
