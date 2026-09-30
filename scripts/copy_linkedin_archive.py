"""Copy only the archive files the analysis needs into data/linkedin_archive/.

A full LinkedIn export also contains login IP addresses, payment receipts, ad-click
history and security challenges. None of that is needed here, so none of it is
copied into the repo tree. The copy set is ``ingest.NEEDED_FILES``, which is
deliberately narrower than ``ingest.ARCHIVE_FILES``: the latter is what ``manifest``
reports on, and it includes the saved-jobs files, which nothing in production reads.

Usage:
    python -m scripts.copy_linkedin_archive <path-to-extracted-export>
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from linkedin import ARCHIVE_DIR
from linkedin.ingest import NEEDED_FILES


def main(argv: list[str] | None = None, destination: Path | None = None) -> int:
    """Stage ``NEEDED_FILES`` from an extracted export into ``destination``.

    ``destination`` defaults to the real ``ARCHIVE_DIR`` and exists so the tests that
    assert *which* files cross the privacy boundary can do so without writing into the
    repo's data/ directory or reading anyone's actual export.
    """
    argv = sys.argv[1:] if argv is None else argv
    destination = ARCHIVE_DIR if destination is None else Path(destination)
    if len(argv) != 1:
        print(__doc__)
        return 2

    source = Path(argv[0]).expanduser()
    if not source.is_dir():
        print(f"not a directory: {source}")
        return 1

    destination.mkdir(parents=True, exist_ok=True)
    copied, missing = [], []
    for name in NEEDED_FILES:
        # Jobs files live in a Jobs/ subdirectory; the rest sit at the top level.
        found = next(iter(source.glob(name)), None) or next(iter(source.glob(f"*/{name}")), None)
        if found is None:
            missing.append(name)
            continue
        shutil.copy2(found, destination / name)
        copied.append(name)

    print(f"copied {len(copied)} file(s) to {destination}")
    for name in copied:
        print(f"  + {name}")
    if missing:
        print(f"not found in export (may be absent from this account): {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
