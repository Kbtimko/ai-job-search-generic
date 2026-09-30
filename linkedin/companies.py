"""Match free-text company strings to the companies in the job store.

The load-bearing piece: every warm-intro join depends on it. Borderline matches are
never auto-accepted -- they go to a review file for a human to confirm.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz

# All matching thresholds live here, not scattered as literals.
MATCH_CONFIG = {
    "auto_accept": 92,   # >= this: accepted without review
    "review_floor": 80,  # [review_floor, auto_accept): written to match_review.csv
}

# ``token_set_ratio`` returns exactly 100 when one side's tokens are a subset of the
# other's, so "Better" scores 100 against "Better Sleep" and auto-accepts without ever
# reaching the review band. A subset is only trustworthy when the shorter side is
# specific enough to identify a company on its own; one token usually is not, because
# single-word company names ("Better", "Toast", "Apex", "Sierra") are common English
# words. Two shared tokens ("Omnicom Media" in "Omnicom Media Group") are specific
# enough to keep. Hence: demote a subset match to review when the shorter side has at
# most this many tokens.
AMBIGUOUS_SUBSET_TOKENS = 1

_LEGAL_SUFFIXES = {
    "inc", "llc", "ltd", "corp", "corporation", "co", "plc",
    "gmbh", "sa", "nv", "ag", "bv", "the",
}


def normalize(name: str | None) -> str:
    """Lowercase, drop legal suffixes and punctuation, collapse whitespace."""
    text = (name or "").strip().lower()
    text = re.sub(r"\s*\([^)]*\)\s*$", "", text)  # trailing parenthetical
    text = re.sub(r"[^a-z0-9]+", " ", text)
    words = [w for w in text.split() if w and w not in _LEGAL_SUFFIXES]
    return " ".join(words)


@dataclass(frozen=True)
class Match:
    """Outcome of matching one source string against the target list.

    ``matched`` is set only for accepted matches. Review-band results carry the
    suggestion in ``candidate`` and leave ``matched`` None, so a caller that reads
    only ``matched`` can never accidentally consume an unconfirmed match.
    """

    source: str
    matched: str | None
    candidate: str | None
    score: float
    band: str  # "alias" | "auto" | "review" | "none"


def load_aliases(path: Path | None = None) -> dict:
    """Load the hand-maintained variant -> canonical map.

    Keys are normalized on load, because ``match_company`` looks them up by the
    normalized source. The file is meant to be hand-edited, so a key written the
    natural way ("Northwind Traders Inc.") has to work rather than silently never
    matching. Values are left verbatim -- they are the canonical output name.
    """
    path = path or Path(__file__).with_name("aliases.json")
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8")).get("aliases", {})
    return {normalize(k): v for k, v in raw.items() if normalize(k)}


def _is_ambiguous_subset(normalized_source: str, normalized_target: str) -> bool:
    """True when one side's tokens are a strict subset of the other's and the shorter
    side is too generic to trust -- see ``AMBIGUOUS_SUBSET_TOKENS``."""
    source_tokens = set(normalized_source.split())
    target_tokens = set(normalized_target.split())
    if source_tokens == target_tokens:
        return False
    if not (source_tokens < target_tokens or target_tokens < source_tokens):
        return False
    return min(len(source_tokens), len(target_tokens)) <= AMBIGUOUS_SUBSET_TOKENS


def match_company(source: str, targets: list[str], aliases: dict | None = None) -> Match:
    """Match one company string against ``targets`` using normalized token-set ratio."""
    aliases = aliases or {}
    normalized = normalize(source)
    if not normalized:
        return Match(source, None, None, 0.0, "none")

    # The subset demotion below deliberately does NOT apply here: an alias entry is
    # itself the human confirmation the review band exists to collect, and mapping a
    # generic single token onto a longer name is the alias file's main use case.
    alias_target = aliases.get(normalized)
    if alias_target:
        return Match(source, alias_target, alias_target, 100.0, "alias")

    best, best_score = None, 0.0
    for target in targets:
        score = fuzz.token_set_ratio(normalized, normalize(target))
        if score > best_score:
            best, best_score = target, score

    if best_score >= MATCH_CONFIG["auto_accept"] and not _is_ambiguous_subset(
        normalized, normalize(best)
    ):
        return Match(source, best, best, best_score, "auto")
    if best_score >= MATCH_CONFIG["review_floor"]:
        # Deliberately leaves `matched` unset: needs a human before it is used.
        return Match(source, None, best, best_score, "review")
    return Match(source, None, None, best_score, "none")


def build_match_report(sources: list[str], targets: list[str],
                       aliases: dict | None = None) -> dict:
    """Summarize a matching run.

    Reports two independent numbers that must not be conflated:

    - ``precision``: of matches that landed in a band, the share auto-accepted.
      A low value means the matcher is struggling and is worth halting on.
    - ``coverage``: the share of targets that got at least one match. A low value
      is a fact about the data (few connections at these companies), NOT a defect,
      and must never gate the run.
    """
    matches = [match_company(s, targets, aliases) for s in sources]
    counts = {"alias": 0, "auto": 0, "review": 0, "none": 0}
    for match in matches:
        counts[match.band] += 1

    accepted = counts["auto"] + counts["alias"]
    banded = accepted + counts["review"]
    # An alias may map onto anything, including a name that is not a target at all.
    # Intersect so coverage cannot exceed 1.0, and de-duplicate the denominator so a
    # repeated target does not deflate it.
    distinct_targets = set(targets)
    covered = {m.matched for m in matches if m.matched} & distinct_targets
    return {
        "attempted": len(sources),
        **counts,
        "precision": (accepted / banded) if banded else 0.0,
        "coverage": (len(covered) / len(distinct_targets)) if distinct_targets else 0.0,
        "matches": matches,
    }
