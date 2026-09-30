"""Workstream 2 -- what titles the market pitches, and reply rates in both directions.

"Outbound" here means threads I sent the first message in, NOT cold outreach: the
archive cannot distinguish a cold approach from a note to a former colleague. See
``OUTBOUND_CAVEAT``.

Classification is rule-based and its precision is unmeasured. ``classifier_sample.csv``
exists so that precision can be eyeballed before deciding whether to spend API budget
on a model-based classifier. Results are cached by message content AND a fingerprint
of the rules that produced them, so reruns stay free while swapping in that classifier
really is a drop-in: entries made under the old rules are discarded rather than served.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path

from linkedin import ARCHIVE_OWNER

# Signals that a message is recruiter outreach. Each is reported when it fires, so a
# reviewer can see why a message was classified the way it was.
RECRUITER_SIGNALS = {
    "recruiter": r"\brecruit(er|ing|ment)?\b",
    "opportunity": r"\bopportunit(y|ies)\b",
    "role": r"\b(role|position|opening|req|vacancy)\b",
    "hiring": r"\b(hiring|we're hiring|talent acquisition)\b",
    "reach_out": r"\breach(ing)? out\b",
    "open_to": r"\bopen to\b.{0,30}\b(new|opportunit|role|conversation|chat)\b",
    "candidate": r"\b(candidate|your background|your profile|your experience)\b",
    "resume": r"\b(resume|cv)\b",
}

# How many distinct signals must fire before a message counts as recruiter outreach.
# Two, not one, because "role" alone fires on plenty of ordinary conversation.
RECRUITER_SIGNAL_THRESHOLD = 2

# DOTALL because message bodies are multi-line: a signal phrase and its qualifier
# routinely sit either side of a line break.
_COMPILED = {name: re.compile(pattern, re.IGNORECASE | re.DOTALL)
             for name, pattern in RECRUITER_SIGNALS.items()}


def message_hash(content: str) -> str:
    """Stable cache key over message content only -- never names or contact details."""
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()[:16]


def classify_message(content: str) -> dict:
    """Rule-based recruiter-outreach classification."""
    text = (content or "").strip()
    if not text:
        return {"is_recruiter": False, "signals": []}
    signals = [name for name, pattern in _COMPILED.items() if pattern.search(text)]
    return {
        "is_recruiter": len(signals) >= RECRUITER_SIGNAL_THRESHOLD,
        "signals": signals,
    }


def rules_version() -> str:
    """Fingerprint of the rules a cached verdict was produced by.

    A content hash identifies the message, not the answer. Without this the cache
    could never be invalidated: change a signal pattern or the threshold and every
    message ever seen would keep its old verdict forever -- including under a
    model-based classifier, which would then be a no-op on the whole existing corpus.
    Read at call time, not frozen at import, so a rules change takes effect at once.
    """
    payload = json.dumps(
        {"signals": RECRUITER_SIGNALS, "threshold": RECRUITER_SIGNAL_THRESHOLD},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def classify_all(messages: list[dict], cache_path: Path) -> list[dict]:
    """Classify messages, reading and writing a content-and-rules keyed cache."""
    cache_path = Path(cache_path)
    version = rules_version()
    cache: dict = {}
    if cache_path.exists():
        stored = json.loads(cache_path.read_text(encoding="utf-8"))
        # Entries made under different rules are dropped, not migrated: they are
        # answers to a different question. A cache with no fingerprint at all predates
        # versioning and cannot be attributed to any ruleset, so it goes the same way.
        if isinstance(stored, dict) and stored.get("rules_version") == version:
            cache = stored.get("entries", {})

    results = []
    for message in messages:
        key = message_hash(message.get("content", ""))
        if key not in cache:
            cache[key] = classify_message(message.get("content", ""))
        results.append({**message, **cache[key]})

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({"rules_version": version, "entries": cache}, indent=2),
        encoding="utf-8",
    )
    return results


def load_buckets(path: Path | None = None) -> dict:
    path = path or Path(__file__).with_name("buckets.json")
    return json.loads(path.read_text(encoding="utf-8"))


def bucket_title(title: str | None, config: dict | None = None) -> str:
    """Map a role title to a configured bucket. First match in file order wins."""
    config = config or load_buckets()
    text = (title or "").strip().lower()
    if not text:
        return config.get("default", "other")
    for bucket in config["buckets"]:
        if any(pattern in text for pattern in bucket["patterns"]):
            return bucket["key"]
    return config.get("default", "other")


# Capture a Title-Cased role phrase sitting next to a role noun. The separators are
# \s-based so they cross the line breaks that 1 message in 4 contains.
_TITLE_PATTERNS = [
    re.compile(r"\b(?:a|an|the)\s+([A-Z][\w&/,-]*(?:\s+(?:of|and|&|for|to)?\s*[A-Z][\w&/,-]*){0,5})"
               r"\s+(?:role|position|opportunity|opening|req)\b"),
    re.compile(r"\b(?:for|about|regarding)\s+(?:a|an|the)\s+"
               r"([A-Z][\w&/,-]*(?:\s+(?:of|and|&|for|to)?\s*[A-Z][\w&/,-]*){0,5})\b"),
]


def extract_title(content: str) -> str | None:
    """Pull a role title out of a message body.

    Best-effort string matching over free text. It misses lowercase and unusual
    phrasings; the caveat is restated in the output files.
    """
    text = (content or "").strip()
    if not text:
        return None
    for pattern in _TITLE_PATTERNS:
        match = pattern.search(text)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" ,")
    return None


def thread_reply_stats(messages: list[dict], me: str = ARCHIVE_OWNER) -> dict:
    """Per-conversation reply outcome and latency, in both directions."""
    threads: dict[str, list[dict]] = {}
    for message in messages:
        threads.setdefault(message["conversation_id"], []).append(message)

    stats = {}
    for conversation_id, thread in threads.items():
        ordered = sorted(thread, key=lambda m: m.get("date_parsed") or date.min)
        first = ordered[0]
        opener = (first.get("from") or "").strip()
        inbound_first = opener != me
        # The reply is simply the first message from anyone other than the opener.
        reply = next(
            (m for m in ordered[1:] if (m.get("from") or "").strip() != opener), None
        )
        latency = None
        if reply and reply.get("date_parsed") and first.get("date_parsed"):
            latency = (reply["date_parsed"] - first["date_parsed"]).days
        stats[conversation_id] = {
            "inbound_first": inbound_first,
            "i_replied": bool(reply) if inbound_first else False,
            "they_replied": bool(reply) if not inbound_first else False,
            "reply_latency_days": latency,
            "opened_on": first.get("date_parsed"),
            "message_count": len(ordered),
        }
    return stats


CAVEAT = (
    "# Recruiter classification is rule-based and its precision is UNMEASURED. "
    "Review classifier_sample.csv before trusting these rates. Title extraction is "
    "best-effort string matching and misses unusual phrasings.\n"
)

# outbound_reply_rate.csv counts threads I opened, which is not the same thing as cold
# outreach and reads about ten times better than it. The number is right; the obvious
# reading of it is wrong, so the correction ships inside the file.
OUTBOUND_CAVEAT = (
    "# NOT COLD OUTREACH. This counts threads where I sent the first message, whoever\n"
    "# the other person was. Most of them are people I already know -- former\n"
    "# colleagues, friends, people I had just met -- which is why the reply rate is so\n"
    "# high. The archive cannot tell a cold approach from a note to a close colleague,\n"
    "# so this is not a cold-outreach metric and must not be read as one.\n"
)

# Caveats that apply to one file only, written after the shared CAVEAT.
EXTRA_CAVEATS = {"outbound_reply_rate.csv": OUTBOUND_CAVEAT}

SAMPLE_SIZE = 40

OUTPUT_FILES = [
    "inbound_titles_by_year.csv", "inbound_reply_rate.csv",
    "outbound_reply_rate.csv", "classifier_sample.csv",
]


def _write_with_caveat(path: Path, columns: list[str], rows: list[dict]) -> None:
    """Write one of this module's CSVs, led by its caveats.

    Every file here rests on an unmeasured classifier, so ``CAVEAT`` goes on all of
    them. A file whose headline number invites a specific wrong reading gets a second
    caveat naming it, appended after the shared one.
    """
    from linkedin.output import write_csv

    write_csv(path, columns, rows, CAVEAT + EXTRA_CAVEATS.get(Path(path).name, ""))


def run(archive_dir: Path, output_dir: Path, me: str = ARCHIVE_OWNER) -> dict:
    """Run workstream 2 and write its four output files."""
    from linkedin.ingest import load_messages

    output_dir = Path(output_dir)
    messages = load_messages(archive_dir)
    for message in messages:
        message["conversation_id"] = message.get("conversation_id", "")

    inbound = [m for m in messages if (m.get("from") or "").strip() != me]
    classified = classify_all(inbound, output_dir / "classification_cache.json")
    recruiter = [m for m in classified if m["is_recruiter"]]

    buckets = load_buckets()
    for message in recruiter:
        message["extracted_title"] = extract_title(message.get("content", "")) or ""
        message["title_bucket"] = bucket_title(message["extracted_title"], buckets)

    # bucket x year counts
    years = sorted({str(m["date_parsed"].year) for m in recruiter if m.get("date_parsed")})
    counts: dict[str, dict[str, int]] = {}
    for message in recruiter:
        if not message.get("date_parsed"):
            continue
        row = counts.setdefault(message["title_bucket"], {y: 0 for y in years})
        row[str(message["date_parsed"].year)] += 1
    _write_with_caveat(
        output_dir / "inbound_titles_by_year.csv",
        ["title_bucket", *years],
        [{"title_bucket": bucket, **year_counts} for bucket, year_counts in sorted(counts.items())],
    )

    stats = thread_reply_stats(messages, me=me)
    bucket_by_thread = {m["conversation_id"]: m["title_bucket"] for m in recruiter}

    inbound_rows: dict[str, dict] = {}
    for conversation_id, stat in stats.items():
        if not stat["inbound_first"] or conversation_id not in bucket_by_thread:
            continue
        row = inbound_rows.setdefault(
            bucket_by_thread[conversation_id],
            {"title_bucket": bucket_by_thread[conversation_id], "threads": 0, "replied": 0},
        )
        row["threads"] += 1
        row["replied"] += int(stat["i_replied"])
    for row in inbound_rows.values():
        row["reply_rate"] = round(row["replied"] / row["threads"], 3) if row["threads"] else 0.0
    _write_with_caveat(output_dir / "inbound_reply_rate.csv",
                       ["title_bucket", "threads", "replied", "reply_rate"],
                       sorted(inbound_rows.values(), key=lambda r: r["title_bucket"]))

    outbound_rows: dict[str, dict] = {}
    for stat in stats.values():
        if stat["inbound_first"] or not stat["opened_on"]:
            continue
        year = str(stat["opened_on"].year)
        row = outbound_rows.setdefault(year, {"year": year, "threads": 0, "replied": 0})
        row["threads"] += 1
        row["replied"] += int(stat["they_replied"])
    for row in outbound_rows.values():
        row["reply_rate"] = round(row["replied"] / row["threads"], 3) if row["threads"] else 0.0
    _write_with_caveat(output_dir / "outbound_reply_rate.csv",
                       ["year", "threads", "replied", "reply_rate"],
                       sorted(outbound_rows.values(), key=lambda r: r["year"]))

    # Precision-review sample: content is included because reviewing without it is
    # impossible. output/ is gitignored, so this never leaves the machine.
    sample = [
        {"is_recruiter": m["is_recruiter"], "signals": "|".join(m["signals"]),
         "extracted_title": m.get("extracted_title", ""),
         "content": (m.get("content", "") or "")[:300]}
        for m in classified[:SAMPLE_SIZE]
    ]
    _write_with_caveat(output_dir / "classifier_sample.csv",
                       ["is_recruiter", "signals", "extracted_title", "content"], sample)

    return {
        "inbound_total": len(inbound),
        "recruiter_total": len(recruiter),
        "files_written": list(OUTPUT_FILES),
    }
