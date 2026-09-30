"""Deterministic 'is this posting still open?' checker for data/jobs.json (no LLM, $0).

HTTP-only and conservative: a posting is only marked closed on a 404/410 or an explicit
dead phrase in the page body. Login walls, errors, and timeouts are left 'unknown'.
"""
from __future__ import annotations

import datetime
import urllib.error
import urllib.request
from pathlib import Path

from scripts import store

REPO_ROOT = Path(__file__).resolve().parent.parent
JOBS_PATH = REPO_ROOT / "data" / "jobs.json"

DEAD_PHRASES = [
    "no longer accepting",
    "position has been filled",
    "no longer available",
    "job posting closed",
]

_ELIGIBLE = {"new", "interested"}
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def classify_posting(status_code: int | None, body: str) -> str:
    """Return 'open' | 'closed' | 'unknown' from an HTTP result. Conservative."""
    if status_code in (404, 410):
        return "closed"
    if status_code == 200:
        text = body.lower()
        if any(phrase in text for phrase in DEAD_PHRASES):
            return "closed"
        return "open"
    return "unknown"


def fetch(url: str, timeout: int = 10) -> tuple[int | None, str]:
    """GET url. Returns (status_code, body). Network errors -> (None, "").

    HTTPError carries a status code (used for 404/410); URLError/timeout -> None.
    """
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(200_000)  # cap body; enough to spot dead phrases
            return resp.status, raw.decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception:
        return None, ""


def main(jobs_path: Path = JOBS_PATH, today: str | None = None) -> int:
    """Check eligible postings and persist status/posting_checked_date patches.

    Eligible = has a url and status in {new, interested} (no-clobber of applied/
    dismissed/closed). Stamps posting_checked_date on every checked job; sets
    status='closed' only where classified closed. Returns process exit code.
    """
    today = today or datetime.date.today().isoformat()
    jobs = store.load_store(jobs_path)

    patches: list[dict] = []
    closed_titles: list[str] = []
    checked = closed = unknown = 0
    for job in jobs:
        url = (job.get("url") or "").strip()
        if not url or job.get("status") not in _ELIGIBLE:
            continue
        checked += 1
        verdict = classify_posting(*fetch(url))
        patch = {"id": job["id"], "posting_checked_date": today}
        if verdict == "closed":
            patch["status"] = "closed"
            closed += 1
            closed_titles.append(f"{job.get('company', '?')} — {job.get('title', '?')}")
        elif verdict == "unknown":
            unknown += 1
        patches.append(patch)

    merged, _ = store.update_jobs(jobs, patches)
    patched_ids = {p["id"] for p in patches}
    from jsonschema import ValidationError
    for j in merged:
        if j.get("id") not in patched_ids:
            continue  # only validate jobs we touched; leave pre-existing data alone
        try:
            store.validate_item(j, "jobs")
        except ValidationError as exc:
            print(f"error: resulting job invalid: {exc.message}", flush=True)
            return 1
    store.save_store(jobs_path, merged)

    print(f"Openness check: checked {checked} | closed {closed} | unknown {unknown}")
    for title in closed_titles:
        print(f"  closed: {title}")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main())
