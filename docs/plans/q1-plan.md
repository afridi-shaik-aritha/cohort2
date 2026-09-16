# Q1 Streaming Chat UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Q1: FastAPI SSE chat endpoint with tool-call events + a single frontend app (Home + Q1 page) that streams tokens and shows tool-call gaps, with working cancel.

**Architecture:** Stateless FastAPI backend; one open HTTP stream per turn (`LLM stream → tool_call_start → await tool → tool_call_end → final stream → done`). Pluggable `llm_client` (mock default; openai/anthropic when keys set). Frontend: Vite + React, `fetch` + ReadableStream SSE parser, no dep on EventSource.

**Tech Stack:** Python 3.10, FastAPI 0.124, uvicorn 0.33, sse-starlette 3.3.4 (installed), openai 2.30 / anthropic 0.89 (installed, optional at runtime), Vite + React 18 + TypeScript, `npm`.

**Spec:** `docs/specs/q1-spec.md` (APPROVED 2026-09-16)

## Global Constraints

- One repo, one system; follow `README.md` layout (`backend/app/q1_streaming`, `backend/app/shared`, `frontend/src/pages`, `frontend/src/components`, `frontend/src/styles`).
- Design tokens in `docs/design/design-tokens.md` are locked — frontend must use the CSS vars, no new palette.
- Event protocol in spec §1 is exact — field names/shapes must match byte-for-byte.
- Q2–Q6 pages stay disabled "Coming soon"; do not build them.
- Max 3 tool rounds per turn; stateless server + ephemeral run_id→cancel map (TTL 5 min).

---

## File map

- Create: `backend/requirements.txt` — pins fastapi, uvicorn, sse-starlette, pydantic, httpx, openai, anthropic, python-dotenv, pytest, httpx.
- Create: `backend/.env.example` — `LLM_PROVIDER=mock`, `OPENAI_API_KEY=`, `ANTHROPIC_API_KEY=`, `OPENAI_MODEL=gpt-4o-mini`, `ANTHROPIC_MODEL=claude-3-5-haiku-latest`.
- Create: `backend/app/__init__.py`, `backend/app/main.py` — FastAPI app, CORS for Vite dev, mounts q1 router, `GET /api/health`.
- Create: `backend/app/shared/__init__.py`, `backend/app/shared/protocol.py` — event constructors (`run_event`, `token_event`, `tool_start`, `tool_end`, `error_event`, `done_event`, `format_sse`).
- Create: `backend/app/shared/cancel_registry.py` — `create_run() -> str`, `get_event(run_id)`, `cancel_run(run_id) -> bool`, TTL prune.
- Create: `backend/app/shared/llm_client.py` — `Message`, `ToolDef`, `get_provider()`, `stream_chat(messages, tools, run_id) -> AsyncIterator[dict]` yielding `{"kind":"text","text":...}` / `{"kind":"tool_call","name":...,"arguments":{}}`; mock/openai/anthropic backends.
- Create: `backend/app/q1_streaming/__init__.py`, `backend/app/q1_streaming/tools.py` — TOOL_DEFS + `TOOL_FUNCS` (get_weather, calculator, get_current_time) + `detect_tool_mock(text)` + `run_tool(name, args) -> (summary, status)`.
- Create: `backend/app/q1_streaming/router.py` — `POST /api/q1/chat`, `DELETE /api/q1/runs/{run_id}/cancel`, `GET /api/q1/health`.
- Create: `backend/tests/__init__.py`, `backend/tests/test_protocol.py`, `backend/tests/test_tools.py`, `backend/tests/test_q1_stream.py`.
- Create: `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tsconfig.json`, `frontend/index.html`.
- Create: `frontend/src/main.tsx`, `frontend/src/App.tsx` (routes `/`, `/q1`), `frontend/src/styles/tokens.css` (copy of spec tokens), `frontend/src/styles/app.css`.
- Create: `frontend/src/pages/Home.tsx`, `frontend/src/pages/Q1Streaming.tsx`, `frontend/src/sse.ts` (fetch SSE parser + `streamChat()`).
### Task 1: Backend scaffold + protocol + cancel registry

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/.env.example`
- Create: `backend/app/__init__.py` (empty)
- Create: `backend/app/shared/__init__.py` (empty)
- Create: `backend/app/shared/protocol.py`
- Create: `backend/app/shared/cancel_registry.py`
- Create: `backend/app/main.py`
- Test: `backend/tests/__init__.py` (empty), `backend/tests/test_protocol.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `protocol.run_event(run_id)`, `token_event(text)`, `tool_start(id,tool,label,args)`, `tool_end(id,tool,status,summary,duration_ms)`, `error_event(message,recoverable)`, `done_event(stop_reason,usage)`, `format_sse(payload)->str`; `cancel_registry.create_run()`, `get_event(run_id)`, `cancel_run(run_id)->bool`.

