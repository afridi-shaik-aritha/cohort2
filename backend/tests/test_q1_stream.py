import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _parse_sse(body: str) -> list[dict]:
    events = []
    for chunk in body.split("data:"):
        chunk = chunk.strip()
        if not chunk:
            continue
        line = chunk.split("\n")[0]
        try:
            events.append(json.loads(line))
        except Exception:
            pass
    return events


def test_chat_tool_roundtrip(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    r = client.post(
        "/api/q1/chat", json={"messages": [{"role": "user", "content": "What's the weather in Paris?"}]}
    )
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    types = [e.get("type") for e in _parse_sse(r.text)]
    assert types[0] == "run"
    assert "token" in types
    assert "tool_call_start" in types
    assert "tool_call_end" in types
    assert types[-1] == "done"
    start = next(e for e in _parse_sse(r.text) if e.get("type") == "tool_call_start")
    assert start["data"]["tool"] == "get_weather"


def test_chat_plain_no_tool(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    r = client.post("/api/q1/chat", json={"messages": [{"role": "user", "content": "zxqw hello there jjj"}]})
    types = [e.get("type") for e in _parse_sse(r.text)]
    assert "token" in types
    assert "tool_call_start" not in types
    assert types[-1] == "done"


def test_cancel_unknown_run():
    r = client.delete("/api/q1/runs/r_nope/cancel")
    assert r.json() == {"ok": False}


def test_health(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    assert client.get("/api/health").json() == {"ok": True}
    h = client.get("/api/q1/health").json()
    assert h["provider"] == "mock"
