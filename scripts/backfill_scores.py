"""One-off backfill: regrade existing jobs under the current search-config criteria.

Re-scores the three subjective sub-scores (differentiation, winnability, domain) via
the Claude API from each job's stored title/excerpt/strengths/gaps — NO web tools, so
none of the daily-scan cost drivers (web_search fees, pause_turn context regrowth) fire.
The deterministic layer (comp, location, title-tier multiplier, negative penalties, the
weighted overall) is reused verbatim from ``scan.apply_scores`` so backfilled scores are
directly comparable to what a live scan produces. Writes via the validated
``store update-jobs`` path. Dry-run by default; pass --write to persist.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import anthropic

from scripts.scan import (
    CONFIG_PATH,
    DATA_PATH,
    EFFORT,
    MAX_TOKENS,
    MODEL,
    PROFILE_PATH,
    build_scoring_rubric,
    prompt_profile,
    UNTRUSTED_CONTENT_RULE,
    apply_scores,
    parse_jobs,
)
from scripts import store

SKIP_STATUSES = {"closed", "dismissed"}


def select_active(jobs: list[dict]) -> list[dict]:
    """Jobs still worth regrading: everything except closed/user-dismissed postings."""
    return [j for j in jobs if j.get("status") not in SKIP_STATUSES]


def chunk(items: list, size: int) -> list[list]:
    """Split ``items`` into consecutive batches of at most ``size``."""
    return [items[i:i + size] for i in range(0, len(items), size)]


def build_batch_prompt(profile: dict, batch: list[dict]) -> str:
    """Prompt to re-score a batch of already-found jobs from their stored text.

    No web tools: the model scores purely from the title/excerpt/strengths/gaps we
    already hold, against the SAME rubric the live scan uses. Output is a JSON array
    keyed by id so results map back to jobs unambiguously.
    """
    profile_json = json.dumps(prompt_profile(profile), ensure_ascii=False)
    postings = "\n\n".join(
        json.dumps(
            {
                "id": j.get("id"),
                "title": j.get("title", ""),
                "description_excerpt": j.get("description_excerpt", ""),
                "prior_strengths": j.get("strengths", []),
                "prior_gaps": j.get("gaps", []),
            },
            ensure_ascii=False,
        )
        for j in batch
    )
    return _BATCH_PROMPT_TEMPLATE.format(
        profile_json=profile_json,
        SCORING_RUBRIC=build_scoring_rubric(profile),
        UNTRUSTED_CONTENT_RULE=UNTRUSTED_CONTENT_RULE,
        postings=postings,
    )


def diff_rows(jobs: list[dict], patches: list[dict]) -> list[dict]:
    """Before→after rows for the dry-run table, sorted by largest score drop first."""
    old_by_id = {j.get("id"): j for j in jobs}
    rows = []
    for p in patches:
        old = old_by_id.get(p["id"], {})
        old_score = old.get("score") or 0
        rows.append({
            "id": p["id"],
            "title": old.get("title", ""),
            "old": old_score,
            "new": p["score"],
            "delta": p["score"] - old_score,
        })
    rows.sort(key=lambda r: r["delta"])
    return rows


def scores_by_id(text: str) -> dict[str, dict]:
    """Parse the model's JSON-array response into a mapping of posting id -> score dict."""
    return {row["id"]: row for row in parse_jobs(text) if row.get("id")}


def build_patches(jobs: list[dict], scores: dict[str, dict],
                  config: dict) -> tuple[list[dict], list[str]]:
    """Turn LLM sub-scores into validated-store patches. Returns (patches, skipped_ids).

    For each job the model scored, seed score_breakdown with its three subjective
    dimensions, then run the SAME apply_scores() the live scan uses so comp/location/
    tier-multiplier/negative-penalties/overall are computed identically. Jobs the model
    did not score (no id match) are skipped, not silently zeroed.
    """
    weights = config.get("score_weights", {})
    patches: list[dict] = []
    skipped: list[str] = []
    for job in jobs:
        s = scores.get(job.get("id"))
        if s is None:
            skipped.append(job.get("id"))
            continue
        seeded = dict(job)
        seeded["score_breakdown"] = {
            "differentiation": s.get("differentiation", 0),
            "winnability": s.get("winnability", 0),
            "domain": s.get("domain", 0),
        }
        scored = apply_scores([seeded], weights, config)[0]
        patches.append({
            "id": job["id"],
            "score": scored["score"],
            "score_breakdown": scored["score_breakdown"],
            "strengths": s.get("strengths", []),
            "gaps": s.get("gaps", []),
        })
    return patches, skipped


