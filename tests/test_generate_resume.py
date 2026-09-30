from pathlib import Path

from docx.shared import Pt

from scripts.generate_resume import format_date_range


def test_format_date_range_full():
    assert format_date_range("2019-12", "Present") == "December 2019 – Present"


def test_format_date_range_two_months():
    assert format_date_range("2004-09", "2008-05") == "September 2004 – May 2008"


def test_format_date_range_missing_end():
    assert format_date_range("2019-12", "") == "December 2019"


def test_format_date_range_none_end():
    assert format_date_range("2019-12", None) == "December 2019"


from docx import Document
from scripts.generate_resume import _add_header, NAME_PT, BODY_PT
from docx.enum.text import WD_ALIGN_PARAGRAPH


def _basics():
    return {"name": "Alex J. Rivera", "headline": "Analytics Leader",
            "email": "alex@example.com", "phone": "555.010.0199",
            "location": "Denver, CO", "links": ["alexrivera.example"]}


def test_add_header_name_is_bold_centered_14pt():
    doc = Document()
    _add_header(doc, _basics())
    name_run = doc.paragraphs[0].runs[0]
    assert name_run.text == "Alex J. Rivera"
    assert name_run.bold is True
    assert name_run.font.size == NAME_PT
    assert doc.paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.CENTER


def test_add_header_contact_line_joined_and_centered():
    doc = Document()
    _add_header(doc, _basics())
    contact = doc.paragraphs[1]
    assert "alex@example.com" in contact.text
    assert "Denver, CO" in contact.text
    assert "alexrivera.example" in contact.text
    assert "•" in contact.text
    assert contact.alignment == WD_ALIGN_PARAGRAPH.CENTER


from scripts.generate_resume import _add_section_heading
from docx.oxml.ns import qn


def test_add_section_heading_uppercased_bold():
    doc = Document()
    _add_section_heading(doc, "Professional Summary")
    run = doc.paragraphs[0].runs[0]
    assert run.text == "PROFESSIONAL SUMMARY"
    assert run.bold is True


def test_add_section_heading_has_bottom_border():
    doc = Document()
    p = _add_section_heading(doc, "Skills")
    pPr = p._p.find(qn("w:pPr"))
    assert pPr is not None
    pBdr = pPr.find(qn("w:pBdr"))
    assert pBdr is not None
    assert pBdr.find(qn("w:bottom")) is not None


from scripts.generate_resume import _add_summary, BODY_INDENT


def test_add_summary_writes_heading_and_indented_body():
    doc = Document()
    _add_summary(doc, "Analytics leader with 15+ years.")
    texts = [p.text for p in doc.paragraphs]
    assert "PROFESSIONAL SUMMARY" in texts
    body = [p for p in doc.paragraphs if p.text == "Analytics leader with 15+ years."][0]
    assert body.paragraph_format.left_indent == BODY_INDENT


from scripts.generate_resume import _add_experience


def _experience():
    return [{
        "company": "Lumen Commerce", "location": "Denver, CO",
        "title": "Senior Manager, Analytics",
        "start": "2019-12", "end": "Present",
        "bullets": ["Primary analytics partner to C-suite.",
                    "Built a metrics ecosystem using dbt & Looker."],
    }]


def test_add_experience_company_bold_and_dates_present():
    doc = Document()
    _add_experience(doc, _experience())
    full = "\n".join(p.text for p in doc.paragraphs)
    assert "PROFESSIONAL EXPERIENCE" in full
    assert "Lumen Commerce, Denver, CO" in full
    assert "December 2019 – Present" in full
    # company run is bold
    header = [p for p in doc.paragraphs if "Lumen Commerce" in p.text][0]
    assert header.runs[0].bold is True


def test_add_experience_title_is_italic():
    doc = Document()
    _add_experience(doc, _experience())
    title_p = [p for p in doc.paragraphs
               if "Senior Manager, Analytics" in p.text][0]
    assert title_p.runs[0].italic is True


def test_add_experience_bullets_rendered():
    doc = Document()
    _add_experience(doc, _experience())
    bullets = [p for p in doc.paragraphs if p.text.startswith("●")]
    assert len(bullets) == 2
    assert "dbt & Looker" in bullets[1].text


from scripts.generate_resume import _add_skills


def test_add_skills_grouped_label_bold_and_items():
    doc = Document()
    resume = {"skill_groups": [
        {"label": "Analytics & Data Science", "items": ["SQL", "Python", "dbt"]},
        {"label": "AI / LLM Tooling", "items": ["Claude Code", "Cursor"]},
    ]}
    _add_skills(doc, resume)
    full = "\n".join(p.text for p in doc.paragraphs)
    assert "SKILLS" in full
    assert "Analytics & Data Science: SQL, Python, dbt" in full
    label_p = [p for p in doc.paragraphs if p.text.startswith("Analytics & Data Science")][0]
    assert label_p.runs[0].bold is True


def test_add_skills_flat_fallback():
    doc = Document()
    _add_skills(doc, {"skills": ["SQL", "dbt", "Looker"]})
    full = "\n".join(p.text for p in doc.paragraphs)
    assert "SKILLS" in full
    assert "SQL, dbt, Looker" in full


