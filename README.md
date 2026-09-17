# AI Fundamentals → AI Engineer — Assignment System

One repo, one running system, six questions. Each question gets its own backend
module and its own frontend page, reachable from a shared home screen. Progress is
built and approved one question at a time — see `00-MASTER-PROMPT.md` for the process.

## Status

| # | Question                                   | Status      |
|---|---------------------------------------------|-------------|
| 1 | Streaming Chat UI                          | Done (approved) |
| 2 | Paper Inference Engine (+ Langfuse)        | Ready to test |
| 3 | RAG Extension — Q&A over the paper         | Not built   |
| 4 | "Runaway Token Spend" writeup              | Not built   |
| 5 | Langfuse Alert on Token/Cost                | Not built   |
| 6 | Multi-paper RAG with access-scoped citations| Not built   |

## Q1 — Streaming Chat UI (built, awaiting human sign-off)

**What it is:** a chat page (`/q1`) streaming tokens over SSE as they're generated,
with a visible indicator whenever a tool call creates a gap in the token stream,
and edge cases handled (drop mid-stream, navigate away, tool failure, cancel).

**SSE event protocol** (exact shapes in `docs/specs/q1-spec.md` §1):
```json
{"type": "run", "data": {"run_id": "r_…"}}                                   // first event, enables cancel
{"type": "token", "data": {"text": "Hel"}}                                   // incremental text chunk
{"type": "tool_call_start", "data": {"id": "call_…", "tool": "get_weather", "label": "Checking weather for Paris…", "args": {"city": "Paris"}}}
{"type": "tool_call_end", "data": {"id": "call_…", "tool": "get_weather", "status": "ok|error", "summary": "Paris: 18°C, cloudy", "duration_ms": 412}}
{"type": "error", "data": {"message": "…", "recoverable": true|false}}
{"type": "done", "data": {"stop_reason": "completed|cancelled|error", "usage": null|{...}}}
```
Plus `: ping` SSE comments every 15s so proxies don't kill idle tool gaps.

**What the UI shows during a tool call:** token rendering pauses; an inline
bordered row appears inside the assistant bubble — spinner + "Checking weather
for Paris…" — and flips to a check/alert icon + result summary when the tool
finishes. Tokens resume below it. Never a frozen screen. While waiting for the
first token, a pulsing "Thinking…/Pondering…/Reasoning through it…" status
sits directly above the composer (sticky bottom, always next to the input),
and disappears the instant tokens arrive.

**Edge cases:** navigate-away aborts the fetch and the backend polls
`is_disconnected()` to tear down generation (no orphans); connection drop keeps
partial text + a Retry button (manual retry only, no auto-loop); tool failure
renders an error row and the stream completes instead of hanging; the circular
**Stop** button cancels server-side (`DELETE /api/q1/runs/{run_id}/cancel` →
`done{stop_reason:"cancelled"}`), not just the UI connection. Auto-scroll
follows the stream only while you're pinned near the bottom — scroll up
mid-generation and it lets go, showing a "Jump to latest" pill above the
composer until you return.

**Tools (deterministic, no external APIs):** `get_weather(city)`,
`calculator(expression)`, `get_current_time()`. In `lmstudio`/`mock` mode a
server-side keyword router decides the first turn; real OpenAI/Anthropic
providers use native function-calling.

**Provider quirk documented** (`backend/app/shared/llm_client.py`): the local
`liquid/lfm2.5-1.2b` refuses coding prompts and lazily skips tools whenever a
`tools` array is attached — so the LM Studio path only sends tools on
tool-result follow-up turns. Q2+ uses real providers; revisit then.

**Verified (working system check):** tool question streams tokens + shows the
gap indicator + completes; disconnect at 2s leaves no orphaned generation;
cancel returns `{"ok":true}` and ends the run; 9/9 backend tests pass;
`tsc` + `vite build` clean. UI is emoji-free, Claude-style: 768px chat column,
plain assistant text with markdown + KaTeX math rendering, rounded composer
pinned just under the last message (sticky, 12px above the viewport bottom),
icon-only buttons.

