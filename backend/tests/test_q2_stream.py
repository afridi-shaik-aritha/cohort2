"""Q2 — HTTP API: upload/analyze/list/get/cancel + quality-gate status codes.

The mock provider is forced via LLM_PROVIDER=mock so no external calls happen.
"""
import fitz
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _pdf_bytes():
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    lines = ["Paper Title Here", "Ada Lovelace", "Abstract"]
    lines += [
        f"Paragraph {i} describes the method, datasets, and evaluation results."
        for i in range(40)
    ]
    for ln in lines:
        page.insert_text((72, y), ln)
        y += 18
    return doc.tobytes()


def test_q2_upload_analyze_and_reload(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("Q2_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    up = client.post(
        "/api/q2/papers", files={"file": ("p.pdf", _pdf_bytes(), "application/pdf")}
    )
    assert up.status_code == 200, up.text
    pid = up.json()["paper_id"]
    assert up.json()["title"] == "Paper Title Here"
    assert client.get("/api/q2/papers").json()["papers"][0]["paper_id"] == pid
    with client.stream("POST", f"/api/q2/papers/{pid}/analyze") as r:
        body = "".join(r.iter_text())
    types = [
        line[5:].strip() and __import__("json").loads(line[5:])["type"]
        for line in body.splitlines()
        if line.startswith("data:")
    ]
    assert types[0] == "run"
    assert types[-1] == "done"
    assert types.count("section_done") == 4
    assert types.count("section_start") == 4
    rec = client.get(f"/api/q2/papers/{pid}").json()
    assert rec["sections"]["summary"] != ""
    h = client.get("/api/q2/health").json()
    assert h["tracing"] is False  # keys removed for this test
    assert h["provider"] == "mock"


def test_q2_rejects_tiny_pdf(monkeypatch, tmp_path):
    monkeypatch.setenv("Q2_DATA_DIR", str(tmp_path))
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "hi")
    r = client.post(
        "/api/q2/papers", files={"file": ("t.pdf", doc.tobytes(), "application/pdf")}
    )
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "no_extractable_text"


def test_q2_rejects_non_pdf(monkeypatch, tmp_path):
    monkeypatch.setenv("Q2_DATA_DIR", str(tmp_path))
    r = client.post(
        "/api/q2/papers", files={"file": ("x.txt", b"hello world", "text/plain")}
    )
    assert r.status_code == 415


def test_q2_missing_paper_404():
    assert client.get("/api/q2/papers/p_nope").status_code == 404