- [ ] **Step 1: Write requirements + env example**

`backend/requirements.txt`:
```
fastapi==0.124.4
uvicorn==0.33.0
sse-starlette==3.3.4
pydantic>=2.0
httpx>=0.27
python-dotenv>=1.0
openai==2.30.0
anthropic==0.89.0
pytest>=8.0
anyio>=4.0
```

`backend/.env.example`:
```
LLM_PROVIDER=mock
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-3-5-haiku-latest
```

- [ ] **Step 2: Write the failing protocol test**

`backend/tests/test_protocol.py`:
```python
from app.shared.protocol import (
    run_event, token_event, tool_start, tool_end,
    error_event, done_event, format_sse,
)

def test_event_shapes():
    assert run_event("r_1") == {"type": "run", "data": {"run_id": "r_1"}}
    assert token_event("Hel") == {"type": "token", "data": {"text": "Hel"}}
    s = tool_start("call_1", "get_weather", "Checking weather for Paris…", {"city": "Paris"})
    assert s["type"] == "tool_call_start" and s["data"]["id"] == "call_1"
    e = tool_end("call_1", "get_weather", "ok", "Paris: 18C, cloudy", 412)
    assert e["data"]["status"] == "ok" and e["data"]["duration_ms"] == 412
    assert error_event("boom", True)["data"]["recoverable"] is True
    d = done_event("completed", {"input_tokens": 1, "output_tokens": 2})
    assert d["data"]["stop_reason"] == "completed"
    assert format_sse({"type": "token", "data": {"text": "x"}}) == 'data: {"type": "token", "data": {"text": "x"}}\n\n'
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_protocol.py -v`
Expected: FAIL with "No module named 'app.shared.protocol'".

- [ ] **Step 4: Write protocol + cancel registry + main.py**

`backend/app/shared/protocol.py`:
```python
"""Q1 SSE event constructors. Shapes must match docs/specs/q1-spec.md section 1."""
import json
from typing import Optional

def run_event(run_id: str) -> dict:
    return {"type": "run", "data": {"run_id": run_id}}

def token_event(text: str) -> dict:
    return {"type": "token", "data": {"text": text}}

def tool_start(call_id: str, tool: str, label: str, args: dict) -> dict:
    return {"type": "tool_call_start", "data": {"id": call_id, "tool": tool, "label": label, "args": args}}

def tool_end(call_id: str, tool: str, status: str, summary: str, duration_ms: int) -> dict:
    return {"type": "tool_call_end", "data": {"id": call_id, "tool": tool, "status": status, "summary": summary, "duration_ms": duration_ms}}

def error_event(message: str, recoverable: bool) -> dict:
    return {"type": "error", "data": {"message": message, "recoverable": recoverable}}

def done_event(stop_reason: str, usage: Optional[dict]) -> dict:
    return {"type": "done", "data": {"stop_reason": stop_reason, "usage": usage}}

def format_sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
```

`backend/app/shared/cancel_registry.py`:
```python
"""Ephemeral run_id -> asyncio.Event map with 5-min TTL. No persistence."""
import asyncio
import secrets
import time

_TTL_S = 5 * 60
_runs: dict[str, tuple[asyncio.Event, float]] = {}

def create_run() -> tuple[str, asyncio.Event]:
    run_id = "r_" + secrets.token_hex(4)
    ev = asyncio.Event()
    _runs[run_id] = (ev, time.monotonic())
    _prune()
    return run_id, ev

def get_event(run_id: str):
    item = _runs.get(run_id)
    return item[0] if item else None

def cancel_run(run_id: str) -> bool:
    item = _runs.get(run_id)
    if not item:
        return False
    item[0].set()
    return True

def is_cancelled(run_id: str) -> bool:
    item = _runs.get(run_id)
    return bool(item and item[0].is_set())

def _prune() -> None:
    now = time.monotonic()
    for k in [k for k, (_, ts) in _runs.items() if now - ts > _TTL_S]:
        del _runs[k]
```

`backend/app/main.py`:
```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="AI Fundamentals Assignment System")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health() -> dict:
    return {"ok": True}
```

- [ ] **Step 5: Run tests, expect PASS**

Run: `cd backend && python -m pytest tests/test_protocol.py -v`
Expected: PASS (1 passed).

- [ ] **Step 6: Commit**

```bash
cd /Users/shaikafridi/Desktop/COHORT-2 && git add backend/requirements.txt backend/.env.example backend/app/__init__.py backend/app/main.py backend/app/shared/__init__.py backend/app/shared/protocol.py backend/app/shared/cancel_registry.py backend/tests/__init__.py backend/tests/test_protocol.py && git commit -m "feat(q1): backend scaffold, SSE protocol, cancel registry"
```