def test_add_skills_empty_renders_nothing():
    doc = Document()
    _add_skills(doc, {})
    assert [p.text for p in doc.paragraphs if p.text] == []


from scripts.generate_resume import _add_education


def test_add_education_entry_and_degree():
    doc = Document()
    edu = [{"institution": "State University",
            "location": "Denver, CO",
            "degree": "B.S. in Economics", "start": "2004-09", "end": "2008-05"}]
    _add_education(doc, edu)
    full = "\n".join(p.text for p in doc.paragraphs)
    assert "EDUCATION" in full
    assert "State University" in full
    assert "September 2004 – May 2008" in full
    assert "B.S. in Economics" in full


def test_add_education_empty_renders_nothing():
    doc = Document()
    _add_education(doc, [])
    assert [p.text for p in doc.paragraphs if p.text] == []


from scripts.generate_resume import build_resume, LEFT_MARGIN


def _full_resume():
    return {
        "basics": {"name": "Alex J. Rivera", "email": "alex@example.com",
                   "phone": "555.010.0199", "location": "Denver, CO",
                   "links": ["alexrivera.example"]},
        "summary": "Analytics leader with 15+ years.",
        "experience": [{"company": "Lumen Commerce", "location": "Denver, CO",
                        "title": "Senior Manager", "start": "2019-12", "end": "Present",
                        "bullets": ["Built a metrics ecosystem using dbt & Looker."]}],
        "skill_groups": [{"label": "Analytics & Data Science", "items": ["SQL", "Python"]}],
        "education": [{"institution": "State University", "location": "Denver, CO",
                       "degree": "B.S. Economics", "start": "2004-09", "end": "2008-05"}],
    }


def test_build_resume_has_all_sections_and_no_tables():
    doc = build_resume(_full_resume())
    full = "\n".join(p.text for p in doc.paragraphs)
    assert "Alex J. Rivera" in full
    assert "PROFESSIONAL SUMMARY" in full
    assert "PROFESSIONAL EXPERIENCE" in full
    assert "SKILLS" in full
    assert "EDUCATION" in full
    assert "dbt & Looker" in full
    assert len(doc.tables) == 0  # ATS: no tables


def test_build_resume_sets_narrow_margins():
    doc = build_resume(_full_resume())
    assert doc.sections[0].left_margin == LEFT_MARGIN


from jsonschema import ValidationError
from scripts.generate_resume import render_resume


def test_render_resume_writes_openable_docx(tmp_path: Path):
    out = tmp_path / "nested" / "resume.docx"
    render_resume(_full_resume(), out)
    assert out.exists()
    reopened = Document(str(out))
    full = "\n".join(p.text for p in reopened.paragraphs)
    assert "Alex J. Rivera" in full


def test_render_resume_invalid_raises_and_writes_nothing(tmp_path: Path):
    out = tmp_path / "resume.docx"
    bad = {"summary": "no basics or experience"}  # missing required fields
    try:
        render_resume(bad, out)
        assert False, "expected ValidationError"
    except ValidationError:
        pass
    assert not out.exists()


import json as _json
from scripts.generate_resume import _cli


def test_cli_writes_docx_and_returns_zero(tmp_path: Path):
    resume_path = tmp_path / "tailored-resume.json"
    out_path = tmp_path / "tailored-resume.docx"
    resume_path.write_text(_json.dumps(_full_resume()))
    rc = _cli(["--resume", str(resume_path), "--out", str(out_path)])
    assert rc == 0
    assert out_path.exists()


def test_cli_invalid_resume_returns_one(tmp_path: Path):
    resume_path = tmp_path / "bad.json"
    out_path = tmp_path / "out.docx"
    resume_path.write_text(_json.dumps({"summary": "missing required"}))
    rc = _cli(["--resume", str(resume_path), "--out", str(out_path)])
    assert rc == 1
    assert not out_path.exists()


def test_cli_malformed_json_returns_two(tmp_path: Path):
    resume_path = tmp_path / "broken.json"
    out_path = tmp_path / "out.docx"
    resume_path.write_text("{ not valid json")
    rc = _cli(["--resume", str(resume_path), "--out", str(out_path)])
    assert rc == 2
    assert not out_path.exists()


def test_entry_header_is_tight_to_title():
    doc = build_resume(_full_resume())
    header = [p for p in doc.paragraphs if "Lumen Commerce" in p.text][0]
    title = [p for p in doc.paragraphs if p.text.startswith("Senior Manager")][0]
    assert header.paragraph_format.space_after == Pt(0)
    assert title.paragraph_format.space_before == Pt(0)
    assert title.paragraph_format.space_after == Pt(0)


def test_bullets_are_single_spaced():
    doc = build_resume(_full_resume())
    bullets = [p for p in doc.paragraphs if p.text.startswith("●")]
    assert len(bullets) >= 1
    for b in bullets:
        assert b.paragraph_format.space_after == Pt(0)
        assert b.paragraph_format.space_before == Pt(0)


def test_section_heading_has_space_above():
    doc = build_resume(_full_resume())
    heading = [p for p in doc.paragraphs if p.text == "PROFESSIONAL EXPERIENCE"][0]
    assert heading.paragraph_format.space_before == Pt(8)
