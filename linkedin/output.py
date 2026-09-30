"""The one CSV writer for every file this package produces.

Three modules used to write their CSVs three different ways, and the copy that had
no caveat parameter is exactly the one that shipped five uncaveated files. So the
caveat is a required argument here: a new output file cannot omit its warning by
forgetting to opt in, only by deliberately writing one that says nothing.
"""

from __future__ import annotations

import csv
from pathlib import Path


def write_csv(path: Path, columns: list[str], rows: list[dict], caveat: str) -> None:
    """Write ``rows`` to ``path`` with ``caveat`` as leading ``#`` comment lines.

    Every metric in this package rests on a proxy of some kind, so the warning has
    to travel with the data rather than live in a docstring the reader of a CSV will
    never see. Consumers must skip leading ``#`` lines.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        handle.write(caveat)
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
