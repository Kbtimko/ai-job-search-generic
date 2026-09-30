"""Command line entry point. One subcommand per workstream.

Each workstream runs independently; none requires another to have run first.

    python -m linkedin.cli manifest
    python -m linkedin.cli warm-intros
    python -m linkedin.cli inbound
    python -m linkedin.cli funnel
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from linkedin import ARCHIVE_DIR, OUTPUT_DIR

DEFAULT_JOBS = Path(__file__).resolve().parent.parent / "data" / "jobs.json"

COMMANDS = ("manifest", "warm-intros", "inbound", "funnel")


def _load_jobs(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _build_parser() -> argparse.ArgumentParser:
    # The shared options hang off each subparser, not the top-level parser: argparse
    # hands everything after the subcommand name to the subparser, so `warm-intros
    # --archive X` only resolves if the subparser owns --archive.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--archive", default=str(ARCHIVE_DIR))
    common.add_argument("--output", default=str(OUTPUT_DIR))
    common.add_argument("--jobs", default=str(DEFAULT_JOBS))

    parser = argparse.ArgumentParser(prog="linkedin.cli", description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    for name in COMMANDS:
        subparsers.add_parser(name, parents=[common])
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    from linkedin import ARCHIVE_OWNER, FALLBACK_OWNER
    if ARCHIVE_OWNER == FALLBACK_OWNER:
        print("WARNING: no archive owner name found; set basics.linkedin_name in "
              "profile/master.json or LINKEDIN_ARCHIVE_OWNER, or inbound/outbound "
              "attribution will be wrong.", file=sys.stderr)

    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_usage()
        return 2

    archive = Path(args.archive)
    output = Path(args.output)
    if not archive.is_dir():
        print(f"archive directory not found: {archive}")
        print("Run: python -m scripts.copy_linkedin_archive <path-to-extracted-export>")
        return 1

    if args.command == "manifest":
        from linkedin.ingest import manifest

        for name, count in manifest(archive, include_missing=True).items():
            print(f"  {name}: {'MISSING' if count is None else f'{count} rows'}")
        return 0

    if args.command == "warm-intros":
        from linkedin.warm_intros import run

        result = run(archive, output, jobs=_load_jobs(Path(args.jobs)), today=date.today())
        report = result["match_report"]
        print(f"  match precision: {report['precision']:.0%} "
              f"({report['auto']} auto, {report['review']} to review, {report['none']} unmatched)")
        print(f"  coverage: {report['coverage']:.0%} of job-store companies have at "
              f"least one connection in the network")
        print(f"  job-store companies with zero connections: "
              f"{len(result['coverage_zero_companies'])}")
        for name in result["files_written"]:
            print(f"  wrote {output / name}")
        print("  note: warm_intros_uncontacted.csv holds ties with no message history "
              "-- most of the network, including former colleagues never messaged here. "
              "Read it, do not skip it.")
        return 0

    if args.command == "inbound":
        from linkedin.inbound import run

        result = run(archive, output)
        print(f"  {result['recruiter_total']} recruiter messages "
              f"of {result['inbound_total']} inbound")
        print("  classification precision is UNMEASURED -- review classifier_sample.csv")
        for name in result["files_written"]:
            print(f"  wrote {output / name}")
        return 0

    from linkedin.funnel import run

    result = run(archive, output, jobs=_load_jobs(Path(args.jobs)))
    print(f"  {result['rows']} Easy Apply applications (not the full funnel)")
    for name in result["files_written"]:
        print(f"  wrote {output / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
