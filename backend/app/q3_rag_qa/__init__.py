"""Q3 — RAG question answering over a Q2-ingested paper.

Three deliberate retrieval paths (see docs/specs/q3-spec.md §2):
  metadata   — answered from Q2's ingested title/authors; no retrieval.
  references — the References section captured verbatim at index time.
  semantic   — hybrid dense (local MiniLM) plus keyword retrieval over section-aware chunks.
Two-layer refusal: cosine floor before generation, NOT_IN_PAPER contract in the
answer prompt. Every turn is one Langfuse trace (`q3.paper_qa`).
"""
