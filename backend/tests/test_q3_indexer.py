"""Q3 — indexer: build → persist → read back, with a mocked embedder."""
import asyncio
import json

from app.q3_rag_qa import indexer
from app.shared import llm_text


def _record(tmp_path, monkeypatch, text=None):
    monkeypatch.setenv("Q2_DATA_DIR", str(tmp_path))
    from app.q2_paper_inference import storage

    rec = {
        "paper_id": "p_idx",
        "owner_id": "demo-user",
        "title": "Attention Is All You Need",
        "authors": ["A. Vaswani"],
        "text": text or (
            "Attention Is All You Need\nA. Vaswani\n\n"
            "Abstract\nWe propose the Transformer which drops recurrence.\n\n"
            "1\nIntroduction\nRecurrent models dominated sequence modeling.\n\n"
            "2\nResults\nWe test on WMT 2014 with BLEU scores on 8 GPUs.\n\n"
            "References\n[1] Ba et al. Layer normalization.\n"
        ),
    }
    storage.save_paper(rec)
    return rec, storage


async def _fake_embed(texts, input_type="passage"):
    return [llm_text._hash_vector(t) for t in texts]


def test_build_index_persists_into_record(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_text, "embed_texts", _fake_embed)
    rec, storage = _record(tmp_path, monkeypatch)
    summary = asyncio.run(indexer.build_index(rec))
    assert summary["chunks"] > 0
    assert summary["has_references"] is True
    assert summary["embedding_provider"] == "local"
    loaded = json.loads((tmp_path / "p_idx.json").read_text())
    assert loaded["index"]["chunks"][0]["vector"]
    assert "[1] Ba" in loaded["index"]["references"]


def test_get_index_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_text, "embed_texts", _fake_embed)
    rec, _ = _record(tmp_path, monkeypatch)
    assert indexer.get_index(rec) is None
    asyncio.run(indexer.build_index(rec))
    idx = indexer.get_index(rec)
    assert idx and idx["chunks"] and all("vector" in c for c in idx["chunks"])
