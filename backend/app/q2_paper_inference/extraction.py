"""PDF extraction for Q2 — text, metadata, and the cleanup real papers need.

Measured on a real paper (`1706.03762v7.pdf`, Attention Is All You Need):
15 pages, 39,498 chars, PDF metadata title/author are EMPTY strings, and 24
hyphen-breaks appear. So: parse title/authors from page-1 text, de-hyphenate,
drop running headers/footers, and refuse image-only PDFs instead of emitting junk.
"""
import re
from collections import Counter
from dataclasses import dataclass, field

MIN_PAPER_CHARS = 2_000
MAX_PAPER_CHARS = 400_000

_AFFILIATION_HINTS = (
    "university", "institute", "research", "google", "microsoft", "meta",
    "deepmind", "openai", "lab", "laboratory", "department", "school", "college",
    "inc.", "ltd", "corp",
)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_ABSTRACT_RE = re.compile(r"^\s*(abstract|1\s+introduction|introduction)\b", re.I)
_HYPHEN_BREAK_RE = re.compile(r"(\w)-\n(\w)")


class PdfError(Exception):
    """Extraction failure with a machine-readable code the API maps to HTTP."""

    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra

    def to_detail(self) -> dict:
        return {"error": self.code, "message": self.message, **self.extra}


@dataclass
class Extraction:
    pages: int
    chars: int
    text: str
    title: str = ""
    authors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def strategy(self) -> str:
        return "single_pass" if self.chars <= 40_000 else "map_reduce"


def extract_pdf(data: bytes) -> Extraction:
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        raise PdfError("bad_pdf", f"could not open PDF: {e}") from e

    try:
        pages = len(doc)
        raw_pages = [p.get_text("text") for p in doc]
        title, authors, meta_warnings = _title_authors(doc)
        cleaned_pages = [_clean_page_text(t) for t in raw_pages]
        header_footer = _running_lines(cleaned_pages)
        kept_pages = [
            "\n".join(ln for ln in page.split("\n") if ln.strip() not in header_footer)
            for page in cleaned_pages
        ]
        text = "\n".join(kept_pages).strip()
    finally:
        doc.close()

    text = re.sub(r"\n{3,}", "\n\n", _dehyphenate(text))
    warnings = list(meta_warnings)
    if header_footer:
        warnings.append(f"removed {len(header_footer)} repeated header/footer line(s)")

    if len(text) < MIN_PAPER_CHARS:
        raise PdfError(
            "no_extractable_text",
            "this PDF has almost no extractable text — is it a scanned/image-only file?",
            chars=len(text),
        )
    if len(text) > MAX_PAPER_CHARS:
        raise PdfError(
            "paper_too_large",
            f"extracted text is {len(text):,} chars; this engine handles papers, not books",
            limit_chars=MAX_PAPER_CHARS,
        )

    return Extraction(
        pages=pages, chars=len(text), text=text, title=title,
        authors=authors, warnings=warnings,
    )


def _dehyphenate(text: str) -> str:
    return _HYPHEN_BREAK_RE.sub(r"\1\2", text)


def _clean_page_text(text: str) -> str:
    out = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            out.append("")
            continue
        if _drop_table_noise(stripped):
            continue
        out.append(stripped)
    return "\n".join(out)


def _drop_table_noise(line: str) -> bool:
    """Numbers-only / symbol-heavy short lines are table fragments, not prose."""
    if len(line.split()) >= 4:
        return False
    non_alnum = sum(1 for c in line if not c.isalnum() and not c.isspace())
    return bool(line) and (non_alnum / len(line)) > 0.6


def _running_lines(pages: list[str]) -> set[str]:
    """Lines appearing on >=50% of pages are running headers/footers."""
    if len(pages) < 3:
        return set()
    counts: dict[str, int] = {}
    for page in pages:
        lines = [ln.strip() for ln in page.split("\n") if ln.strip()]
        for cand in set(lines[:2] + lines[-2:]):
            if len(cand) < 80:
                counts[cand] = counts.get(cand, 0) + 1
    threshold = max(2, int(len(pages) * 0.5))
    return {line for line, n in counts.items() if n >= threshold}
