"""Q2 — section prompts, chunking strategy, and map-reduce digest prompts."""
from app.q2_paper_inference.prompts import (
    SECTIONS,
    build_digest_messages,
    build_messages,
    chunk_text,
)


def test_section_order_and_registers():
    assert [s["key"] for s in SECTIONS] == [
        "technical", "intuition", "prerequisites", "summary",
    ]
    sys_p, user_p = build_messages("prerequisites", title="T", authors=["A"], context="BODY")
    assert "prerequisite" in (sys_p.content + user_p.content).lower()
    assert "BODY" in user_p.content and "T" in user_p.content


def test_chunking_overlap_and_bounds():
    text = "x" * 20_000
    chunks = chunk_text(text)
    assert len(chunks) == 3  # 8000, then 7000-step with 1000 overlap
    assert all(len(c) <= 8000 for c in chunks)
    assert len(chunk_text("short")) == 1


def test_digest_messages_shape():
    sys_p, user_p = build_digest_messages("chunk body", index=0, total=2)
    assert "Excerpt 1 of 2" in user_p.content
    assert "chunk body" in user_p.content
