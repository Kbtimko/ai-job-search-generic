import json

from linkedin import FALLBACK_OWNER, resolve_owner


def _profile(tmp_path, basics):
    p = tmp_path / "master.json"
    p.write_text(json.dumps({"basics": basics, "experience": []}))
    return p


def test_env_override_wins(tmp_path):
    p = _profile(tmp_path, {"name": "Alex J. Rivera", "linkedin_name": "Alex Rivera"})
    assert resolve_owner(p, {"LINKEDIN_ARCHIVE_OWNER": "A. Rivera"}) == "A. Rivera"


def test_linkedin_name_beats_resume_name(tmp_path):
    # A résumé name with a middle initial must not be used when the export shows none —
    # a mismatch silently flips every inbound/outbound attribution.
    p = _profile(tmp_path, {"name": "Alex J. Rivera", "linkedin_name": "Alex Rivera"})
    assert resolve_owner(p, {}) == "Alex Rivera"


def test_falls_back_to_resume_name(tmp_path):
    assert resolve_owner(_profile(tmp_path, {"name": "Alex Rivera"}), {}) == "Alex Rivera"


def test_missing_profile_uses_the_fallback(tmp_path):
    assert resolve_owner(tmp_path / "nope.json", {}) == FALLBACK_OWNER
