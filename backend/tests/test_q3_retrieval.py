"""Q3 — retrieval router: three deliberate paths + refusal gating.

Deterministic by construction: chunk and query vectors are scripted directly,
so cosine scores (and the 0.18 gate) are exact, not properties of a real
embedder's scale.
"""
import asyncio
import math

import pytest

from app.q3_rag_qa import retrieval


def _unit(vec):
    n = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / n for v in vec]


def _topic_vec(topic: str, dim: int = 384):
    """A deterministic unit vector per topic string — distinct topics are
    (near-)orthogonal, identical topics are identical."""
    h = int.from_bytes(topic.encode(), "big")
    v = [0.0] * dim
    v[h % dim] = 1.0
    if dim > (h // dim) % dim:
        v[(h // dim) % dim] = 0.5
    return _unit(v)


def _record(chunks, references=None, title="Attention Is All You Need",
            authors=("A. Vaswani", "N. Shazeer")):
    return {
        "paper_id": "p_test",
        "title": title,
        "authors": list(authors),
        "index": {"chunks": chunks, "references": references},
    }


def _chunk(text, topic, section="1 Intro"):
    return {"id": "c", "section": section, "text": text, "vector": _topic_vec(topic)}


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch):
    """Query embedding maps a question to its scripted topic vector.

    The fake is async because the REAL embed_texts is async — the test double
    must mirror the production contract (a sync double once masked exactly
    that bug)."""
    async def _fake(texts, input_type="passage"):
        return [_topic_vec(_topic_of(t)) for t in texts]

    monkeypatch.setattr(retrieval, "embed_texts", _fake)


def _topic_of(text: str) -> str:
    text = text.lower()
    if "bleu" in text or "wmt" in text or "gpu" in text or "tested" in text:
        return "eval"
    if "breakfast" in text or "egg" in text or "toast" in text:
        return "breakfast"
    if "reference" in text or "normalization" in text:
        return "refs"
    return "misc"


# --- classification ----------------------------------------------------------

@pytest.mark.parametrize(
    "question,expected",
    [
        ("Who are the authors?", "metadata"),
        ("who wrote this paper?", "metadata"),
        ("What is the title of the paper?", "metadata"),
        ("What are the references?", "references"),
        ("List the bibliography.", "references"),
        ("Which works are cited in section 2?", "references"),
        ("How was this tested?", "semantic"),
        ("What results did the model achieve on translation?", "semantic"),
        ("What did the authors have for breakfast?", "semantic"),
    ],
)
def test_classification_table(question, expected):
    assert retrieval.classify(question) == expected


# --- metadata path -----------------------------------------------------------

def test_metadata_path_uses_record_not_retrieval():
    rec = _record([])
    res = asyncio.run(retrieval.retrieve(rec, "Who are the authors?"))
    assert res.path == "metadata"
    assert res.citations[0]["label"] == "Page 1 (title block)"
    assert "A. Vaswani" in res.context_blocks[0]["text"]


def test_metadata_path_when_authors_missing():
    rec = _record([], authors=[])
    res = asyncio.run(retrieval.retrieve(rec, "Who wrote this?"))
    assert res.path == "metadata"  # answered with the fallback, not a refusal


# --- references path ---------------------------------------------------------

def test_references_path_returns_block_verbatim():
    refs = "[1] Ba et al. Layer normalization.\n[2] Vaswani et al. Attention."
    rec = _record([], references=refs)
    res = asyncio.run(retrieval.retrieve(rec, "What are the references?"))
    assert res.path == "references"
    assert res.citations[0]["label"] == "References (section)"
    assert res.context_blocks[0]["text"] == refs


def test_references_missing_falls_back_to_semantic():
    rec = _record([_chunk("Ba, Kiros and Hinton. Layer normalization.", "refs",
                          section="References")])
    res = asyncio.run(retrieval.retrieve(rec, "What are the references?"))
    assert res.path == "semantic"  # fallback path, not a hard refusal


# --- semantic path + gating --------------------------------------------------

def test_semantic_retrieval_ranks_relevant_chunk_first():
    rec = _record([
        _chunk("We evaluate BLEU on WMT with 8 GPUs.", "eval", section="5 Results"),
        _chunk("The encoder stacks self-attention layers.", "misc", section="3 Model"),
        _chunk("Unrelated filler sentence about toast.", "breakfast", section="1 Intro"),
    ])
    res = asyncio.run(retrieval.retrieve(rec, "How was this tested? BLEU on WMT?"))
    assert res.path == "semantic"
    assert res.context_blocks[0]["label"] == "5 Results"
    assert res.top_score >= retrieval.REFUSAL_FLOOR


def test_semantic_refusal_below_floor():
    rec = _record([_chunk("Attention layers replace recurrence entirely.", "misc",
                          section="3 Model")])
    res = asyncio.run(retrieval.retrieve(rec, "What did the authors have for breakfast?"))
    assert res.path == "refused"
    assert res.top_score is not None and res.top_score < retrieval.REFUSAL_FLOOR


def test_no_index_refuses():
    res = asyncio.run(retrieval.retrieve(
        {"paper_id": "p", "title": "T", "authors": []}, "How was this tested?"
    ))
    assert res.path == "refused"


def test_empty_index_refuses():
    res = asyncio.run(retrieval.retrieve(_record([]), "How was this tested?"))
    assert res.path == "refused"