## Q2 — Paper Inference Engine (built)

**What it is:** upload a paper PDF at `/q2` → the system extracts text + metadata,
then drafts four sections in **four separate sequential LLM calls** (per-section
streaming progress, per-section retry/eval, per-section token cost): Technical
summary, Intuition, Prerequisite learning, Summary. Every call is traced to
Langfuse Cloud.

**Pipeline:** PyMuPDF extraction (de-hyphenation, running header/footer removal,
table-fragment dropping, title/authors parsed from page-1 fonts — PDF metadata is
empty on real papers) → quality gate (<2,000 chars → 422, >400,000 → 413) →
`single_pass` (≤40k chars) or `chunk-and-map-reduce` (8k chunks, 1k overlap,
`chunk_digest:<n>` generations) → four section generations streamed over SSE.

**Adaptive input budget:** the 40k-char budget assumes a roomy context window.
For LM Studio the backend probes the loaded model's real `context_length` via
its REST API and shrinks the per-call budget accordingly (e.g. an 8192-token
context → ~27k chars), switching to map-reduce instead of silently truncating.

**SSE events** (Q1 vocabulary + Q2 additions):
```json
{"type": "section_start", "data": {"key": "technical", "label": "Technical summary", "index": 0, "total": 4}}
{"type": "token", "data": {"section": "technical", "text": "…"}}
{"type": "section_done", "data": {"key": "technical", "chars": 1834, "usage": {"input": 9947, "output": 512, "total": 10459}, "duration_ms": 41234}}
```
Sections persist as they finish, so a cancelled run keeps completed sections.

**Langfuse trace structure** (one trace per analyze run, `q2.paper_analysis`):
`extract_pdf` span → `chunk_digest:0..n` generations (map-reduce only) →
`section:technical|intuition|prerequisites|summary` generations. Trace metadata
carries `paper_id`, `title`, `owner_id`, `provider`, `model`, `strategy`; tags
`["q2", "paper-analysis", provider]`; `session_id = paper_id`. Each generation
records `usage_details` (input/output/total tokens) so cost shows per step.
No keys → tracing no-ops and the UI shows "Observability: off".

**Endpoints:** `POST /api/q2/papers` (upload), `POST /api/q2/papers/{id}/analyze`
(SSE), `GET /api/q2/papers[?owner_id=]`, `GET /api/q2/papers/{id}`,
`DELETE /api/q2/runs/{run_id}/cancel`, `GET /api/q2/health`.

**Verified (working system check):** uploaded *Attention Is All You Need* (15
pages, 39,379 chars) — title + all 8 authors parsed correctly; four sections
stream in order on the local `lfm2.5-2.6b` model with real per-call token usage
(LM Studio honors `stream_options.include_usage`); Langfuse Cloud (jp region)
shows ONE trace per run — root `q2.paper_analysis` span → `extract_pdf` →
`chunk_digest:0..5` → `section:*` — with model + usage on every generation
(e.g. technical 2731 in / 1132 out, summary 2692 in / 659 out). 28 backend
tests pass (9 Q1 + 19 Q2); `tsc` + `vite build` clean.

## Q3 — RAG Q&A over the paper (built)

**What it is:** pick one of your uploaded papers at `/q3` and ask questions
("how was this tested?", "what are the references?", "who are the authors?").
Answers stream over SSE grounded in the paper, carry inline `[1]`-style
numbered citations rendered as chips, and unanswerable questions are refused
honestly instead of hallucinated.

