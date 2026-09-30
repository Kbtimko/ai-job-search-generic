"""Workstream 4 -- the LinkedIn Easy Apply record, enriched against the job store.

Deliberately just one CSV. The source file has no response or outcome column, so
response rates and time-to-first-response are not computable from it at any sample
size. The caveat below is written into the output rather than left in a comment.
"""

from __future__ import annotations

from pathlib import Path

CAVEAT = (
    "# LinkedIn Easy Apply only -- NOT direct-to-ATS applications, which are the\n"
    "# bulk of any real search. The source file carries no response or outcome\n"
    "# column, so response rates and time-to-response are not computable from it at\n"
    "# any sample size. Read this as a biased, largely historical activity log --\n"
    "# not the funnel. matched_job_id joins on company name only, so an old\n"
    "# application can point at an unrelated current req at the same company.\n"
)

COLUMNS = ["application_date", "company", "job_title", "job_url", "resume_name",
           "matched_job_id", "match_confidence"]


def run(archive_dir: Path, output_dir: Path, jobs: list[dict],
        aliases: dict | None = None) -> dict:
    """Run workstream 4 and write ``applications_enriched.csv``."""
    from linkedin.companies import load_aliases, match_company
    from linkedin.ingest import load_applications
    from linkedin.output import write_csv

    aliases = load_aliases() if aliases is None else aliases
    output_dir = Path(output_dir)
    applications = load_applications(archive_dir)

    job_companies = [j.get("company", "") for j in jobs]
    by_company = {j.get("company", ""): j for j in jobs}

    rows = []
    for application in applications:
        result = match_company(application.get("company_name", ""), job_companies, aliases)
        job = by_company.get(result.matched) if result.matched else None
        rows.append({
            "application_date": application.get("application_date_parsed") or "",
            "company": application.get("company_name", ""),
            "job_title": application.get("job_title", ""),
            "job_url": application.get("job_url", ""),
            "resume_name": application.get("resume_name", ""),
            "matched_job_id": job.get("id", "") if job else "",
            "match_confidence": round(result.score, 1) if job else "",
        })

    write_csv(output_dir / "applications_enriched.csv", COLUMNS, rows, CAVEAT)

    return {"rows": len(rows), "files_written": ["applications_enriched.csv"]}
