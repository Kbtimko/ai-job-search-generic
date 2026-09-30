from scripts.store import patch_application


def _app(**over):
    base = {"company": "Acme", "title": "Director", "stage": "applied",
            "applied_date": "2026-06-20",
            "stage_history": [{"stage": "applied", "date": "2026-06-20", "source": "manual"}]}
    base.update(over)
    return base


def test_patch_sets_metadata_fields():
    existing = [_app()]
    merged, changed = patch_application(existing, "acme::director",
                                        {"notes": "call Tue", "next_action": "prep",
                                         "due_date": "2026-07-10"})
    assert changed is True
    assert merged[0]["notes"] == "call Tue"
    assert merged[0]["next_action"] == "prep"
    assert merged[0]["due_date"] == "2026-07-10"


def test_patch_ignores_stage_and_unknown_fields():
    existing = [_app()]
    merged, changed = patch_application(existing, "acme::director",
                                        {"stage": "offer", "notes": "ok", "bogus": 1})
    assert changed is True
    assert merged[0]["stage"] == "applied"       # stage NOT changed by patch
    assert merged[0]["notes"] == "ok"
    assert "bogus" not in merged[0]              # unknown field dropped


def test_patch_unknown_key_is_noop():
    existing = [_app()]
    merged, changed = patch_application(existing, "nope::nobody", {"notes": "x"})
    assert changed is False
    assert "notes" not in merged[0]


def test_patch_does_not_mutate_input():
    existing = [_app()]
    patch_application(existing, "acme::director", {"notes": "x"})
    assert "notes" not in existing[0]           # deep-copied
