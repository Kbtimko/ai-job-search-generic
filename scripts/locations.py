"""Configured job locations — the single source for location keys, labels and scores.

Every consumer (scan prompt, deterministic location score, digest buckets, tracker
filters, store CLI) reads locations through here, so a user's metros live only in
config/search-config.json. "other" is always a valid key: a posting outside every
configured location is kept and scored as other, never dropped.
"""
from __future__ import annotations

import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "search-config.json"
OTHER = {"key": "other", "label": "Other", "match": "anywhere else", "score": 0}


def load_config(path: Path = CONFIG_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def entries(config: dict) -> list[dict]:
    """Configured locations in config order, then the (implicit or configured) 'other'."""
    raw = config.get("locations", [])
    for loc in raw:
        if not isinstance(loc, dict) or "key" not in loc:
            raise ValueError(
                "config 'locations' entries must be objects with key/label/score "
                f"(got {loc!r}); see profile/README.md or rerun /setup search")
    configured = [loc for loc in raw if loc["key"] != "other"]
    other = next((loc for loc in raw if loc["key"] == "other"), OTHER)
    return configured + [other]


def keys(config: dict) -> list[str]:
    return [e["key"] for e in entries(config)]


def score(location_type: str | None, config: dict) -> int:
    by_key = {e["key"]: e.get("score", 0) for e in entries(config)}
    return by_key.get(location_type, by_key["other"])


def normalize_type(location_type: str | None, config: dict) -> str:
    return location_type if location_type in keys(config) else "other"


def check_type(location_type: str, config: dict) -> None:
    valid = keys(config)
    if location_type not in valid:
        raise ValueError(f"unknown location_type {location_type!r}; valid: {', '.join(valid)}")


def _match(entry: dict) -> str:
    if entry.get("match"):
        return entry["match"]
    return "US-remote" if entry["key"] == "remote" else f"{entry['label']} metro"


def prompt_line(config: dict) -> str:
    return ", ".join(f"{e['label']} ({_match(e)})"
                     for e in entries(config) if e["key"] != "other")
