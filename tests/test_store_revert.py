from scripts.store import revert_application


def _app(stage, hist, applied_date="2026-06-20"):
    return {"company": "Acme", "title": "Director", "stage": stage,
            "applied_date": applied_date, "stage_history": list(hist)}


def _h(stage, date="2026-06-20"):
    return {"stage": stage, "date": date, "source": "manual"}


def test_revert_pops_and_sets_prior_stage():
    existing = [_app("interview", [_h("draft"), _h("applied"), _h("interview")])]
    merged, changed = revert_application(existing, "acme::director")
    assert changed is True
    assert merged[0]["stage"] == "applied"
    assert [e["stage"] for e in merged[0]["stage_history"]] == ["draft", "applied"]


def test_revert_single_entry_is_noop():
    existing = [_app("draft", [_h("draft")], applied_date=None)]
    merged, changed = revert_application(existing, "acme::director")
    assert changed is False
    assert merged[0]["stage"] == "draft"
    assert len(merged[0]["stage_history"]) == 1


def test_revert_from_terminal_restores_active():
    existing = [_app("rejected", [_h("draft"), _h("applied"), _h("interview"), _h("rejected")])]
    merged, changed = revert_application(existing, "acme::director")
    assert changed is True
    assert merged[0]["stage"] == "interview"


def test_revert_to_draft_clears_applied_date():
    existing = [_app("applied", [_h("draft"), _h("applied")])]
    merged, changed = revert_application(existing, "acme::director")
    assert changed is True
    assert merged[0]["stage"] == "draft"
    assert merged[0]["applied_date"] is None


def test_revert_unknown_key_is_noop():
    existing = [_app("applied", [_h("draft"), _h("applied")])]
    merged, changed = revert_application(existing, "nobody::nope")
    assert changed is False


def test_revert_does_not_mutate_input():
    existing = [_app("applied", [_h("draft"), _h("applied")])]
    revert_application(existing, "acme::director")
    assert existing[0]["stage"] == "applied"
    assert len(existing[0]["stage_history"]) == 2
