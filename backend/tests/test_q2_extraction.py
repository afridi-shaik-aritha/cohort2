"""Q2 — PDF extraction: cleanup, metadata parsing, quality gates.

PDFs are built on the fly with fitz so no binary fixtures ship. The helper
mirrors real arXiv layout: distinct font sizes per line (title > license >
body) and DIFFERENT content per page — identical pages would make every line
a "running header" by the ≥50%-of-pages rule.
"""
import fitz
import pytest

from app.q2_paper_inference.extraction import PdfError, extract_pdf


def _pdf(pages_lines):
    """pages_lines: list of pages, each a list of (line, fontsize) tuples."""
    doc = fitz.open()
    for page_lines in pages_lines:
        page = doc.new_page()
        y = 72
        for ln, size in page_lines:
            page.insert_text((72, y), ln, fontsize=size)
            y += size * 1.6
    data = doc.tobytes()
    doc.close()
    return data


def _filler(page_no, n=30, size=10):
    return [
        (f"Paragraph {page_no}.{i} about methods, datasets, and quantitative results.", size)
        for i in range(n)
    ]


def test_dehyphenation_title_and_authors():
    page1 = [
        ("Attention Is All You Need", 17),
        ("Ashish Vaswani", 10),
        ("Abstract", 12),
        ("We propose a new architecture.", 10),
        ("attribu-", 10),
        ("tion mechanisms matter.", 10),
        ("More body text follows here.", 10),
    ]
    ex = extract_pdf(_pdf([page1, _filler(2), _filler(3)]))
    assert "attribution mechanisms" in ex.text
    assert ex.title == "Attention Is All You Need"
    assert "Ashish Vaswani" in ex.authors
    assert ex.pages == 3
    assert ex.chars == len(ex.text)
    assert ex.strategy == "single_pass"


def test_title_is_largest_font_not_license_boilerplate():
    # mirrors the real arXiv layout: 12pt license lines, 17pt title, 10pt
    # authors/affiliations/emails — the largest upright font must win.
    page1 = [
        ("Provided proper attribution is provided, Google hereby grants permission", 12),
        ("reproduce the tables and figures in this paper solely for journalistic use in", 12),
        ("scholarly works.", 12),
        ("Attention Is All You Need", 17),
        ("Ashish Vaswani", 10),
        ("Google Brain", 10),
        ("avaswani@google.com", 10),
        ("Noam Shazeer", 10),
        ("Abstract", 12),
    ] + _filler(1, n=10)
    ex = extract_pdf(_pdf([page1, _filler(2), _filler(3)]))
    assert ex.title == "Attention Is All You Need"
    assert "Ashish Vaswani" in ex.authors
    assert "Noam Shazeer" in ex.authors
    assert not any("journalistic" in a for a in ex.authors)


def test_running_header_removed():
    header = "arXiv:1706.03762v7 [cs.CL] 2 Aug 2023"
    pages = [[(header, 9)] + _filler(p, n=8) for p in range(1, 6)]
    ex = extract_pdf(_pdf(pages))
    assert "arXiv:1706.03762v7" not in ex.text
    assert "Paragraph 3.1" in ex.text  # unique content survives


def test_quality_gate_and_bad_pdf():
    with pytest.raises(PdfError) as e:
        extract_pdf(_pdf([[("tiny", 11)]]))
    assert e.value.code == "no_extractable_text"
    with pytest.raises(PdfError) as e2:
        extract_pdf(b"not a pdf at all")
    assert e2.value.code == "bad_pdf"
