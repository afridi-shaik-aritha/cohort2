"""Section-aware retrieval chunking (Q3).

Spec §1: chunk per section, not blind fixed windows — each chunk stays inside
one labelled section, sized to CHUNK_TARGET chars (paragraph-boundary splits,
sentence fallback) with CHUNK_OVERLAP carried between consecutive chunks of the
same section. The section label rides on every chunk and becomes the citation.
"""
import re
from dataclasses import dataclass

from app.q3_rag_qa.segmentation import Section

CHUNK_TARGET = 1_600
CHUNK_OVERLAP = 800
MIN_PROSE_LINE_CHARS = 40  # a chunk must contain at least one real prose line

_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


@dataclass
class Chunk:
    id: int
    section: str
    text: str


def _split_long(text: str, target: int) -> list[str]:
    """Split an oversized block at paragraph, then sentence, then hard bounds."""
    if len(text) <= target:
        return [text]
    paras = [p for p in text.split("\n\n") if p.strip()]
    if len(paras) > 1:
        out: list[str] = []
        cur = ""
        for p in paras:
            piece = (cur + "\n\n" + p) if cur else p
            if len(piece) <= target:
                cur = piece
                continue
            if cur:
                out.append(cur)
            cur = p if len(p) <= target else ""
            if not cur:
                out.extend(_split_long(p, target))
        if cur:
            out.append(cur)
        if out and len(out[-1]) <= target * 1.5:
            return out
    sentences = _SENT_RE.split(text)
    if len(sentences) > 1:
        out = []
        cur = ""
        for s in sentences:
            piece = (cur + " " + s) if cur else s
            if len(piece) <= target:
                cur = piece
                continue
            if cur:
                out.append(cur)
            cur = s if len(s) <= target else ""
            if not cur:
                # single mega-sentence: hard split
                out.extend(piece[i:i + target] for i in range(0, len(piece), target))
        if cur:
            out.append(cur)
        return out
    return [text[i:i + target] for i in range(0, len(text), target)]


def chunk_sections(
    sections: list[Section], target: int = CHUNK_TARGET, overlap: int = CHUNK_OVERLAP
) -> list[Chunk]:
    """Chunks across all sections (References handled separately by the caller).

    Consecutive chunks of the same section carry `overlap` chars of context
    from the previous chunk so evidence straddling a split is seen whole.
    """
    chunks: list[Chunk] = []
    cid = 0
    for section in sections:
        label = section.text_label
        if label.strip().lower() == "references":
            continue  # exposed as a structured block, not semantic chunks
        # Skip content-free heading stubs: papers like Attention have bare
        # "6\nResults" headers whose content lives in 6.1/6.2/… An all-heading
        # chunk matches "how was this tested?" lexically ("Results") while
        # carrying zero evidence — it outranks the real evaluation chunks and
        # starves the generator (seen live; see analyzer's generation gate).
        if not any(
            len(line.strip()) >= MIN_PROSE_LINE_CHARS
            for line in section.text.splitlines()
        ):
            continue
        pieces: list[str] = []
        for block in section.text.split("\n\n"):
            if not block.strip():
                continue
            if len(block) > target:
                pieces.extend(_split_long(block, target))
            else:
                pieces.append(block)
        # merge small pieces up to target, then apply overlap between emitted chunks
        merged: list[str] = []
        cur = ""
        for p in pieces:
            piece = (cur + "\n\n" + p) if cur else p
            if len(piece) <= target:
                cur = piece
            else:
                if cur:
                    merged.append(cur)
                cur = p
        if cur:
            merged.append(cur)

        prev_tail = ""
        for piece in merged:
            text = ((prev_tail + "\n\n") if prev_tail else "") + piece
            chunks.append(Chunk(id=cid, section=label, text=text))
            cid += 1
            prev_tail = piece[-overlap:] if overlap and len(piece) > overlap else piece
    return chunks
