"""Vercel Python entrypoint: exposes the Flask WSGI app as `app`."""
import sys
from pathlib import Path

# Ensure the repo root is on sys.path regardless of the runtime's cwd.
_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from scripts.tracker import create_app  # noqa: E402

app = create_app()
