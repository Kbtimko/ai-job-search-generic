import pytest


@pytest.fixture(autouse=True)
def _no_real_linkedin_archive(monkeypatch, tmp_path):
    """The main checkout holds a real LinkedIn archive at the tracker's default path;
    no test may read it. Tests that want warm paths set LINKEDIN_ARCHIVE_DIR to the
    fictional fixture archive explicitly."""
    monkeypatch.setattr("scripts.tracker.DEFAULT_LINKEDIN_ARCHIVE",
                        tmp_path / "no-linkedin-archive")
