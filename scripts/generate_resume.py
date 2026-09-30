"""Render a master-shaped résumé dict to an editable .docx matching the user's format."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Length, Pt

from scripts import store

REPO_ROOT = Path(__file__).resolve().parent.parent

# Page geometry (US Letter, narrow margins) and type — tuned in the verify task.
PAGE_WIDTH = Inches(8.5)
PAGE_HEIGHT = Inches(11)
LEFT_MARGIN = Inches(0.25)
RIGHT_MARGIN = Inches(0.25)
TOP_MARGIN = Inches(0.7)
BOTTOM_MARGIN = Inches(0.5)
CONTENT_WIDTH = Inches(8.0)   # page width minus L/R margins; right-tab position
BODY_INDENT = Inches(0.5)     # body content indented from headings
BULLET_INDENT = Inches(0.75)
BULLET_HANG = Inches(-0.25)
FONT_NAME = "Times New Roman"
BODY_PT = Pt(12)
NAME_PT = Pt(14)


def _format_month(token: str | None) -> str:
    token = (token or "").strip()
    try:
        return datetime.strptime(token, "%Y-%m").strftime("%B %Y")
    except ValueError:
        return token


def format_date_range(start: str | None, end: str | None) -> str:
    """Format a résumé date range, e.g. ('2019-12', 'Present') -> 'December 2019 – Present'."""
    s = _format_month(start)
    e = _format_month(end)
    if s and e:
        return f"{s} – {e}"
    return s or e


def _styled_run(paragraph, text: str, *, bold: bool = False, italic: bool = False,
                size: Length = BODY_PT):
    run = paragraph.add_run(text)
    run.bold = bold
    run.italic = italic
    run.font.name = FONT_NAME
    run.font.size = size
    return run


def _add_header(document, basics: dict) -> None:
    name_p = document.add_paragraph()
    name_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _styled_run(name_p, basics.get("name", ""), bold=True, size=NAME_PT)

    parts: list[str] = []
    for key in ("location", "email", "phone"):
        if basics.get(key):
            parts.append(basics[key])
    parts.extend(basics.get("links", []))

    contact_p = document.add_paragraph()
    contact_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _styled_run(contact_p, " • ".join(parts))


def _set_bottom_border(paragraph) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    p_bdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "000000")
    p_bdr.append(bottom)
    p_pr.append(p_bdr)


def _add_section_heading(document, text: str):
    p = document.add_paragraph()
    _styled_run(p, text.upper(), bold=True)
    _set_bottom_border(p)
    # Small gap above each section heading so sections don't run together.
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(2)
    return p


def _add_body_paragraph(document, *, indent=BODY_INDENT):
    p = document.add_paragraph()
    p.paragraph_format.left_indent = indent
    return p


def _add_summary(document, summary: str) -> None:
    _add_section_heading(document, "Professional Summary")
    p = _add_body_paragraph(document)
    _styled_run(p, summary)


def _add_entry_header(document, left_text: str, right_text: str) -> None:
    p = _add_body_paragraph(document)
    p.paragraph_format.tab_stops.add_tab_stop(CONTENT_WIDTH, WD_TAB_ALIGNMENT.RIGHT)
    _styled_run(p, left_text, bold=True)
    if right_text:
        _styled_run(p, "\t" + right_text, bold=True)
    # Small gap above entry so roles don't run together; no gap below so
    # the italic title line sits flush under the company/location line.
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(0)


def _add_bullet(document, text: str) -> None:
    p = document.add_paragraph()
    p.paragraph_format.left_indent = BULLET_INDENT
    p.paragraph_format.first_line_indent = BULLET_HANG
    # Single-space bullets — no gap above or below each item.
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(0)
    _styled_run(p, "●\t" + text)


def _add_experience(document, experience: list) -> None:
    _add_section_heading(document, "Professional Experience")
    for job in experience:
        company = job.get("company", "")
        location = job.get("location", "")
        left = f"{company}, {location}" if location else company
        dates = format_date_range(job.get("start"), job.get("end"))
        _add_entry_header(document, left, dates)
        title = job.get("title", "")
        if title:
            tp = _add_body_paragraph(document)
            _styled_run(tp, title, italic=True)
            # No vertical space around the title line — it must sit flush
            # under the company header and flush above the first bullet.
            tp.paragraph_format.space_before = Pt(0)
            tp.paragraph_format.space_after = Pt(0)
        for bullet in job.get("bullets", []):
            _add_bullet(document, bullet)


def _add_skills(document, resume: dict) -> None:
    groups = resume.get("skill_groups")
    skills = resume.get("skills", [])
    if not groups and not skills:
        return
    _add_section_heading(document, "Skills")
    if groups:
        for group in groups:
            p = _add_body_paragraph(document)
            _styled_run(p, f"{group.get('label', '')}: ", bold=True)
            _styled_run(p, ", ".join(group.get("items", [])))
    else:
        p = _add_body_paragraph(document)
        _styled_run(p, ", ".join(skills))


def _add_education(document, education: list) -> None:
    if not education:
        return
    _add_section_heading(document, "Education")
    for edu in education:
        inst = edu.get("institution", "")
        location = edu.get("location", "")
        left = f"{inst}, {location}" if location else inst
        dates = format_date_range(edu.get("start"), edu.get("end"))
        _add_entry_header(document, left, dates)
        degree = edu.get("degree", "")
        if degree:
            p = _add_body_paragraph(document)
            _styled_run(p, degree)


def _setup_page(document) -> None:
    section = document.sections[0]
    section.page_width = PAGE_WIDTH
    section.page_height = PAGE_HEIGHT
    section.left_margin = LEFT_MARGIN
    section.right_margin = RIGHT_MARGIN
    section.top_margin = TOP_MARGIN
    section.bottom_margin = BOTTOM_MARGIN
    normal = document.styles["Normal"]
    normal.font.name = FONT_NAME
    normal.font.size = BODY_PT
    # Collapse default Word paragraph spacing so spacing is controlled explicitly.
    normal.paragraph_format.line_spacing = 1.0
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(0)


def build_resume(resume: dict) -> Document:
    document = Document()
    _setup_page(document)
    _add_header(document, resume.get("basics", {}))
    if resume.get("summary"):
        _add_summary(document, resume["summary"])
    if resume.get("experience"):
        _add_experience(document, resume["experience"])
    _add_skills(document, resume)
    _add_education(document, resume.get("education", []))
    return document


def render_resume(resume: dict, out_path: Path) -> None:
    store.validate_item(resume, "master")
    document = build_resume(resume)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(out_path))


def _cli(argv: list[str] | None = None) -> int:
    from jsonschema import ValidationError

    parser = argparse.ArgumentParser(prog="scripts.generate_resume")
    parser.add_argument("--resume", required=True, type=Path,
                        help="path to a master-shaped tailored-resume JSON")
    parser.add_argument("--out", required=True, type=Path,
                        help="path to write the .docx")
    args = parser.parse_args(argv)

    try:
        resume = json.loads(args.resume.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read --resume: {exc}", flush=True)
        return 2
    try:
        render_resume(resume, args.out)
    except ValidationError as exc:
        print(f"error: invalid resume: {exc.message}", flush=True)
        return 1
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
