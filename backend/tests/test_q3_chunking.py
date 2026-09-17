"""Q3 — chunker: section-aware splitting with overlap; references excluded."""
from app.q3_rag_qa.chunking import chunk_sections
from app.q3_rag_qa.segmentation import Section, segment_text

from tests.test_q3_segmentation import _paper_text


def test_chunks_carry_section_labels():
    secs = segment_text(_paper_text())
    chunks = chunk_sections(secs)
    assert chunks, "expected chunks"
    assert all(c.section for c in chunks)
    labels = {c.section for c in chunks}
    assert "Abstract" in labels
    assert "5 Results" in labels


def test_chunk_sizes_within_bounds():
    secs = segment_text(_paper_text())
    chunks = chunk_sections(secs)
    for c in chunks:
        assert len(c.text) <= 1_600, f"chunk too big: {len(c.text)}"
        assert len(c.text) > 0


def test_overlap_present_between_adjacent_chunks():
    long_text = " ".join(
        f"Sentence number {i} talks about attention mechanisms and BLEU scores."
        for i in range(80)
    )
    secs = [Section(text_label="1 Long", text="1 Long\n" + long_text, start=0)]
    chunks = chunk_sections(secs)
    assert len(chunks) > 1, "long section must split"
    # overlap: tail of chunk 0 appears at the head of chunk 1
    tail = chunks[0].text[-120:]
    assert tail.strip()[:60] in chunks[1].text


def test_short_section_single_chunk():
    body = "Tiny body, but a full sentence of prose, well over the minimum."
    secs = [Section(text_label="1 Short", text=f"1 Short\n{body}", start=0)]
    (chunk,) = chunk_sections(secs)
    assert body in chunk.text


def test_heading_only_stubs_are_skipped():
    # A bare "6\nResults" header with no prose carries no evidence and must not
    # become a retrievable chunk (live failure: it outranked real eval chunks).
    secs = [
        Section(text_label="6 Results", text="6\nResults", start=0),
        Section(text_label="6.1 Real", text="6.1 Real\nWe evaluate BLEU on WMT 2014 translation tasks.", start=2),
    ]
    chunks = chunk_sections(secs)
    assert [c.section for c in chunks] == ["6.1 Real"]


def test_references_excluded_from_chunks():
    secs = segment_text(_paper_text())
    chunks = chunk_sections(secs)
    assert all("Jimmy Lei Ba" not in c.text for c in chunks)
