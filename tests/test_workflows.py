import re
from pathlib import Path

import pytest

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows"


@pytest.mark.parametrize("name", ["daily-scan.yml", "pipeline-scan.yml"])
def test_workflow_emails_come_from_repo_variables(name):
    text = (WF / name).read_text()
    assert "${{ vars.JOBSEARCH_EMAIL_TO }}" in text
    assert "${{ vars.JOBSEARCH_EMAIL_FROM }}" in text
    assert not re.search(r"JOBSEARCH_EMAIL_(TO|FROM):\s*[^$\s]", text), "literal address in workflow"
    assert "@" not in "".join(l for l in text.splitlines() if "JOBSEARCH_EMAIL" in l)


def _job_block(text: str, job: str) -> str:
    m = re.search(rf"^  {job}:\n(.*?)(?=^  \w[\w-]*:\n|\Z)", text, re.S | re.M)
    assert m, f"no job {job}"
    return m.group(1)


def test_scheduled_scans_skip_until_an_email_recipient_is_configured():
    """A fresh fork or the public template has no JOBSEARCH_EMAIL_TO: skip, don't fail."""
    gate = "vars.JOBSEARCH_EMAIL_TO != ''"
    assert re.search(rf"^    if: .*{re.escape(gate)}", _job_block((WF / "pipeline-scan.yml").read_text(), "scan"), re.M)
    assert re.search(rf"^    if: .*{re.escape(gate)}", _job_block((WF / "daily-scan.yml").read_text(), "guard"), re.M)


def test_daily_scan_skips_without_an_anthropic_key_and_still_honours_force():
    text = (WF / "daily-scan.yml").read_text()
    guard = _job_block(text, "guard")
    assert "secrets.ANTHROPIC_API_KEY != ''" in guard
    # the key check runs before the force bypass, so force cannot start a keyless scan
    assert guard.index("HAVE_KEY") < guard.index('"$FORCE" == "true"')
    assert "workflow_dispatch" in text and "needs.guard.outputs.should_run == 'true'" in text
