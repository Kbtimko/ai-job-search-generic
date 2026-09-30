"""LinkedIn data-export analysis for the job search.

Reads a subset of a LinkedIn archive export from ``data/linkedin_archive/`` and
writes aggregates to ``output/linkedin/``. Both paths are gitignored: the archive
holds other people's contact details and private message content, and the outputs
carry real names. Neither is ever committed.
"""

import json
import os
from collections.abc import Mapping
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = REPO_ROOT / "data" / "linkedin_archive"
OUTPUT_DIR = REPO_ROOT / "output" / "linkedin"
PROFILE_PATH = REPO_ROOT / "profile" / "master.json"
FALLBACK_OWNER = "Archive Owner"

# The archive owner's display name, exactly as it appears in the export's "From"
# column. Everything that has to tell "me" from "them" -- the inbound/outbound split,
# message_count, reply direction -- compares against this string, so a mismatch
# mis-attributes all three at once rather than failing loudly. One definition, and an
# override parameter on every entry point that uses it.

def resolve_owner(profile_path: Path = PROFILE_PATH, env: Mapping = os.environ) -> str:
    """LINKEDIN_ARCHIVE_OWNER env, else profile basics.linkedin_name, else basics.name."""
    override = (env.get("LINKEDIN_ARCHIVE_OWNER") or "").strip()
    if override:
        return override
    try:
        basics = json.loads(Path(profile_path).read_text(encoding="utf-8")).get("basics", {})
    except (FileNotFoundError, json.JSONDecodeError):
        basics = {}
    return (basics.get("linkedin_name") or basics.get("name") or "").strip() or FALLBACK_OWNER


ARCHIVE_OWNER = resolve_owner()
