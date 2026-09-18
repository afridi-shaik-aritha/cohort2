"""Q6 cross-paper retrieval over the owner-scoped chunk pool.

Reuses Q3's proven machinery (dense cosine + BM25 keyword ranks fused via RRF,
the 0.10 floor) but scores across ALL scoped papers at once. Every pooled chunk
is stamped with its paper metadata at pool-build time (scope.py owns the pool —
chunks from other users never reach this module), and every block/citation
carries `paper_id` + `paper_title` so answers attribute claims per paper.
"""
from dataclasses import dataclass, field

from app.q3_rag_qa import retrieval as q3_retrieval
from app.q6_multi_paper_rag.scope import Scope
from app.shared.llm_text import embed_texts  # module-level so tests can patch

__all__ = ["MultiRetrieval", "retrieve_multi"]


@dataclass
class MultiRetrieval:
    path: str                            # "semantic" | "refused" (Q3 paths reused)
    context_blocks: list[dict] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)
    top_score: float | None = None
    papers_cited: list[dict] = field(default_factory=list)  # [{paper_id, title, blocks}]


def _pool(scope: Scope) -> list[dict]:
    """One flat chunk list across the scoped records, metadata stamped.

    This is the "vector index with metadata" surface: every chunk the scorer
    sees carries paper_id/owner_id/paper_title/authors alongside its vector.
    """
    pool: list[dict] = []
    for rec in scope.records:
        index = rec.get("index") or {}
        for c in index.get("chunks") or []:
            if not c.get("vector"):
                continue
            pool.append(
                {
                    **c,
                    "paper_id": rec["paper_id"],
                    "owner_id": rec.get("owner_id", ""),
                    "paper_title": rec.get("title") or "(untitled paper)",
                    "authors": rec.get("authors") or [],
                }
            )
    return pool


async def retrieve_multi(scope: Scope, question: str, k: int = 5) -> MultiRetrieval:
    """Hybrid retrieval across the scoped papers, with per-paper attribution."""
    pool = _pool(scope)
    if not pool:
        return MultiRetrieval(path="refused")

    qv = (await embed_texts([question], input_type="query"))[0]
    vectors = [c["vector"] for c in pool]
    scored = sorted(
        ((sum(a * b for a, b in zip(qv, v)), i) for i, v in enumerate(vectors)),
        reverse=True,
    )
    top_score = scored[0][0]
    if top_score < q3_retrieval.REFUSAL_FLOOR:
        return MultiRetrieval(path="refused", top_score=top_score)

    # Q3's fusion, applied to the pooled chunks: dense + keyword ranks → RRF.
    dense_ranked = [i for _, i in scored]
    kw_ranked = q3_retrieval._keyword_rank(pool, question)
    fused = q3_retrieval._rrf([dense_ranked, kw_ranked])

    dense_by_idx = {i: s for s, i in scored}
    res = MultiRetrieval(path="semantic", top_score=top_score)
    papers_order: list[str] = []
    per_paper: dict[str, int] = {}
    for pos, i in enumerate(fused[:k], start=1):
        c = pool[i]
        label = f"{c['paper_title']} — {c['section']}"
        block = {
            "position": pos,
            "label": label,
            "paper_id": c["paper_id"],
            "paper_title": c["paper_title"],
            "text": c["text"],
            "score": round(dense_by_idx.get(i, 0.0), 4),
        }
        res.context_blocks.append(block)
        res.citations.append(
            {
                "position": pos,
                "label": label,
                "paper_id": c["paper_id"],
                "paper_title": c["paper_title"],
                "snippet": c["text"][:140],
            }
        )
        if c["paper_id"] not in per_paper:
            papers_order.append(c["paper_id"])
        per_paper[c["paper_id"]] = per_paper.get(c["paper_id"], 0) + 1

    titles = {r["paper_id"]: r.get("title") or "(untitled paper)" for r in scope.records}
    res.papers_cited = [
        {"paper_id": pid, "title": titles.get(pid, pid), "blocks": per_paper[pid]}
        for pid in papers_order
    ]
    return res
