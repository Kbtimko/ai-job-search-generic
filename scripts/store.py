"""Load, validate, and append-with-dedupe JSON data stores for the job search assistant."""
from __future__ import annotations

import copy
import importlib
import json
import os
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"


class StoreConflict(RuntimeError):
    """Raised when a write's base SHA is stale (a concurrent commit landed first)."""


def _use_github() -> bool:
    return os.environ.get("STORE_BACKEND") == "github"


def load_store(path: Path) -> list[dict]:
    """Return the list stored at path, or [] if missing/empty."""
    if _use_github():
        github_store = importlib.import_module("scripts.github_store")
        return github_store.load(Path(path))
    return _local_load(Path(path))


def save_store(path: Path, items: list[dict]) -> None:
    """Persist items as pretty JSON (local FS, or a GitHub commit)."""
    if _use_github():
        github_store = importlib.import_module("scripts.github_store")
        github_store.save(Path(path), items)
        return
    _local_save(Path(path), items)


def _local_load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return []
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON array, got {type(data).__name__}")
    return data


def _local_save(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def validate_item(item: dict, schema_name: str) -> None:
    """Validate a single item against schema/<schema_name>.schema.json.

    For array-store schemas, validates against the schema's ``items`` subschema.
    Enforces ``format`` (e.g. ISO ``date``). Raises jsonschema.ValidationError on failure.
    """
    schema_path = SCHEMA_DIR / f"{schema_name}.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    item_schema = schema.get("items", schema)
    validator = Draft202012Validator(item_schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    validator.validate(item)


def job_key(job: dict) -> tuple[str, str]:
    """Dedupe keys for a job: (normalized url, "company::title").

    The composite key is empty when both company and title are blank, so
    url-only jobs are deduped on url alone rather than colliding on "::".
    """
    url = (job.get("url") or "").strip().lower().rstrip("/")
    company = (job.get("company") or "").strip().lower()
    title = (job.get("title") or "").strip().lower()
    composite = f"{company}::{title}" if (company or title) else ""
    return url, composite


def append_jobs(existing: list[dict], incoming: list[dict]) -> tuple[list[dict], int]:
    """Append incoming jobs absent from existing (match on url OR company+title).

    Also dedupes within ``incoming``. Returns (merged_list, added_count).
    """
    seen_urls: set[str] = set()
    seen_ct: set[str] = set()
    for j in existing:
        url, ct = job_key(j)
        if url:
            seen_urls.add(url)
        if ct:
            seen_ct.add(ct)

    merged = list(existing)
    added = 0
    for j in incoming:
        url, ct = job_key(j)
        if (url and url in seen_urls) or (ct and ct in seen_ct):
            continue
        merged.append(j)
        added += 1
        if url:
            seen_urls.add(url)
        if ct:
            seen_ct.add(ct)
    return merged, added


def update_jobs(existing: list[dict], patches: list[dict]) -> tuple[list[dict], int]:
    """Patch existing jobs by ``id`` (shallow field merge). Returns (merged, changed).

    Each patch is ``{"id": <str>, ...fields}``. Patches whose id matches no job are
    skipped (not counted). Does not mutate ``existing``.
    """
    merged = [copy.deepcopy(j) for j in existing]
    by_id = {j.get("id"): j for j in merged}
    changed = 0
    for patch in patches:
        target = by_id.get(patch.get("id"))
        if target is None:
            continue
        for key, value in patch.items():
            if key == "id":
                continue
            target[key] = value
        changed += 1
    return merged, changed


STAGE_ORDER = {"draft": 0, "applied": 1, "screening": 2, "interview": 3, "offer": 4}
TERMINAL_STAGES = {"rejected", "withdrawn"}


def application_key(app: dict) -> str:
    """Dedupe key: job_id if present, else normalized 'company::title'."""
    jid = (app.get("job_id") or "").strip()
    if jid:
        return jid
    company = (app.get("company") or "").strip().lower()
    title = (app.get("title") or "").strip().lower()
    return f"{company}::{title}"


def is_forward_transition(current: str, candidate: str) -> bool:
    """True if moving current->candidate is a valid forward move.

    Terminal stages (rejected/withdrawn) are reachable from any non-terminal
    stage; nothing leaves a terminal stage. Otherwise candidate must rank higher.
    """
    if current in TERMINAL_STAGES:
        return False
    if candidate in TERMINAL_STAGES:
        return True
    return STAGE_ORDER.get(candidate, -1) > STAGE_ORDER.get(current, -1)


def upsert_application(existing: list[dict], incoming: dict, today: str) -> tuple[list[dict], bool]:
    """Advance the matching application (or append a new one). Returns (merged, changed).

    incoming: {company, stage, source, [title, job_id, evidence, artifacts_path]}.
    Only forward transitions apply; backward/equal are no-ops (changed=False).
    """
    key = application_key(incoming)
    merged = [copy.deepcopy(a) for a in existing]

    def _hist_entry() -> dict:
        entry = {"stage": incoming["stage"], "date": today,
                 "source": incoming.get("source", "manual")}
        if incoming.get("evidence"):
            entry["evidence"] = incoming["evidence"]
        return entry

    for app in merged:
        if application_key(app) == key:
            if not is_forward_transition(app.get("stage", "applied"), incoming["stage"]):
                return merged, False
            app["stage"] = incoming["stage"]
            if incoming["stage"] == "applied" and not app.get("applied_date"):
                app["applied_date"] = today
            app.setdefault("stage_history", []).append(_hist_entry())
            if incoming.get("artifacts_path"):
                app["artifacts_path"] = incoming["artifacts_path"]
            return merged, True

    stage = incoming["stage"]
    new_app = {
        "company": incoming.get("company", ""),
        "title": incoming.get("title", ""),
        "stage": stage,
        "applied_date": today if STAGE_ORDER.get(stage, 0) >= STAGE_ORDER["applied"] else None,
        "stage_history": [_hist_entry()],
    }
    if incoming.get("job_id"):
        new_app["job_id"] = incoming["job_id"]
    if incoming.get("artifacts_path"):
        new_app["artifacts_path"] = incoming["artifacts_path"]
    merged.append(new_app)
    return merged, True


PATCHABLE_FIELDS = {"notes", "next_action", "due_date"}


def patch_application(existing: list[dict], key: str, fields: dict) -> tuple[list[dict], bool]:
    """Set metadata fields on the application matching ``key``. Returns (merged, changed).

    Only ``notes`` / ``next_action`` / ``due_date`` are applied; anything else (including
    ``stage`` — that must go through ``upsert_application``) is ignored. Unknown key is a
    no-op. Does not mutate ``existing``.
    """
    merged = [copy.deepcopy(a) for a in existing]
    for app in merged:
        if application_key(app) == key:
            for name, value in fields.items():
                if name in PATCHABLE_FIELDS:
                    app[name] = value
            return merged, True
    return merged, False


def revert_application(existing: list[dict], key: str) -> tuple[list[dict], bool]:
    """Undo the last stage change for the application matching ``key``.

    Pops the most recent ``stage_history`` entry and sets ``stage`` back to the now-latest
    entry. No-op (``changed=False``) if there are fewer than 2 entries (can't undo the
    creation entry) or the key matches nothing. Clears ``applied_date`` when the reverted-to
    stage is ``draft``. Does not mutate ``existing``.

    Manual-only: kept separate from ``upsert_application`` so the automated pipeline stays
    forward-only.
    """
    merged = [copy.deepcopy(a) for a in existing]
    for app in merged:
        if application_key(app) == key:
            hist = app.get("stage_history") or []
            if len(hist) < 2:
                return merged, False
            hist.pop()
            app["stage_history"] = hist
            app["stage"] = hist[-1]["stage"]
            if app["stage"] == "draft":
                app["applied_date"] = None
            return merged, True
    return merged, False


def _invalid_location(jobs: list[dict], config_path: Path | None = None) -> str | None:
    """First location_type error among `jobs` vs the configured locations, else None.

    Reads ``config_path`` (the CLI's --config), defaulting to locations.CONFIG_PATH at call time.
    """
    from scripts import locations
    path = Path(config_path) if config_path else locations.CONFIG_PATH
    if not path.exists():
        return None
    config = locations.load_config(path)
    for job in jobs:
        if "location_type" in job:
            try:
                locations.check_type(job["location_type"], config)
            except ValueError as exc:
                return f"invalid job {job.get('id', '<no id>')}: {exc}"
    return None


def _cli(argv: list[str] | None = None) -> int:
    import argparse
    from jsonschema import ValidationError

    parser = argparse.ArgumentParser(prog="scripts.store")
    parser.add_argument("--config", type=Path, default=None,
                        help="search config used to validate location_type "
                             "(default: config/search-config.json)")
    sub = parser.add_subparsers(dest="command", required=True)
    ap = sub.add_parser("append-jobs", help="validate + dedupe-append jobs into a data file")
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument("--incoming", required=True, type=Path)

    up = sub.add_parser("upsert-applications", help="upsert applications (forward-only) into a data file")
    up.add_argument("--data", required=True, type=Path)
    up.add_argument("--incoming", required=True, type=Path)

    uj = sub.add_parser("update-jobs", help="patch existing jobs by id (e.g. mark closed)")
    uj.add_argument("--data", required=True, type=Path)
    uj.add_argument("--incoming", required=True, type=Path)

    args = parser.parse_args(argv)

    if args.command == "append-jobs":
        try:
            incoming = json.loads(args.incoming.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"error: --incoming is not valid JSON: {exc}", flush=True)
            return 2
        if not isinstance(incoming, list):
            print("error: --incoming must be a JSON array", flush=True)
            return 2
        for job in incoming:
            try:
                validate_item(job, "jobs")
            except ValidationError as exc:
                print(f"error: invalid job {job.get('id', '<no id>')}: {exc.message}", flush=True)
                return 1
        loc_err = _invalid_location(incoming, args.config)
        if loc_err:
            print(f"error: {loc_err}", flush=True)
            return 1
        existing = load_store(args.data)
        merged, added = append_jobs(existing, incoming)
        save_store(args.data, merged)
        print(f"Added {added} new jobs ({len(incoming) - added} duplicates skipped); total {len(merged)}.")
        return 0

    if args.command == "upsert-applications":
        import datetime
        today = datetime.date.today().isoformat()
        try:
            incoming = json.loads(args.incoming.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"error: --incoming is not valid JSON: {exc}", flush=True)
            return 2
        if not isinstance(incoming, list):
            print("error: --incoming must be a JSON array", flush=True)
            return 2
        for i, inc in enumerate(incoming):
            if not isinstance(inc, dict):
                print(f"error: incoming item {i} is not an object", flush=True)
                return 2
        apps = load_store(args.data)
        changed = 0
        for inc in incoming:
            apps, did = upsert_application(apps, inc, today)
            changed += int(did)
        for app in apps:
            try:
                validate_item(app, "applications")
            except ValidationError as exc:
                print(f"error: resulting application invalid: {exc.message}", flush=True)
                return 1
        save_store(args.data, apps)
        print(f"Applied {changed} change(s); {len(apps)} applications total.")
        return 0

    if args.command == "update-jobs":
        try:
            incoming = json.loads(args.incoming.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"error: --incoming is not valid JSON: {exc}", flush=True)
            return 2
        if not isinstance(incoming, list):
            print("error: --incoming must be a JSON array", flush=True)
            return 2
        jobs = load_store(args.data)
        merged, changed = update_jobs(jobs, incoming)
        for job in merged:
            try:
                validate_item(job, "jobs")
            except ValidationError as exc:
                print(f"error: resulting job invalid: {exc.message}", flush=True)
                return 1
        patched_ids = {p.get("id") for p in incoming if isinstance(p, dict)}
        loc_err = _invalid_location([j for j in merged if j.get("id") in patched_ids],
                                    args.config)
        if loc_err:
            print(f"error: {loc_err}", flush=True)
            return 1
        save_store(args.data, merged)
        print(f"Patched {changed} job(s); {len(merged)} jobs total.")
        return 0

    return 2


if __name__ == "__main__":
    import sys
    raise SystemExit(_cli(sys.argv[1:]))