**Indexing:** the extracted paper text is **section-segmented** (numbered +
word headings, split-number/title lines re-joined, table-cell noise rejected by
monotonic-sequence checking) → **section-aware chunks** (1,600-char target,
800-char overlap, heading-only stubs skipped — a bare "6\nResults" header
matches "how was this tested?" lexically while carrying zero evidence) →
embedded locally with **sentence-transformers all-MiniLM-L6-v2** (384-dim,
no network, no key; no vector DB — vectors live in the paper record). The
References section is captured verbatim as structured data, NOT chunked.

**Three deliberate retrieval paths** (deterministic keyword router):
- *metadata* ("who are the authors?") → answered from Q2's ingested title/
  authors; no retrieval, no embeddings. Citation: `Page 1 (title block)`.
- *references* ("what are the references?") → the verbatim References block.
  Citation: `References (section)`.
- *semantic* ("how was this tested?") → **hybrid retrieval**: dense cosine
  fused with BM25-style keyword scoring via reciprocal rank fusion (dense-only
  missed the evaluation section live — "tested" shares no wording with
  "BLEU/newstest2014"), top-4 chunks. Citations: section labels.

**Two-layer refusal:** cosine floor (0.10, recalibrated on live MiniLM scores —
MiniLM is symmetric so vague queries score low; the floor is only a degenerate-
retrieval pre-filter) skips generation entirely; the answer prompt otherwise
requires grounding in the provided blocks and orders the model to reply
`NOT_IN_PAPER` otherwise — that marker is prefix-buffered so it never flickers
into the chat, and `done.refused=true` + the friendly message replace it.
Refusals are traced too (`refused=true`) so Q5's alerting sees them.

**Langfuse:** one trace per Q&A turn, `q3.paper_qa` (session_id = paper_id):
`retrieve` span (path, top score, block count) → `answer` GENERATION with the
retrieved context as input and real per-call usage.

**Endpoints:** `POST /api/q3/papers/{id}/index` (idempotent; also auto-run
before the first question), `GET /api/q3/papers/{id}/index` (UI chip),
`POST /api/q3/papers/{id}/ask` → SSE `run` → `citation*` → `token*` →
`done{stop_reason, usage, refused, citations}`, `DELETE
/api/q3/runs/{run_id}/cancel`, `GET /api/q3/health` (chat + embedding models).

**Verified (working system check, live `nvidia` / `openai/gpt-oss-20b` + local
MiniLM, on `p_9c3022cf` — 35 chunks, 23 sections, 9,283-char references block):**
1. "How was this tested?" → grounded answer (WMT 2014, newstest-2014 test sets,
   BLEU, 8×P100 GPU schedule) citing 5.4/5/5.2 — 994 in / 643 out tokens.
2. "What are the references?" → all 40 entries listed, `References (section)` —
   3,078 in / 2,360 out.
3. "Who are the authors?" → all 8 authors from metadata, `Page 1 (title
   block)`, no retrieval — 253 in / 81 out.
4. "What did the authors have for breakfast?" → honest refusal, generation
   gate (`NOT_IN_PAPER`), traced with `refused=true` — 1,207 in / 62 out.
Langfuse Cloud (jp) shows one `q3.paper_qa` trace per turn: root span →
nested `retrieve` span + `answer` GENERATION with model + usage. 68 backend
tests pass (9 Q1 + 19 Q2 + 40 Q3); `tsc` + `vite build` clean.

## Repository layout

