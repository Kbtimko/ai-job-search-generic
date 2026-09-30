import base64
import json

import pytest

import scripts.github_store as gh


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    monkeypatch.setenv("STORE_GIT_OWNER", "me")
    monkeypatch.setenv("STORE_GIT_REPO", "job-search")
    monkeypatch.setenv("STORE_GIT_BRANCH", "main")
    gh.reset_sha_cache()
    yield
    gh.reset_sha_cache()


def _abs(name):
    return gh.REPO_ROOT / "data" / name


def test_load_decodes_base64_and_caches_sha(monkeypatch):
    payload = [{"company": "Etsy"}]
    encoded = base64.b64encode((json.dumps(payload)).encode()).decode()
    monkeypatch.setattr(gh, "_get", lambda repo_path: {"sha": "abc", "content": encoded})
    assert gh.load(_abs("applications.json")) == payload
    assert gh._sha_cache.get()["data/applications.json"] == "abc"


def test_load_missing_file_returns_empty(monkeypatch):
    monkeypatch.setattr(gh, "_get", lambda repo_path: None)  # 404 -> None
    assert gh.load(_abs("jobs.json")) == []


def test_save_sends_cached_sha(monkeypatch):
    gh._sha_cache.set({"data/applications.json": "sha123"})
    sent = {}
    monkeypatch.setattr(gh, "_put", lambda repo_path, body: sent.update(body=body, path=repo_path) or {"content": {"sha": "new"}})
    gh.save(_abs("applications.json"), [{"a": 1}])
    assert sent["path"] == "data/applications.json"
    assert sent["body"]["sha"] == "sha123"
    assert sent["body"]["branch"] == "main"
    decoded = base64.b64decode(sent["body"]["content"]).decode()
    assert json.loads(decoded) == [{"a": 1}]


def test_save_without_cached_sha_creates(monkeypatch):
    gh.reset_sha_cache()
    monkeypatch.setattr(gh, "_get", lambda repo_path: None)  # no existing file
    sent = {}
    monkeypatch.setattr(gh, "_put", lambda repo_path, body: sent.update(body=body) or {"content": {"sha": "new"}})
    gh.save(_abs("jobs.json"), [{"a": 1}])
    assert "sha" not in sent["body"]  # create: no sha


def test_save_conflict_raises(monkeypatch):
    from scripts.store import StoreConflict
    gh._sha_cache.set({"data/applications.json": "stale"})

    def _put_409(repo_path, body):
        raise gh.GithubStatusError(409)

    monkeypatch.setattr(gh, "_put", _put_409)
    with pytest.raises(StoreConflict):
        gh.save(_abs("applications.json"), [{"a": 1}])