_BATCH_PROMPT_TEMPLATE = """You are re-scoring job postings that were already found, against an updated profile/rubric.
Score ONLY from the text provided for each posting — do NOT search or fetch anything.

CANDIDATE PROFILE (JSON):
{profile_json}

RULES:
{UNTRUSTED_CONTENT_RULE}

{SCORING_RUBRIC}

POSTINGS TO SCORE (one JSON object per line; prior_strengths/prior_gaps were written from
the full job description at first scan — use them as extra signal alongside the excerpt):
{postings}

OUTPUT: Return ONLY a JSON array (no prose), one object per posting above, each exactly:
{{"id": "<the posting id, verbatim>", "differentiation": 0-10, "winnability": 0-10, "domain": 0-10, "strengths": ["short bullet"], "gaps": ["short bullet"]}}
Wrap the array in a ```json code fence."""


def call_model(client, prompt: str) -> str:
    """One scoring call — no web tools, so no search fees or pause_turn context regrowth."""
    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        output_config={"effort": EFFORT},
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")


def score_job(client, profile: dict, job: dict, config: dict) -> dict | None:
    """Score a single job from its stored text. Returns the score fields, or None.

    None ONLY when there's no description to score from. API/parse errors PROPAGATE so
    the caller can surface the real reason rather than silently adding an unscored job.
    """
    if not (job.get("description_excerpt") or "").strip():
        return None
    scores = scores_by_id(call_model(client, build_batch_prompt(profile, [job])))
    patches, _ = build_patches([job], scores, config)
    if not patches:
        return None
    p = patches[0]
    return {k: p[k] for k in ("score", "score_breakdown", "strengths", "gaps")}


def run_backfill(client, profile: dict, active: list[dict], batch_size: int) -> dict[str, dict]:
    """Score every active job in id-keyed batches; returns the merged id -> scores map."""
    scores: dict[str, dict] = {}
    batches = chunk(active, batch_size)
    for i, batch in enumerate(batches, 1):
        text = call_model(client, build_batch_prompt(profile, batch))
        try:
            batch_scores = scores_by_id(text)
        except ValueError as exc:
            print(f"  batch {i}/{len(batches)}: UNPARSEABLE ({exc}); skipping", flush=True)
            continue
        scores.update(batch_scores)
        print(f"  batch {i}/{len(batches)}: scored {len(batch_scores)}/{len(batch)}", flush=True)
    return scores


def write_patches_via_store(patches: list[dict], data_path: Path) -> None:
    """Persist patches through the validated `store update-jobs` CLI (forward-only path)."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(patches, f)
        tmp = f.name
    subprocess.run(
        [sys.executable, "-m", "scripts.store", "update-jobs",
         "--data", str(data_path), "--incoming", tmp],
        check=True,
    )


def _print_diff_table(rows: list[dict]) -> None:
    print(f"\n{'delta':>6}  {'old':>3} {'new':>3}  title")
    print("  " + "-" * 60)
    for r in rows:
        print(f"{r['delta']:>+6}  {r['old']:>3} {r['new']:>3}  {r['title'][:48]}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scripts.backfill_scores", description=__doc__)
    ap.add_argument("--write", action="store_true", help="persist changes (default: dry run)")
    ap.add_argument("--batch-size", type=int, default=8, help="jobs per API call (default 8)")
    ap.add_argument("--limit", type=int, default=None, help="only score the first N active jobs")
    ap.add_argument("--data", type=Path, default=DATA_PATH)
    ap.add_argument("--config", type=Path, default=CONFIG_PATH)
    ap.add_argument("--profile", type=Path, default=PROFILE_PATH)
    args = ap.parse_args(argv)

    config = json.loads(args.config.read_text(encoding="utf-8"))
    profile = json.loads(args.profile.read_text(encoding="utf-8")) if args.profile.exists() else {}
    jobs = store.load_store(args.data)
    active = select_active(jobs)
    if args.limit is not None:
        active = active[: args.limit]
    print(f"Regrading {len(active)} active jobs (model={MODEL}, effort={EFFORT}, "
          f"batch={args.batch_size}) ...")

    client = anthropic.Anthropic()
    scores = run_backfill(client, profile, active, args.batch_size)
    patches, skipped = build_patches(active, scores, config)

    rows = diff_rows(active, patches)
    _print_diff_table(rows)
    drops = sum(1 for r in rows if r["delta"] < 0)
    print(f"\n{len(patches)} regraded  |  {drops} down, {len(rows) - drops} up/flat"
          f"  |  {len(skipped)} skipped (no score returned)")
    if skipped:
        print(f"  skipped ids: {', '.join(i for i in skipped if i)}")

    if not args.write:
        print("\nDRY RUN — nothing written. Re-run with --write to persist.")
        return 0
    write_patches_via_store(patches, args.data)
    print(f"\nWrote {len(patches)} patched scores to {args.data}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
