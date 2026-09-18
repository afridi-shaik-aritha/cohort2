"""Filesystem storage for uploaded papers.

JSON files, not a database: the simplest thing that lets Q3 reuse the same
artifact (text + metadata + sections). `owner_id` is stored from Q2 onward so
Q6's access-scoped retrieval does not require re-ingesting anything.
"""
import json
import os
import uuid
from pathlib import Path

_DEFAULT_DIR = Path(__file__).resolve().parents[2] / "data" / "papers"

DEFAULT_OWNER = "demo-user"


def _dir() -> Path:
    """Papers dir, resolved per call so the Q2_DATA_DIR env override works in
    tests that set it after import (import-time resolution made it useless)."""
    return Path(os.getenv("Q2_DATA_DIR") or _DEFAULT_DIR)


def new_paper_id() -> str:
    return "p_" + uuid.uuid4().hex[:8]


def _ensure_dir() -> Path:
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path_for(paper_id: str) -> Path:
    return _dir() / f"{paper_id}.json"


def save_paper(record: dict) -> None:
    _ensure_dir()
    path = _path_for(record["paper_id"])
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False)
    tmp.replace(path)


def load_paper(paper_id: str) -> dict | None:
    path = _path_for(paper_id)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def list_papers(owner_id: str | None = None) -> list[dict]:
    """Summary rows only — never the full text, so listings stay cheap."""
    rows = []
    for rec in all_records():
        if owner_id and rec.get("owner_id") != owner_id:
            continue
        rows.append(
            {
                "paper_id": rec.get("paper_id"),
                "title": rec.get("title", ""),
                "authors": rec.get("authors", []),
                "pages": rec.get("pages", 0),
                "chars": rec.get("chars", 0),
                "created_at": rec.get("created_at", ""),
                "has_sections": bool(rec.get("sections")),
            }
        )
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return rows


def all_records() -> list[dict]:
    """Full records, unordered — the raw store Q6's scope filter walks.

    Q6 applies its owner filter to the result; nothing else should use this
    without an equivalent filter.
    """
    papers_dir = _dir()
    if not papers_dir.exists():
        return []
    out: list[dict] = []
    for path in papers_dir.glob("p_*.json"):
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
        except Exception:
            continue
        if isinstance(rec, dict) and rec.get("paper_id"):
            out.append(rec)
    return out