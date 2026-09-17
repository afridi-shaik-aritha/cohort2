"""Q3 — section segmentation + references extraction.

The fixture mimics how PyMuPDF extracts a real academic paper: heading numbers
and titles as separate lines, table cells emitting bare numbers, author emails
interrupting the title block.
"""
from app.q3_rag_qa.segmentation import extract_references, segment_text


def _paper_text() -> str:
    lines = [
        "Attention Is All You Need",
        "Ashish Vaswani, Noam Shazeer, Niki Parmar",
        "",
        "Abstract",
        "The dominant sequence transduction models are based on recurrent networks.",
        "We propose the Transformer, which dispenses with recurrence entirely.",
        "",
        "1",
        "Introduction",
        "Recurrent neural networks have long dominated sequence modeling.",
        "This gate and cell state allows the model to persist information.",
        "",
        "2",
        "Background",
        "Barzilay and Lapata propose entropy rate constancy.",
        "We compare self-attention layers to recurrent and convolutional layers.",
        "",
        "3",
        "Model Architecture",
        "Most competitive sequence transduction models have an encoder-decoder structure.",
        "The Transformer follows this overall architecture using stacked layers.",
        "",
        "64",
        "32",
        "Table 1: hyperparameters",
        "",
        "4",
        "Training",
        "We trained on the WMT 2014 English-German translation task.",
        "All models were trained on a machine with 8 NVIDIA Pascal GPUs.",
        "",
        "5",
        "Results",
        "Our model achieves 28.4 BLEU on WMT 2014 English-to-German.",
        "On the WMT 2014 English-to-French task, our model establishes 41.8.",
        "",
        "6",
        "Conclusion",
        "The Transformer achieves better quality with far less training compute.",
        "We plan to extend it to other input and output modalities.",
        "",
        "References",
        "[1] Jimmy Lei Ba, Jamie Ryan Kiros, and Geoffrey E Hinton. Layer normalization.",
        "[2] Dzmitry Bahdanau, Kyunghyun Cho, and Yoshua Bengio. Neural machine translation.",
        "[3] Kyunghyun Cho, Bart van Merrienboer. Learning phrase representations.",
    ]
    return "\n".join(lines)


def test_expected_sections_found():
    secs = segment_text(_paper_text())
    labels = [s.text_label for s in secs]
    assert "Title & Authors" in labels
    assert "Abstract" in labels
    for expected in ["1 Introduction", "2 Background", "3 Model Architecture",
                     "4 Training", "5 Results", "6 Conclusion", "References"]:
        assert expected in labels
    # table cell noise must NOT become a heading
    assert not any("64" in l or "32" in l for l in labels)
    # title lines consumed by numbered headings must not spawn duplicate sections
    assert labels.count("Introduction") == 0
    assert labels.count("Conclusion") == 0
    assert "7 Conclusion" not in labels  # numbering must be monotonic


def test_section_bodies_bounded_correctly():
    secs = {s.text_label: s for s in segment_text(_paper_text())}
    assert "Transformer" in secs["Abstract"].text
    assert "WMT 2014 English-German" in secs["4 Training"].text
    assert "28.4 BLEU" in secs["5 Results"].text
    assert "BLEU" not in secs["4 Training"].text  # no bleed between sections


def test_table_noise_stays_inside_its_section():
    secs = {s.text_label: s for s in segment_text(_paper_text())}
    assert "Table 1: hyperparameters" in secs["3 Model Architecture"].text


def test_preamble_isolated_as_title_block():
    secs = segment_text(_paper_text())
    assert secs[0].text_label == "Title & Authors"
    assert "Ashish Vaswani" in secs[0].text


def test_extract_references_returns_verbatim_block():
    refs = extract_references(segment_text(_paper_text()))
    assert refs is not None
    assert "[1] Jimmy Lei Ba" in refs
    assert "Learning phrase representations" in refs
    assert "BLEU" not in refs  # never captures non-references content


def test_no_references_section():
    text = "Title\n\n1\nIntro\nBody text here.\n"
    assert extract_references(segment_text(text)) is None