def _title_authors(doc) -> tuple[str, list[str], list[str]]:
    """Parse title/authors from page 1 — PDF metadata is unreliable (often empty).

    Measured on a real arXiv paper: the title is the LARGEST upright font on
    page 1 (17.2pt vs 12pt license boilerplate and 10pt body); the arXiv stamp
    is rotated 90° and must be ignored; author emails sit between author names.
    """
    warnings: list[str] = []
    try:
        meta = doc.metadata or {}
        meta_title = (meta.get("title") or "").strip()
        meta_author = (meta.get("author") or "").strip()
    except Exception:
        meta_title, meta_author = "", ""

    lines = _page1_lines_by_font(doc)
    if not lines:
        if meta_title:
            warnings.append("title taken from PDF metadata (page 1 had no text spans)")
            return meta_title, _split_authors(meta_author), warnings
        warnings.append("could not determine title")
        return "", [], warnings

    body_size = Counter(round(size, 1) for _, size in lines).most_common(1)[0][0]
    headline_sizes = sorted(
        {size for _, size in lines if size > body_size + 0.4}, reverse=True
    )
    title = ""
    if headline_sizes:
        biggest = headline_sizes[0]  # largest type on the page beats license blocks
        title = max((t for t, s in lines if s == biggest), key=len)
    if not meta_title and not title:
        warnings.append("title heuristics found no headline text; using first page-1 line")
        title = lines[0][0]
    elif not meta_title:
        warnings.append("title inferred from page-1 text (PDF metadata empty)")

    authors: list[str] = []
    if title:
        idx = next((i for i, (t, _) in enumerate(lines) if t == title), None)
        if idx is not None:
            bucket: list[str] = []
            for text, size in lines[idx + 1:]:
                if _ABSTRACT_RE.match(text):
                    break
                if _EMAIL_RE.search(text):
                    continue  # emails interleave the author block; skip, don't stop
                if any(h in text.lower() for h in _AFFILIATION_HINTS):
                    continue
                if size < body_size - 0.2:  # affiliations set in smaller type
                    continue
                if len(text) > 60 or len(text) < 3 or re.fullmatch(r"[\d\W]+", text):
                    continue
                bucket.append(text)
                if len(bucket) >= 16:
                    break
            authors = _split_authors(", ".join(bucket))
    if not authors and meta_author:
        authors = _split_authors(meta_author)
    if not authors:
        warnings.append("author list could not be parsed from page 1")
    return title, authors, warnings


def _page1_lines_by_font(doc) -> list[tuple[str, float]]:
    """[(line_text, max_font_size)] for page 1, in reading order.

    Rotated lines (arXiv side-stamps, watermarks) are dropped — never titles.
    """
    if len(doc) == 0:
        return []
    try:
        data = doc[0].get_text("dict")
    except Exception:
        return []
    lines: list[tuple[str, float]] = []
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(s.get("text", "") for s in spans).strip()
            if not text:
                continue
            direction = line.get("dir") or (1.0, 0.0)
            if abs(direction[0] - 1.0) > 0.05 or abs(direction[1]) > 0.05:
                continue  # rotated: stamps/watermarks, not title text
            lines.append((text, max((s.get("size", 0.0) for s in spans), default=0.0)))
    return lines


_FOOTNOTE_MARKS = " *\u2020\u2021\u2217\u203B"  # *, †, ‡, U+2217 (arXiv's asterisk), reference mark


def _split_authors(blob: str) -> list[str]:
    if not blob:
        return []
    out: list[str] = []
    for part in re.split(r",| and |;|·|[*\u2020\u2021\u2217]|\d", blob):
        name = re.sub(r"\s+", " ", part).strip(_FOOTNOTE_MARKS)
        if len(name) < 3 or len(name) > 60 or _EMAIL_RE.search(name):
            continue
        if not re.search(r"[A-Za-z]", name):
            continue
        if name.lower() in {n.lower() for n in out}:
            continue
        out.append(name)
        if len(out) >= 20:
            break
    return out