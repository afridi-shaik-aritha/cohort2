"""Q3 retrieval router: pick the right path for each question, deliberately.

Three routes (docs/specs/q3-spec.md §2):
  metadata   — "who are the authors?" / "what is the title?" → answered from
               Q2's ingested title/authors fields; retrieval never runs.
  references — "what are the references?" → the verbatim References block
               captured at index time; semantic search over a dense list of
               short, similar-looking entries is noise.
  semantic   — "how was this tested?" → dense cosine top-k over section-aware
               chunks, gated at REFUSAL_FLOOR so weak matches refuse honestly
               instead of grounding a guess.

Routing is deterministic keyword scoring (inspectable, free, instant). The
known upgrade path — an LLM classifier — is recorded in the spec's Decisions
log and can replace `classify()` without touching any other layer.
"""
import math
import re
from collections import Counter
from dataclasses import dataclass, field

from app.shared.llm_text import embed_texts  # module-level so tests can patch it

__all__ = ["classify", "RetrievalResult", "retrieve", "REFUSAL_FLOOR"]

# Recalibrated for the local MiniLM embedder (2026-09-17 live runs): MiniLM is
# a symmetric model, so short vague queries score low even when the paper holds
# the answer ("How was this tested?" → 0.139 top score), and garbage questions
# score moderately (breakfast → 0.27). The floor is therefore only a weak
# pre-filter for empty/degenerate retrieval; the GENERATION gate (NOT_IN_PAPER
# contract) carries the real refusal duty — see docs/specs/q3-spec.md §3.
REFUSAL_FLOOR = 0.10

# Metadata questions must be ABOUT authorship/title, not merely mention the
# word ("what did the AUTHORS have for breakfast?" is a semantic question).
_METADATA_RE = re.compile(
    r"(\bwho\s+(?:are|is|were|wrote|authored|created|made|published)\b"
    r"|\bwhose\b"
    r"|\b(?:name|list)\s+the\s+authors?\b"
    r"|\bhow\s+many\s+authors?\b"
    r"|\bauthors?'?s?\s+(?:names?|list)\b"
    r"|\bwhat(?:'s|\s+is)\s+the\s+title\b"
    r"|\btitle\s+of\s+(?:the|this)\b"
    r"|\bwritten\s+by\b)",
    re.I,
)
_REFERENCES_RE = re.compile(
    r"\b(references?|bibliograph\w*|works\s+cited|citations?\s+list|"
    r"papers?\s+cited|cite[sd]?\b|cited\s+in\b)\b",
    re.I,
)
_AUTHORS_FALLBACK = ["Authors not extracted for this paper."]


@dataclass
class RetrievalResult:
    path: str                       # "metadata" | "references" | "semantic" | "refused"
    citations: list[dict] = field(default_factory=list)
    context_blocks: list[dict] = field(default_factory=list)
    top_score: float | None = None
    question_type: str = "semantic"


def classify(question: str) -> str:
    """Route a question: metadata | references | semantic.

    References wins over metadata ("who wrote the papers cited here?" is a
    references question); semantic is the default path.
    """
    if _REFERENCES_RE.search(question):
        return "references"
    if _METADATA_RE.search(question):
        return "metadata"
    return "semantic"


def _citation(label: str, text: str, position: int) -> dict:
    return {"position": position, "label": label, "snippet": text[:140]}


async def _semantic(record: dict, question: str, k: int = 4) -> RetrievalResult:
    index = record.get("index") or {}
    chunks = index.get("chunks") or []
    if not chunks:
        return RetrievalResult(
            path="refused",
            question_type="semantic",
            top_score=None,
            citations=[],
            context_blocks=[],
        )
    vectors = [c["vector"] for c in chunks]
    qv = (await embed_texts([question], input_type="query"))[0]
    scored = sorted(
        ((sum(a * b for a, b in zip(qv, v)), i) for i, v in enumerate(vectors)),
        reverse=True,
    )
    top_score = scored[0][0]
    if top_score < REFUSAL_FLOOR:
        return RetrievalResult(
            path="refused", question_type="semantic", top_score=top_score
        )
    # Hybrid fusion (spec §2): dense cosine alone misses lexical mismatches —
    # "How was this tested?" shares no wording with the evaluation prose
    # ("BLEU", "newstest2014 test sets"), so the eval chunks ranked below
    # training chunks and the generator honestly refused a paper that DID
    # answer the question (live failure, 2026-09-17). Fuse dense ranks with
    # BM25-style keyword ranks via Reciprocal Rank Fusion.
    dense_ranked = [i for _, i in scored]
    kw_ranked = _keyword_rank(chunks, question)
    fused = _rrf([dense_ranked, kw_ranked])
    # Blocks keep their dense cosine for display (citations panel / trace);
    # ORDER comes from the fusion, not the score.
    dense_by_idx = {i: s for s, i in scored}
    picked = [(dense_by_idx.get(i, 0.0), i) for i in fused[:k]]
    res = RetrievalResult(path="semantic", question_type="semantic", top_score=top_score)
    for pos, (score, i) in enumerate(picked, start=1):
        c = chunks[i]
        block = {
            "position": pos,
            "label": c["section"],
            "text": c["text"],
            "score": round(score, 4),
        }
        res.context_blocks.append(block)
        res.citations.append(
            {"position": pos, "label": c["section"], "snippet": c["text"][:140]}
        )
    return res


