"""Render a Markdown cover letter to an editable .docx matching the résumé's typography."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from docx import Document
from docx.shared import Inches, Pt

from scripts.generate_resume import BODY_PT, FONT_NAME, _add_header, _setup_page

REPO_ROOT = Path(__file__).resolve().parent.parent

# Cover letters read better with conventional 1" side margins than the résumé's
# narrow ones; everything else (font, name header) matches the résumé.
LETTER_LEFT_MARGIN = Inches(1.0)
LETTER_RIGHT_MARGIN = Inches(1.0)
PARA_SPACE_AFTER = Pt(10)

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _add_letter_paragraph(document, block: str) -> None:
    """Add one Markdown block as a paragraph. Blank-line-separated source blocks
    become paragraphs; single newlines within a block (e.g. the signature) become
    line breaks. ``**bold**`` spans render bold."""
    p = document.add_paragraph()
    p.paragraph_format.space_after = PARA_SPACE_AFTER
    lines = block.split("\n")
    for line_idx, line in enumerate(lines):
        if line_idx:
            p.add_run().add_break()
        # Odd-index segments from the split are the captured bold spans.
        for seg_idx, seg in enumerate(_BOLD_RE.split(line)):
            if not seg:
                continue
            run = p.add_run(seg)
            run.bold = seg_idx % 2 == 1
            run.font.name = FONT_NAME
            run.font.size = BODY_PT


def build_cover_letter(markdown: str, basics: dict) -> Document:
    document = Document()
    _setup_page(document)
    section = document.sections[0]
    section.left_margin = LETTER_LEFT_MARGIN
    section.right_margin = LETTER_RIGHT_MARGIN
    if basics:
        _add_header(document, basics)
        document.add_paragraph()  # spacer between letterhead and salutation
    blocks = [b for b in re.split(r"\n\s*\n", markdown.strip()) if b.strip()]
    for block in blocks:
        _add_letter_paragraph(document, block.strip("\n"))
    return document


def render_cover_letter(letter_path: Path, out_path: Path, profile_path: Path | None) -> None:
    markdown = Path(letter_path).read_text(encoding="utf-8")
    basics: dict = {}
    if profile_path and Path(profile_path).exists():
        basics = json.loads(Path(profile_path).read_text(encoding="utf-8")).get("basics", {})
    document = build_cover_letter(markdown, basics)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(out_path))


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.generate_cover_letter")
    parser.add_argument("--letter", required=True, type=Path,
                        help="path to a cover-letter Markdown file")
    parser.add_argument("--out", required=True, type=Path,
                        help="path to write the .docx")
    parser.add_argument("--profile", type=Path, default=REPO_ROOT / "profile" / "master.json",
                        help="master.json for the letterhead (name/contact); omitted gracefully if absent")
    args = parser.parse_args(argv)

    try:
        render_cover_letter(args.letter, args.out, args.profile)
    except OSError as exc:
        print(f"error: cannot render cover letter: {exc}", flush=True)
        return 2
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
