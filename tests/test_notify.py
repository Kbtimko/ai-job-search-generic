import pytest

from scripts import notify

VARS = ("JOBSEARCH_EMAIL_TO", "JOBSEARCH_EMAIL_FROM", "GMAIL_APP_PASSWORD")


def test_email_cfg_names_every_missing_variable(monkeypatch):
    for v in VARS:
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(RuntimeError) as exc:
        notify.email_cfg()
    for v in VARS:
        assert v in str(exc.value)


def test_email_cfg_has_no_baked_in_address(monkeypatch):
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "x")
    monkeypatch.delenv("JOBSEARCH_EMAIL_TO", raising=False)
    monkeypatch.delenv("JOBSEARCH_EMAIL_FROM", raising=False)
    with pytest.raises(RuntimeError):
        notify.email_cfg()


def test_email_cfg_reads_env(monkeypatch):
    monkeypatch.setenv("JOBSEARCH_EMAIL_TO", "alex@example.com")
    monkeypatch.setenv("JOBSEARCH_EMAIL_FROM", "alex@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
    assert notify.email_cfg() == {"to": "alex@example.com", "from": "alex@example.com", "password": "pw"}
