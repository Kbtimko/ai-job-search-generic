"""GitHub Contents API storage backend for the deployed tracker.

Reads/commits the JSON data stores in the repo instead of the local filesystem.
Active only when STORE_BACKEND=github. Uses optimistic concurrency: the blob SHA
read during a request is sent on the matching write; a stale SHA -> 409 -> StoreConflict.
"""
from __future__ import annotations

import base64
import contextvars
import json
import os
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
_API = "https://api.github.com"

# request-scoped {repo_path: sha} captured on load, consumed on save
_sha_cache: contextvars.ContextVar[dict] = contextvars.ContextVar("store_sha_cache", default=None)


class GithubStatusError(RuntimeError):
    def __init__(self, status: int, body: str = ""):
        super().__init__(f"GitHub API {status}: {body}")
        self.status = status


def reset_sha_cache() -> None:
    """Start a fresh per-request SHA cache. Call at the top of each request."""
    _sha_cache.set({})


def _cache() -> dict:
    c = _sha_cache.get()
    if c is None:
        c = {}
        _sha_cache.set(c)
    return c


def _cfg() -> tuple[str, str, str, str]:
    token = os.environ["GITHUB_TOKEN"]
    owner = os.environ["STORE_GIT_OWNER"]
    repo = os.environ["STORE_GIT_REPO"]
    branch = os.environ.get("STORE_GIT_BRANCH", "main")
    return token, owner, repo, branch


def _repo_path(path: Path) -> str:
    return Path(path).resolve().relative_to(REPO_ROOT).as_posix()


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def _get(repo_path: str) -> dict | None:
    """Return the contents JSON for repo_path on the branch, or None if 404."""
    token, owner, repo, branch = _cfg()
    url = f"{_API}/repos/{owner}/{repo}/contents/{repo_path}"
    r = requests.get(url, headers=_headers(token), params={"ref": branch}, timeout=15)
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        raise GithubStatusError(r.status_code, r.text)
    return r.json()


def _put(repo_path: str, body: dict) -> dict:
    token, owner, repo, _branch = _cfg()
    url = f"{_API}/repos/{owner}/{repo}/contents/{repo_path}"
    r = requests.put(url, headers=_headers(token), json=body, timeout=15)
    if r.status_code >= 400:
        raise GithubStatusError(r.status_code, r.text)
    return r.json()


def load(path: Path) -> list[dict]:
    repo_path = _repo_path(path)
    data = _get(repo_path)
    if data is None:
        return []
    self_sha = data.get("sha")
    if self_sha:
        _cache()[repo_path] = self_sha
    raw_b64 = data.get("content")
    if not raw_b64:
        raise ValueError(f"{repo_path}: GitHub returned no file content (keys={list(data.keys())})")
    raw = base64.b64decode(raw_b64).decode("utf-8")
    if not raw.strip():
        return []
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise ValueError(f"{repo_path} must contain a JSON array")
    return parsed


def save(path: Path, items: list[dict]) -> None:
    from scripts.store import StoreConflict
    _token, _owner, _repo, branch = _cfg()
    repo_path = _repo_path(path)
    content = json.dumps(items, indent=2, ensure_ascii=False) + "\n"
    body = {
        "message": f"tracker(phone): update {repo_path}",
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    sha = _cache().get(repo_path)
    if sha is None:
        # TOCTOU: if another commit lands between this _get and the _put below,
        # GitHub still accepts our PUT (the SHA is valid as of this read) and we
        # silently overwrite it. This path only runs when save() is called with no
        # prior load() in the same request; the tracker's routes always load first,
        # so the primary path is the cached-SHA optimistic-concurrency check.
        existing = _get(repo_path)  # may exist from a prior process; None if new file
        if existing and existing.get("sha"):
            sha = existing["sha"]
    if sha is not None:
        body["sha"] = sha
    try:
        result = _put(repo_path, body)
    except GithubStatusError as e:
        if e.status == 409:  # stale sha: a concurrent commit landed first
            raise StoreConflict(repo_path) from e
        raise
    new_sha = (result.get("content") or {}).get("sha")
    if new_sha:
        _cache()[repo_path] = new_sha
