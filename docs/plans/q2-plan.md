# Q2 Paper Inference Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upload a PDF → extract text + metadata → generate four independently-registered sections (technical, intuition, prerequisites, summary) with per-section streaming progress, every step traced in Langfuse (Cloud, no-op without keys).

**Architecture:** New backend module `app/q2_paper_inference/` (extraction → prompts → analyzer → router) plus two shared additions: `langfuse_client.py` (no-op-aware tracing) and `llm_text.py` (plain-text streaming completion with usage capture, leaving Q1's tool-calling streamer untouched). Papers persist as JSON under `backend/data/papers/`. Progress streams over SSE reusing Q1's event vocabulary + cancel registry. New frontend page `/q2` with a 2×2 panel layout.

**Tech Stack:** PyMuPDF 1.27 (`fitz`), FastAPI + `python-multipart` (uploads), Langfuse 4.15.4 SDK (Cloud), React + react-markdown/KaTeX (reused from Q1).

**Spec:** `docs/specs/q2-spec.md` (APPROVED 2026-09-16)

## Global Constraints (from spec — apply to every task)
- Langfuse **Cloud**, keys via `.env`; **must run with no keys** (tracing no-ops, UI shows "Observability: off").
- `MAX_INPUT_CHARS = 40_000`; `>40k → map-reduce` (8k chunks, 1k overlap, ≤250-word digests); `>400_000 → 413`; `<2_000 → 422`.
- **Four sequential calls**, one per section; generation names `section:technical|intuition|prerequisites|summary`; digests `chunk_digest:<n>`; extraction span `extract_pdf`.
- Persist `owner_id` (default `"demo-user"`) from day one; `paper_id` = `p_<8 hex>`.
- Do not modify Q1 behaviour: `protocol.py` additions purely additive; `llm_client.py` untouched.
- No new colour palette, no emoji; reuse `Icon.tsx`, `sse.ts`, design tokens.

## File map
- Create: `backend/app/shared/langfuse_client.py` — `tracing_enabled()`, `analysis_trace(...)`, `observation(...)`, `flush()`.
- Create: `backend/app/shared/llm_text.py` — `stream_text(messages, *, max_tokens, temperature)`, `provider_model()`, `estimate_tokens(text)`.
- Modify: `backend/app/shared/protocol.py` — add `section_start(...)`, `section_done(...)` (additive).
- Create: `backend/app/q2_paper_inference/{__init__,extraction,prompts,storage,analyzer,router}.py`.
- Modify: `backend/app/main.py` — mount Q2 router.
- Modify: `backend/requirements.txt` — add `pymupdf`, `langfuse`, `python-multipart`.
- Modify: `backend/.env.example` — Langfuse + Q2 knobs.
- Create: `backend/tests/test_q2_langfuse.py`, `test_q2_extraction.py`, `test_q2_prompts.py`, `test_q2_storage.py`, `test_q2_stream.py`.
- Create: `frontend/src/pages/Q2PaperInference.tsx`; modify `sse.ts`, `App.tsx`, `Home.tsx`, `styles/app.css`, `README.md`.
- Create: `backend/data/papers/.gitkeep`; `.gitignore` ignores `backend/data/papers/*.json`.
### Task 1: Langfuse client wrapper (no-op safe)

**Files:** Create `backend/app/shared/langfuse_client.py`; Test `backend/tests/test_q2_langfuse.py`

**Interfaces (produces):** `tracing_enabled() -> bool`; `analysis_trace(*, paper_id, title, owner_id, provider, model, metadata) -> ctx mgr`; `observation(*, name, as_type, model=None, input=None, metadata=None) -> ctx mgr` yielding an object with `.update(output=…, usage_details=…, metadata=…)`; `flush()`.

**Verified SDK facts (introspected, langfuse 4.15.4):** trace attributes via `langfuse.propagate_attributes(trace_name=…, user_id=…, session_id=…, tags=[…], metadata={…})`; observations via `langfuse.start_as_current_observation(name=…, as_type="span"|"generation", …)`; updates via `obs.update(output=…, usage_details={"input":n,"output":m,"total":n+m})`; `langfuse.flush()`. There is **no** `start_as_current_span` / `update_current_trace` in v4 — do not use them.

- [ ] **Step 1: Failing test**

```python
from app.shared import langfuse_client as lc

def test_disabled_without_keys(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert lc.tracing_enabled() is False
    with lc.analysis_trace(paper_id="p_1", title="T", owner_id="demo-user",
                           provider="mock", model="mock", metadata={}) as tr:
        assert tr is None
        with lc.observation(name="section:summary", as_type="generation") as obs:
            obs.update(output="hello", usage_details=None)   # must not raise
    lc.flush()

def test_enabled_with_keys(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    assert lc.tracing_enabled() is True
```

- [ ] **Step 2: Run** `cd backend && python -m pytest tests/test_q2_langfuse.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement** `langfuse_client.py`: `tracing_enabled()` (both keys set), `_client()` (lazy `Langfuse(public_key, secret_key, host=LANGFUSE_HOST default `https://cloud.langfuse.com`)`, returns `None` and logs once on any exception), `analysis_trace()` (no-op yields `None`; else enters `propagate_attributes(trace_name="q2.paper_analysis", user_id=owner_id, session_id=paper_id, tags=["q2","paper-analysis",provider], metadata=meta)` and yields the client), `observation()` (no-op yields `_NoOp()` whose `.update(**kw)` returns None; else `client.start_as_current_observation(name, as_type, model?, input, metadata)`; any exception → log + yield `_NoOp()` so tracing never breaks generation), `flush()` (best-effort `client.flush()`).

- [ ] **Step 4: Run** the test file → PASS (2 passed).
- [ ] **Step 5: Commit** `feat(q2): no-op-safe Langfuse client wrapper`

---

---
### Task 2: Text completion with usage capture (`llm_text.py`)

**Files:** Create `backend/app/shared/llm_text.py`; Test `backend/tests/test_q2_llm_text.py`

**Interfaces (produces):**
- `stream_text(messages, *, max_tokens=None, temperature=None) -> AsyncIterator[dict]` yielding `{"kind":"text","text":str}` chunks then exactly one `{"kind":"usage","usage":dict|None}`.
- `provider_model() -> tuple[str, str]` — `(provider, model)` for trace metadata/UI chip.
- `estimate_tokens(text) -> int` — `max(1, len(text)//4)`.

**Rules:** same provider selection as `llm_client.get_provider()`; OpenAI-compatible hosted providers send `stream_options={"include_usage": True}` (openai/groq/openrouter/deepinfra/nvidia), **LM Studio must not** (rejects unknown fields) so its usage is captured opportunistically from any chunk carrying it; Anthropic reads `get_final_message().usage`; mock emits canned text with `usage=None` (never fabricate numbers).

- [ ] **Step 1: Failing test**

```python
import pytest
from app.shared.llm_text import Message, estimate_tokens, provider_model, stream_text

@pytest.mark.anyio
async def test_mock_stream_yields_text_then_usage(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    chunks, usage_seen = [], "unset"
    async for item in stream_text([Message("user", "Technical summary please")]):
        if item["kind"] == "text":
            chunks.append(item["text"])
        else:
            usage_seen = item["usage"]
    assert "".join(chunks).strip() != ""
    assert usage_seen is None

def test_provider_model_and_estimate(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "lmstudio")
    monkeypatch.setenv("LMSTUDIO_MODEL", "liquid/lfm2.5-1.2b")
    assert provider_model() == ("lmstudio", "liquid/lfm2.5-1.2b")
    assert estimate_tokens("abcd" * 25) == 25
```

Create `backend/tests/conftest.py` with an `anyio_backend` fixture pinned to `"asyncio"` so `@pytest.mark.anyio` works.

- [ ] **Step 2: Run** `cd backend && python -m pytest tests/test_q2_llm_text.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement** `llm_text.py`: `Message` re-exported from `llm_client`; `_openai_compat_stream(base_url, api_key, model, messages, *, max_tokens, temperature, with_usage)` yielding text then usage; `_anthropic_stream(...)` (messages.stream → text deltas → `get_final_message().usage` → `{"input": u.input_tokens, "output": u.output_tokens, "total": sum}`); `_mock_stream(...)` (canned text, word-chunked with small sleeps, usage None); `stream_text()` dispatcher; `provider_model()`; `estimate_tokens()`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(q2): plain-text streaming completion with usage capture`

---

### Task 3: Protocol additions + extraction pipeline

**Files:** Modify `backend/app/shared/protocol.py`; Create `backend/app/q2_paper_inference/__init__.py`, `extraction.py`; Test `backend/tests/test_q2_extraction.py`

**Interfaces (produces):**
- `protocol.section_start(key, label, index, total) -> dict` → `{"type":"section_start","data":{"key":…,"label":…,"index":…,"total":…}}`
- `protocol.section_done(key, chars, usage, duration_ms) -> dict` → `{"type":"section_done","data":{"key":…,"chars":…,"usage":…,"duration_ms":…}}`
- `extraction.extract_pdf(data: bytes) -> Extraction` — dataclass `pages:int, chars:int, text:str, title:str, authors:list[str], warnings:list[str]`.
- `extraction.MIN_PAPER_CHARS = 2_000`, `MAX_PAPER_CHARS = 400_000`, `extraction.PdfError(Exception)` with `.code` ∈ `{"no_extractable_text","paper_too_large","bad_pdf"}`.

- [ ] **Step 1: Failing test** (PDFs built on the fly with `fitz`, so no fixtures ship)

```python
import fitz
import pytest
from app.q2_paper_inference.extraction import PdfError, extract_pdf

def _pdf(lines: list[str], pages: int = 1, font: float = 11) -> bytes:
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        y = 72
        for ln in lines:
            page.insert_text((72, y), ln, fontsize=font)
            y += font * 1.6
    data = doc.tobytes(); doc.close(); return data

def test_dehyphenation_and_metadata():
    first = ["Attention Is All You Need", "Ashish Vaswani", "Google Brain", "Abstract",
             "We propose a new architecture.", "attribu-", "tion mechanisms matter."]
    body = first + ["More body text about the model and its evaluation protocol." * 12]
    ex = extract_pdf(_pdf(body, pages=3))
    assert "attribution mechanisms" in ex.text
    assert ex.title == "Attention Is All You Need"
    assert "Ashish Vaswani" in ex.authors
    assert ex.pages == 3 and ex.chars == len(ex.text)

def test_running_header_removed():
    lines = ["arXiv:1706.03762v7 [cs.CL] 2 Aug 2023"] * 5 + ["Real content here. " * 60]
    ex = extract_pdf(_pdf(lines, pages=5))
    assert "arXiv:1706.03762v7" not in ex.text

def test_quality_gate_and_bad_pdf():
    with pytest.raises(PdfError) as e:
        extract_pdf(_pdf(["tiny"]))
    assert e.value.code == "no_extractable_text"
    with pytest.raises(PdfError):
        extract_pdf(b"not a pdf at all")
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `extraction.py`: `_dehyphenate` (`re.sub(r"(\w)-\n(\w)", r"\1\2", t)`); `_strip_running_lines` (>50% of pages, <80 chars, first/last non-empty line per page); `_drop_table_noise` (>60% non-alphanumeric and <4 words → drop); `_title_authors(doc)` (page-1 `get_text("dict")` spans: title = longest among top-3 largest font sizes excluding emails/affiliations, authors = following lines until `Abstract`/email/affiliation keyword, split on `,`/` and `/`*`, dedupe, cap 20, warn when heuristics fired); gate + ceiling raising `PdfError`; public `extract_pdf`.
### Task 4: Prompts + chunking

**Files:** Create `backend/app/q2_paper_inference/prompts.py`; Test `backend/tests/test_q2_prompts.py`

**Interfaces (produces):**
- `SECTIONS: list[dict]` — ordered `[{"key":"technical","label":"Technical summary","instruction":…}, {"key":"intuition","label":"Intuition",…}, {"key":"prerequisites","label":"Prerequisite learning",…}, {"key":"summary","label":"Summary",…}]` (order = generation order).
- `build_messages(section_key, *, title, authors, context) -> list[Message]`
- `chunk_text(text, *, size=8000, overlap=1000) -> list[str]`
- `DIGEST_INSTRUCTION: str` and `build_digest_messages(chunk, *, index, total) -> list[Message]`

- [ ] **Step 1: Failing test**

```python
from app.q2_paper_inference.prompts import SECTIONS, build_messages, chunk_text

def test_section_order_and_registers():
    assert [s["key"] for s in SECTIONS] == ["technical", "intuition", "prerequisites", "summary"]
    sys_p, user_p = build_messages("prerequisites", title="T", authors=["A"], context="BODY")
    assert "prerequisite" in (sys_p.content + user_p.content).lower()
    assert "BODY" in user_p.content and "T" in user_p.content

def test_chunking_overlap_and_bounds():
    text = "x" * 20_000
    chunks = chunk_text(text)
    assert len(chunks) == 3                      # 8000 + 7000-with-overlap style stepping
    assert all(len(c) <= 8000 for c in chunks)
    assert len(chunk_text("short")) == 1
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `prompts.py` with the four instructions copied verbatim from the spec's output contract (word ranges, registers, the `- **Name** — why` prerequisite format), a shared system prompt ("You summarize research papers. Output markdown only — no preamble, no meta-commentary."), `build_messages` returning `[system, user]`, `chunk_text` stepping `size - overlap`, and the digest prompt (≤250 words of factual content, tables/figures kept as text, no opinions) → `[system, user]`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(q2): section prompts + chunking`

---

### Task 5: Storage

**Files:** Create `backend/app/q2_paper_inference/storage.py`, `backend/data/papers/.gitkeep`; modify `.gitignore`; Test `backend/tests/test_q2_storage.py`

**Interfaces (produces):**
- `new_paper_id() -> str` (`"p_" + uuid4().hex[:8]`)
- `save_paper(record: dict) -> None`, `load_paper(paper_id: str) -> dict | None`, `list_papers(owner_id: str | None = None) -> list[dict]` (summary fields only: `paper_id,title,authors,pages,chars,created_at,has_sections`)
- `PAPERS_DIR: Path` (overridable via env `Q2_DATA_DIR` — the tests use it)

- [ ] **Step 1: Failing test**

```python
from app.q2_paper_inference import storage

def test_roundtrip_and_list(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "PAPERS_DIR", tmp_path)
    pid = storage.new_paper_id()
    assert pid.startswith("p_") and len(pid) == 10
    storage.save_paper({"paper_id": pid, "owner_id": "demo-user", "title": "T",
                        "authors": ["A"], "pages": 1, "chars": 10, "created_at": "now",
                        "sections": {"summary": "s"}, "text": "t"})
    rec = storage.load_paper(pid)
    assert rec["title"] == "T" and rec["owner_id"] == "demo-user"
    assert [p["paper_id"] for p in storage.list_papers()] == [pid]
    assert storage.list_papers(owner_id="someone-else") == []
    assert storage.load_paper("p_missing") is None
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `storage.py`: `PAPERS_DIR = Path(os.getenv("Q2_DATA_DIR", Path(__file__).resolve().parents[2] / "data" / "papers"))`, `_ensure_dir()`, save via `json.dump(..., indent=2, ensure_ascii=False)`, load with `None` on missing/corrupt, list sorted by `created_at` desc. Add `.gitkeep`, and to `.gitignore`: `backend/data/papers/*.json`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(q2): filesystem paper storage with owner_id`

---
- [ ] **Step 4: Run** new file → PASS, then full suite `python -m pytest tests/ -q` to prove Q1 still green.
### Task 6: Analyzer (4 sequential generations + map-reduce + tracing)

**Files:** Create `backend/app/q2_paper_inference/analyzer.py`; Test `backend/tests/test_q2_analysis.py`

**Interfaces (produces):**
- `analyze_events(record: dict, *, cancel_ev: asyncio.Event) -> AsyncIterator[dict]` yielding **protocol payload dicts** ready for `format_sse`: `section_start`, repeated `token` (data additionally carries `"section"` — a Q2 extension documented in the spec), `section_done`, then `done{stop_reason, totals}`.
- `MAX_INPUT_CHARS = 40_000`, `CHUNK_SIZE = 8_000`, `CHUNK_OVERLAP = 1_000`.

**Behaviour:** chooses `single_pass` (≤40k) or `map_reduce` (>40k → digest each chunk, traced as `chunk_digest:<n>`, sections then run on concatenated digests capped at 40k); wraps everything in `analysis_trace`; each section is `observation(name=f"section:{key}", as_type="generation", model=model)` with `.update(output=text, usage_details=usage)`; extraction recorded as `observation(name="extract_pdf", as_type="span")`; checks `cancel_ev` between sections and chunks, emitting `done{stop_reason:"cancelled"}`; `flush()` at the end.

- [ ] **Step 1: Failing test**

```python
import asyncio
import pytest
from app.q2_paper_inference.analyzer import analyze_events

def _record(text="Body text about the method and results. " * 40):
    return {"paper_id": "p_test", "owner_id": "demo-user", "title": "T",
            "authors": ["A"], "pages": 3, "chars": len(text), "text": text,
            "created_at": "now", "sections": {},
            "extraction": {"strategy": "single_pass", "warnings": []}}

@pytest.mark.anyio
async def test_four_sections_in_order(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    events = [e async for e in analyze_events(_record(), cancel_ev=asyncio.Event())]
    keys = [e["data"]["key"] for e in events if e["type"] == "section_start"]
    assert keys == ["technical", "intuition", "prerequisites", "summary"]
    assert [e["type"] for e in events].count("section_done") == 4
    assert events[-1]["type"] == "done"
    assert events[-1]["data"]["stop_reason"] == "completed"
    assert all("section" in e["data"] for e in events if e["type"] == "token")

@pytest.mark.anyio
async def test_cancel_stops_generation(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    ev = asyncio.Event(); out = []
    async for e in analyze_events(_record(), cancel_ev=ev):
        out.append(e)
        if e["type"] == "section_done":
            ev.set()
    assert out[-1]["data"]["stop_reason"] == "cancelled"
    assert len([e for e in out if e["type"] == "section_done"]) == 1
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `analyzer.py` (helpers `_strategy_for(text)`, `_digest_context(...)`, `_run_section(...)`).
- [ ] **Step 4: Run** → PASS; full suite green.
- [ ] **Step 5: Commit** `feat(q2): four-section analyzer with tracing and map-reduce`

---
- [ ] **Step 5: Commit** `feat(q2): protocol section events + PyMuPDF extraction pipeline`

---
### Task 7: Router + wiring

**Files:** Create `backend/app/q2_paper_inference/router.py`; modify `backend/app/main.py`, `backend/requirements.txt`, `backend/.env.example`; Test `backend/tests/test_q2_stream.py`

**Interfaces (produces):** the spec's endpoints — `POST /api/q2/papers`, `POST /api/q2/papers/{paper_id}/analyze` (SSE), `GET /api/q2/papers`, `GET /api/q2/papers/{paper_id}`, `GET /api/q2/health`, `DELETE /api/q2/runs/{run_id}/cancel`.

- [ ] **Step 1: Failing test**

```python
import fitz, json
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)

def _pdf_bytes():
    doc = fitz.open(); page = doc.new_page(); y = 72
    for ln in ["Paper Title Here", "Ada Lovelace", "Abstract",
               "Body about methods and results. " * 40]:
        page.insert_text((72, y), ln); y += 18
    return doc.tobytes()

def test_q2_upload_analyze_and_reload(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("Q2_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    up = client.post("/api/q2/papers", files={"file": ("p.pdf", _pdf_bytes(), "application/pdf")})
    assert up.status_code == 200, up.text
    pid = up.json()["paper_id"]
    assert client.get("/api/q2/papers").json()["papers"][0]["paper_id"] == pid
    with client.stream("POST", f"/api/q2/papers/{pid}/analyze") as r:
        body = "".join(r.iter_text())
    types = [json.loads(l[5:])["type"] for l in body.splitlines() if l.startswith("data:")]
    assert types[0] == "run" and types[-1] == "done"
    assert types.count("section_done") == 4
    assert client.get(f"/api/q2/papers/{pid}").json()["sections"]["summary"] != ""
    assert client.get("/api/q2/health").json()["tracing"] is False

def test_q2_rejects_tiny_pdf():
    doc = fitz.open(); doc.new_page().insert_text((72, 72), "hi")
    r = client.post("/api/q2/papers", files={"file": ("t.pdf", doc.tobytes(), "application/pdf")})
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "no_extractable_text"
```

- [ ] **Step 2: Run** → FAIL (routes missing).
- [ ] **Step 3: Implement** the router (upload via `UploadFile`, 25MB guard, `PdfError` → 422/413, `StreamingResponse` reusing `format_sse` + `cancel_registry`, persist after each section so a cancelled run keeps finished sections), mount it in `main.py`, add `pymupdf`/`langfuse`/`python-multipart` to `requirements.txt`, append the Langfuse + Q2 block to `.env.example`.
- [ ] **Step 4: Run** → PASS; then full suite.
- [ ] **Step 5: Commit** `feat(q2): upload/analyze API with SSE progress and cancel`

---

### Task 8: Frontend `/q2` page

**Files:** Create `frontend/src/pages/Q2PaperInference.tsx`; modify `frontend/src/sse.ts`, `App.tsx`, `Home.tsx`, `styles/app.css`

**Interfaces (produces):** route `/q2`; `sse.ts` gains `uploadPaper(file) -> Promise<PaperMeta>` and `streamAnalyze(paperId, onEvent, signal)` with `Q2Event` covering `run|section_start|token|section_done|error|done`.

- [ ] **Step 1:** Add TS types + fetch helpers in `sse.ts`, factoring the manual SSE frame loop into a local `parseFrames(res, onEvent)` — Q1's existing `streamChat` stays untouched.
- [ ] **Step 2:** Build the page: drop zone + file input → `uploadPaper` then `streamAnalyze`; four panel cards in a 2×2 grid with status chips (`queued` → `generating` spinner → `done` check icon); streamed text rendered via the same `react-markdown` + `remarkMath`/`rehypeKatex` config as Q1; header shows title/authors/pages; chips for provider + `Observability: on/off`; warnings note; Stop button calling `DELETE /api/q2/runs/{run_id}/cancel`; 422/413 errors rendered as a friendly notice.
- [ ] **Step 3:** Wire routes/nav (`App.tsx` adds `/q2`; `Home.tsx` flips Q2 to enabled "Ready to test") and add CSS: `.dropzone`, `.q2-grid`, `.panel`, `.chip-queued/.chip-generating/.chip-done`.
- [ ] **Step 4: Verify** `npx tsc --noEmit` clean, `npm run build` succeeds.
- [ ] **Step 5: Commit** `feat(q2): paper upload + four-panel streaming page`

---

### Task 9: Docs + working system check

**Files:** modify `README.md`, `docs/specs/q2-spec.md` (status → APPROVED).

- [ ] **Step 1:** README: Q2 row → "Ready to test"; new "Q2 — Paper Inference Engine" section (what it does, API additions, event shapes, Langfuse Cloud setup incl. where the keys go, provider/tracing chips, how to run the check); repo-layout block updated (`q2_paper_inference/`, `shared/langfuse_client.py`, `shared/llm_text.py`, `data/papers/`, the `.env.example` Langfuse block).
- [ ] **Step 2: Working system check (run for real, report raw output):**
  1. `curl -F file=@"…/1706.03762v7.pdf" localhost:8000/api/q2/papers` → non-empty title/authors; analyze → correct order, 4 × `section_done`.
  2. Langfuse: keys absent → `GET /api/q2/health` reports `tracing: false` while the run still completes end-to-end (traced path exercised the moment keys are added — stated honestly in the report).
  3. Report per-section `chars` + `usage` from the SSE stream (the baseline numbers Q4/Q5 build on).
- [ ] **Step 3: Commit** `docs(q2): README + spec status + working-system-check evidence`

---

## Self-review
- **Spec coverage:** §1 extraction → Task 3; §2 four calls → Tasks 4+6; §3 chunking/ceiling → Tasks 3+6; §4 trace structure → Tasks 1+6; output contract → Task 4; API contract → Task 7; UI notes → Task 8; checks 1–3 → Task 9. No gaps.
- **Placeholders:** none — every step names files, signatures, and expected output.
- **Type consistency:** `section_start`/`section_done` shapes agree across Task 3 (definition), Task 6 (emission), Task 8 (consumption); `Extraction` fields agree Task 3 → 7; `analysis_trace`/`observation` signatures agree Task 1 → 6; `stream_text` contract agrees Task 2 → 6; storage keys agree Task 5 → 6/7.