```
/backend
  /app
    /q1_streaming            # SSE chat endpoint, event protocol, tool-call events  ← BUILT
      router.py              # POST /api/q1/chat (SSE), DELETE cancel, GET health
      tools.py               # 3 demo tools + keyword router + safe arithmetic
    /q2_paper_inference       # PDF ingest, 4-section structured generation  ← BUILT
      extraction.py          # PyMuPDF + cleanup + title/author heuristics + gates
      prompts.py             # four section instructions + chunking + digest prompt
      analyzer.py            # 4 sequential generations, map-reduce, tracing
      storage.py             # JSON records under data/papers/ (owner_id from day one)
      router.py              # upload / analyze (SSE) / list / get / cancel / health
    /q3_rag_qa                # single-paper RAG, section-aware chunking, citations  ← BUILT
      segmentation.py         # section segmenter + verbatim References capture
      chunking.py             # section-aware chunks (1.6k target / 800 overlap)
      indexer.py              # embed chunks (local MiniLM) → index in paper record
      retrieval.py            # router: metadata | references | semantic (+hybrid RRF)
      analyzer.py             # grounded answer streaming, NOT_IN_PAPER gate, tracing
      router.py               # index / ask (SSE) / status / cancel / health
    /q6_multi_paper_rag        # multi-paper RAG, owner_id-scoped retrieval  (not built)
    /shared
      langfuse_client.py       # no-op-safe Langfuse Cloud tracing (used by Q2)
      llm_text.py              # plain-text streaming + usage capture + adaptive budget + local embeddings
      llm_client.py            # pluggable streaming provider: mock|openai|anthropic|lmstudio
      protocol.py              # SSE event constructors (shapes above)
      cancel_registry.py       # run_id → asyncio.Event, TTL 5 min
    main.py                    # mounts routers + serves frontend/dist (single process)
  /tests                       # pytest: protocol, tools, Q1 stream, Q2 unit+API, Q3 unit+API (68 tests)
  /data/papers/                # uploaded-paper JSON records (gitignored)
  requirements.txt
  .env / .env.example

/frontend
  /src
    /pages
      Home.tsx                 # question cards, status badges, routes to /q1../q6
      Q1Streaming.tsx          # Claude-style chat: streaming, tool rows, stop/retry  ← BUILT
      Q2PaperInference.tsx     # dropzone + 2×2 panel grid + per-section chips  ← BUILT
      Q3RagQa.tsx              # paper picker + chat + citation chips + refusal state ← BUILT
      (Q4–Q6 pages: added when built)
    /components
      Icon.tsx                 # inline SVG icon set (no emojis, no icon library)
    /styles
      tokens.css               # from docs/design/design-tokens.md (locked)
      app.css                  # Claude-style chat + card styles
    App.tsx / main.tsx         # router shell; backend also serves built assets
  /dist                        # production build (served by backend on :8000)
  package.json / vite.config.ts

/start.sh                       # ONE command: builds frontend if needed + runs everything

/docs
  /specs
    q1-spec.md                 # APPROVED — event protocol, tool-gap UI, disconnect/cancel
    q2-spec.md … q6-spec.md    # skeletons, filled per-question before building
  /plans
    q1-plan.md                 # Q1 implementation plan
  /design
    design-tokens.md             # locked visual system — read before any frontend work
  q4-writeup-template.md
  q5-alert-proof-template.md

00-MASTER-PROMPT.md              # the process contract — read this first, always
```

## Running the system

One command, one process — UI + API together on **http://localhost:8000**:

```bash
./start.sh
```

Then open http://localhost:8000 (Home at `/`, Q1 chat at `/q1`, Q2 at `/q2`).

- `start.sh` creates `backend/.env` from `.env.example` on first run, rebuilds
  the frontend only when `frontend/src` changed, and serves everything via uvicorn.
- If `backend/.env` says `LLM_PROVIDER=lmstudio`, keep LM Studio running on
  `:1234` with model `liquid/lfm2.5-1.2b` loaded. The Q1 badge shows the active
  provider; if LM Studio is down you get an `error` event, not a hang.

Manual mode (two terminals — only needed for hot-reload frontend dev):

```bash
# terminal 1 — backend API only
cd backend
pip install -r requirements.txt
cp .env.example .env   # edit as below
uvicorn app.main:app --reload --port 8000

# terminal 2 — Vite dev server with HMR (proxies /api → :8000)
cd frontend
npm install
npm run dev   # http://localhost:5173
```

### Providers (`backend/.env`)