def _keywords(text: str) -> list[str]:
    """Lowercased, stopword-stripped, crudely stemmed tokens (tested→test)."""
    stop = {
        "the", "a", "an", "of", "is", "are", "was", "were", "this", "that",
        "what", "how", "who", "when", "where", "why", "in", "on", "for", "to",
        "and", "it", "with", "as", "by", "be", "at", "or", "from", "did", "do",
        "does", "have", "has", "their", "they", "its", "about",
    }
    out: list[str] = []
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        if tok in stop or len(tok) < 3:
            continue
        for suf in ("ing", "ed", "es", "s"):
            if tok.endswith(suf) and len(tok) > len(suf) + 2:
                tok = tok[: -len(suf)]
                break
        out.append(tok)
    return out


def _keyword_rank(chunks: list[dict], question: str) -> list[int]:
    """Chunk indices ranked by a light BM25-style keyword score (IDF-weighted).

    Term matching is substring-aware in both directions (min length 4):
    "test" must hit "newstest2014"/"testing", "eval" must hit "evaluation" —
    exact-token matching missed the evaluation section entirely on the live
    run because the paper's testing evidence never uses the bare word "test".
    """
    q_terms = _keywords(question)
    if not q_terms:
        return []
    docs = [_keywords(c["text"]) for c in chunks]
    n = len(docs)
    df: Counter = Counter()
    for d in docs:
        df.update(set(d))

    def _matches(term: str, tok: str) -> bool:
        if term == tok:
            return True
        if len(term) >= 4 and (term in tok or tok in term):
            return True
        return False

    scored: list[tuple[float, int]] = []
    for i, d in enumerate(docs):
        tf = Counter(d)
        s = 0.0
        for t in set(q_terms):
            hits = sum(tf[tok] for tok in tf if _matches(t, tok))
            if hits:
                # IDF from exact-token df is a lower bound; substring hits can
                # only make a term more common, so reuse it as-is.
                idf = math.log(1 + n / (1 + df[t]))
                s += idf * hits / (hits + 1.2)
        if s > 0:
            scored.append((s, i))
    scored.sort(reverse=True)
    return [i for _, i in scored]


def _rrf(rank_lists: list[list[int]], k_const: int = 60) -> list[int]:
    """Reciprocal Rank Fusion over any number of ranked index lists."""
    agg: dict[int, float] = {}
    for ranked in rank_lists:
        if not ranked:
            continue
        for rank, i in enumerate(ranked):
            agg[i] = agg.get(i, 0.0) + 1.0 / (k_const + rank + 1)
    return [i for i, _ in sorted(agg.items(), key=lambda kv: (-kv[1], kv[0]))]


def _references(record: dict) -> RetrievalResult:
    refs = (record.get("index") or {}).get("references")
    if not refs:
        return RetrievalResult(path="refused", question_type="references")
    res = RetrievalResult(path="references", question_type="references")
    res.citations.append(_citation("References (section)", refs, 1))
    res.context_blocks.append(
        {"position": 1, "label": "References (section)", "text": refs, "score": None}
    )
    return res


def _metadata(record: dict) -> RetrievalResult:
    title = record.get("title") or "Untitled"
    authors = record.get("authors") or _AUTHORS_FALLBACK
    block_text = f"Title: {title}\nAuthors: {', '.join(authors)}"
    res = RetrievalResult(path="metadata", question_type="metadata")
    res.citations.append(_citation("Page 1 (title block)", block_text, 1))
    res.context_blocks.append(
        {"position": 1, "label": "Page 1 (title block)", "text": block_text, "score": None}
    )
    return res


async def retrieve(record: dict, question: str) -> RetrievalResult:
    """Route + retrieve. Async: embed_texts offloads encoding internally."""
    path = classify(question)
    if path == "metadata":
        return _metadata(record)
    if path == "references":
        res = _references(record)
        if res.path == "refused":  # no References captured → try semantic over chunks
            return await _semantic(record, question)
        return res
    return await _semantic(record, question)
