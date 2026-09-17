"""Q3 indexer: turn a Q2-ingested paper into a retrieval index.

Pipeline (docs/specs/q3-spec.md §1):
  record["text"] → segment_text() → chunk_sections() → embed chunks (local
  MiniLM) → persist `{index: {...}}` back into the paper record.

The References section is captured verbatim as structured data — NOT embedded —
because a dense list of short, similar-looking entries serves semantic
retrieval poorly (spec §2, the "what are the references?" decision).
"""
import datetime as _dt

from app.q2_paper_inference import storage
from app.q3_rag_qa.chunking import Chunk, chunk_sections
from app.q3_rag_qa.segmentation import extract_references, segment_text
from app.shared.llm_text import embed_model, embed_texts

__all__ = ["build_index", "get_index"]


def get_index(record: dict) -> dict | None:
    """The paper's built Q3 index, or None if never indexed."""
    idx = record.get("index")
    return idx if isinstance(idx, dict) and idx.get("chunks") else None


async def build_index(record: dict) -> dict:
    """Segment → chunk → embed → persist into the paper record. Idempotent."""
    sections = segment_text(record.get("text", ""))
    references = extract_references(sections)
    # References are excluded from semantic chunks by the chunker itself.
    chunks: list[Chunk] = chunk_sections(sections)

    provider, model = embed_model()
    vectors = await embed_texts([c.text for c in chunks], input_type="passage") if chunks else []

    index = {
        "embedding_provider": provider,
        "embedding_model": model,
        "built_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "references": references,
        "chunks": [
            {"id": c.id, "section": c.section, "text": c.text, "vector": v}
            for c, v in zip(chunks, vectors)
        ],
    }
    record["index"] = index
    storage.save_paper(record)
    return {
        "paper_id": record["paper_id"],
        "chunks": len(chunks),
        "sections": len(sections),
        "references_chars": len(references) if references else 0,
        "has_references": references is not None,
        "embedding_provider": provider,
        "embedding_model": model,
    }