| `LLM_PROVIDER` | Needs | Notes |
|---|---|---|
| `mock` (default) | nothing | canned streaming, keyword-routed tools; demo badge |
| `lmstudio` | LM Studio on :1234 | `LMSTUDIO_MODEL=liquid/lfm2.5-1.2b`; no API key |
| `openai` | `OPENAI_API_KEY` | `OPENAI_MODEL=gpt-4o-mini`, native function-calling |
| `anthropic` | `ANTHROPIC_API_KEY` | `ANTHROPIC_MODEL=claude-3-5-haiku-latest`, native tool-use |
| `openrouter` | `OPENROUTER_API_KEY` | 100+ models; `OPENROUTER_MODEL=meta-llama/llama-3.3-70b-instruct` |
| `groq` | `GROQ_API_KEY` | fastest inference; `GROQ_MODEL=llama-3.3-70b-versatile` |
| `deepinfra` | `DEEPINFRA_API_KEY` | `DEEPINFRA_MODEL=meta-llama/Llama-3.3-70B-Instruct` |
| `nvidia` | `NVIDIA_API_KEY` | NVIDIA NIM; `NVIDIA_MODEL=meta/llama-3.3-70b-instruct` |

All hosted providers speak the OpenAI wire format, so streaming + tool-calling
work identically across them — see `backend/.env.example` for per-provider
keys/models (model name formats differ per provider; examples included there).

### Testing Q1

- In the browser: ask "What's the weather in Paris?" → tokens stream, tool-gap
  row appears, summary flips in, answer completes. Press **Stop** mid-stream →
  "Cancelled." + server tears down the run.
- Raw SSE: `curl -N -X POST localhost:8000/api/q1/chat -H 'Content-Type: application/json' -d '{"messages":[{"role":"user","content":"What is the weather in Paris?"}]}'`
- Backend tests: `cd backend && python -m pytest tests/ -v` (provider-pinned to
  mock, so your `.env` doesn't affect them)

### API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/q1/chat` | `{messages: [{role, content}]}` → SSE stream |
| DELETE | `/api/q1/runs/{run_id}/cancel` | server-side cancel → `{"ok": bool}` |
| GET | `/api/q1/health` | active provider + demo/live badge info |
| POST | `/api/q2/papers` | multipart PDF → `{paper_id, title, authors, pages, chars, strategy, warnings}` |
| POST | `/api/q2/papers/{id}/analyze` | SSE: run → section_start/token/section_done ×4 → done |
| GET | `/api/q2/papers` | stored papers (summary rows, optional `?owner_id=`) |
| GET | `/api/q2/papers/{id}` | full record incl. generated sections |
| DELETE | `/api/q2/runs/{run_id}/cancel` | server-side cancel → `{"ok": bool}` |
| GET | `/api/q2/health` | `{provider, model, tracing, langfuse_host}` |
| GET | `/api/health` | `{"ok": true}` |

### Git workflow

Remote: `https://github.com/afridi-shaik-aritha/cohort2.git` — initialized, all
work kept **unstaged** (zero commits so far). Commit/push happens per-question
when explicitly requested.

### Langfuse (Q2+)

Cloud, regional host supported. In `backend/.env`:

```ini
LANGFUSE_PUBLIC_KEY=pk-lf-…
LANGFUSE_SECRET_KEY=sk-lf-…
LANGFUSE_HOST=https://jp.cloud.langfuse.com   # or LANGFUSE_BASE_URL; default: cloud.langfuse.com
```

Leave the keys blank and everything still runs — tracing no-ops, `/api/q2/health`
reports `"tracing": false`, and the Q2 page shows "Observability: off". Per-
generation token usage appears in the Langfuse UI for every provider (LM Studio
included — verified `stream_options.include_usage` works there). Cost computes
automatically for models Langfuse has pricing for; local models report tokens only.

## Process

See `00-MASTER-PROMPT.md`. Short version: one question at a time, spec → plan →
build → self-verify against the PDF's "Working system check" → stop for human
testing → only then move to the next question.
