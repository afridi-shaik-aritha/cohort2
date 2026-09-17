"""Section-aware segmentation of a paper's extracted text (Q3).

Q2's extraction gives clean lines but no section structure. This module rebuilds
the section map, calibrated on the real Attention paper where PyMuPDF emits
heading numbers and titles as SEPARATE lines in reading order:

    '1'  -> 'Introduction'      (number line, then title line)
    '3.1' -> 'Encoder and Decoder Stacks'

plus word headings (Abstract / References / Appendix / Acknowledgements).

Table cells also emit bare number lines ('64', '32', ...), so a number only
counts as a heading if the FIRST non-empty line after it is a Title-Case title
(1–10 words, no trailing punctuation) — table cells are followed by numbers or
fragments, not titles. Numbered headings must also form a monotonic sequence
(top level 1, 2, 3...; subsections increment within their parent), which kills
the residual false positives (e.g. '92.1' inside results tables).
"""
import re
from dataclasses import dataclass

_NUM_ONLY_RE = re.compile(r"^(\d{1,2}(?:\.\d+)*)$")
_WORD_HEADINGS = {"abstract", "references", "appendix", "acknowledgements",
                  "acknowledgments", "conclusion", "introduction", "discussion",
                  "related work", "background", "limitations"}

MAX_REFS_CHARS = 20_000


@dataclass
class Section:
    text_label: str   # e.g. "3.2 Attention" — the citation label
    text: str
    start: int        # line index where the section begins


def _title_after(lines: list[str], i: int) -> int | None:
    """Line index of the title that pairs with a heading-number line, else None.

    The FIRST non-empty line after the number must be the title (PyMuPDF keeps
    reading order); anything else means this number is table data.
    """
    for k in range(i + 1, min(i + 4, len(lines))):
        t = lines[k].strip()
        if not t:
            continue
        words = t.split()
        if (
            1 <= len(words) <= 10
            and not t.endswith((".", ",", ";", ":"))
            and t[0].isupper()
            and sum(1 for w in words if w[0].isupper()) >= len(words) * 0.6
        ):
            return k
        return None
    return None


def _find_heading_lines(lines: list[str]) -> list[tuple[int, str]]:
    """[(line_index, label)] for every heading, before sequence filtering."""
    marks: list[tuple[int, str]] = []
    consumed: set[int] = set()  # title lines already absorbed into a numbered heading
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s or len(s) > 80:
            continue
        low = s.lower().rstrip(":")
        if low in _WORD_HEADINGS:
            if i not in consumed:
                marks.append((i, s[0].upper() + s[1:].rstrip(":")))
            continue
        if _NUM_ONLY_RE.match(s) and len(s) <= 3:
            t = _title_after(lines, i)
            if t is not None:
                marks.append((i, f"{s} {lines[t].strip()}"))
                consumed.add(t)
    return marks


def _sequence_filter(marks: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Keep numbered headings only when they form a monotonic sequence.

    Top level must be 1, 2, 3, …; a subsection x.y must follow its parent's
    number and increment y within it. Word headings pass through untouched.
    """
    out: list[tuple[int, str]] = []
    prev_top = 0
    sub: dict[str, int] = {}
    for i, label in marks:
        m = re.match(r"^(\d{1,2}(?:\.\d+)*) ", label)
        if not m:
            out.append((i, label))
            continue
        num = m.group(1)
        if "." not in num:
            n = int(num)
            if n != prev_top + 1:
                continue
            prev_top = n
            sub = {}
        else:
            parts = num.split(".")
            if int(parts[0]) != prev_top:
                continue
            key = ".".join(parts[:-1])
            if int(parts[-1]) != sub.get(key, 0) + 1:
                continue
            sub[key] = int(parts[-1])
        out.append((i, label))
    return out


def segment_text(text: str) -> list[Section]:
    """Split extracted paper text into labelled sections.

    Text before the first heading (title block + author list on page 1) becomes
    the implicit section "Title & Authors".
    """
    lines = text.split("\n")
    headings = _sequence_filter(_find_heading_lines(lines))
    if not headings:
        return [Section(text_label="Title & Authors", text=text.strip(), start=0)]

    sections: list[Section] = []
    if headings[0][0] > 0:
        preamble = "\n".join(lines[: headings[0][0]]).strip()
        if preamble:
            sections.append(Section(text_label="Title & Authors", text=preamble, start=0))
    for j, (start, label) in enumerate(headings):
        end = headings[j + 1][0] if j + 1 < len(headings) else len(lines)
        body = "\n".join(lines[start:end]).strip()
        if body:
            sections.append(Section(text_label=label, text=body, start=start))
    return sections


def extract_references(sections: list[Section]) -> str | None:
    """Verbatim References section (label-stripped), or None if absent.

    Captured at index time as structured data — a references list is dense,
    short, similar-looking entries that semantic chunk retrieval serves poorly.
    """
    for idx, s in enumerate(sections):
        if s.text_label.strip().lower() == "references":
            body = s.text.split("\n", 1)[1].strip() if "\n" in s.text else ""
            # include following non-heading sections' text (refs often run to the
            # end of the paper, sometimes under stray post-heading noise)
            if idx + 1 < len(sections):
                nxt = sections[idx + 1]
                if nxt.text_label.strip().lower() not in {"appendix"} and nxt.start > s.start:
                    tail = "\n".join(
                        sec.text for sec in sections[idx + 1:]
                        if not _NUM_ONLY_RE_WITH_TITLE.match(sec.text_label)
                    )
                    body = (body + "\n" + tail).strip()
            return body[:MAX_REFS_CHARS] if body else None
    return None


_NUM_ONLY_RE_WITH_TITLE = re.compile(r"^\d{1,2}(\.\d+)* ")
