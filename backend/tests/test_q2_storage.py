"""Q2 — filesystem paper storage: roundtrip, owner filtering, listing."""
from app.q2_paper_inference import storage


def test_roundtrip_and_list(tmp_path, monkeypatch):
    monkeypatch.setenv("Q2_DATA_DIR", str(tmp_path))  # resolved per call by storage._dir()
    pid = storage.new_paper_id()
    assert pid.startswith("p_") and len(pid) == 10
    storage.save_paper({
        "paper_id": pid, "owner_id": "demo-user", "title": "T",
        "authors": ["A"], "pages": 1, "chars": 10, "created_at": "now",
        "sections": {"summary": "s"}, "text": "t",
    })
    rec = storage.load_paper(pid)
    assert rec["title"] == "T" and rec["owner_id"] == "demo-user"
    rows = storage.list_papers()
    assert [p["paper_id"] for p in rows] == [pid]
    assert "text" not in rows[0]  # summaries never leak full text
    assert storage.list_papers(owner_id="someone-else") == []
    assert storage.load_paper("p_missing") is None
