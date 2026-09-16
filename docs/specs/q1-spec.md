# Q1 Spec — Streaming Chat UI

Status: APPROVED (human signed off 2026-09-16 — cleared to plan + build)

## Original ask (from assignment PDF)
Build a chat UI for the chat API. LLM calls can take a long time to reply, so make
these SSE calls and stream the response from the LLM.

## Elaboration (from PDF, summarized)
Multi-tool agent responses take 5–20+s (each tool call adds a round trip). Blocking
responses feel frozen. Stream tokens as generated + explain tool-call gaps with
explicit events so the UI never goes silent.

## Architecture choice (per user decision)
Pluggable LLM client (`backend/app/shared/llm_client.py`, `LLM_PROVIDER=openai|anthropic|mock` via `.env`).
- `openai`: `gpt-4o-mini`, `stream=True`, function tools.
- `anthropic`: `claude-3-5-haiku-latest`, streaming + tool_use.
- `mock`: deterministic canned streamer (no key needed) — emits the exact same
  event protocol so the UI can be demoed/tested with zero credentials.
Default dev path: `mock` works out of the box; set a real key to see real tokens.

## Demo tools (server-side, deterministic, no external APIs)
1. `get_weather(city: string)` → canned weather for a few cities + generic fallback.
   Label: `"Checking weather for {city}…"`.
2. `calculator(expression: string)` → safe arithmetic eval. Label: `"Calculating {expression}…"`.
3. `get_current_time(timezone: string = "UTC")` → server time. Label: `"Getting current time…"`.
Trigger phrases (documented in UI placeholder + empty state): "weather in …", "calculate …", "what time is it".
Real-LLM mode exposes the same 3 as OpenAI functions / Anthropic tools. Mock mode
triggers them by keyword so the tool-gap UI is exercisable without a key.

### 1. Event protocol — exact shape of every SSE message type
Transport: `POST /api/q1/chat` → `Content-Type: text/event-stream`, `Cache-Control: no-cache`.
Framing: one SSE message per event, payload is JSON in `data:` (no custom `event:` field —
`type` inside the JSON is the single source of truth, keeps `fetch` + `EventSource` parsers identical).

```json
{"type": "run", "data": {"run_id": "r_7f3a"}}
{"type": "token", "data": {"text": "Hel"}}
{"type": "tool_call_start", "data": {"id": "call_7f3a", "tool": "get_weather", "label": "Checking weather for Paris…", "args": {"city": "Paris"}}}
{"type": "tool_call_end", "data": {"id": "call_7f3a", "tool": "get_weather", "status": "ok", "summary": "Paris: 18°C, cloudy", "duration_ms": 412}}
{"type": "error", "data": {"message": "Weather lookup failed", "recoverable": true}}
{"type": "done", "data": {"stop_reason": "completed", "usage": {"input_tokens": 231, "output_tokens": 94}}}
```

Field rules:
- `token.text`: raw incremental chunk (may be 1 char – 1 word depending on provider). Client appends verbatim, no reordering.
- `tool_call_start.id`: server-generated `call_xxxx`, correlates start/end. `args` is the exact JSON passed to the tool (surfaced for transparency, truncated to 300 chars in UI title attr).
- `tool_call_end.status`: `"ok" | "error"`. `summary`: ≤140-char human string rendered in UI. `duration_ms`: server-measured tool wall time.
- `error.message`: user-safe string. `recoverable=true` → UI shows Retry; `false` → stream is dead, `done{stop_reason:error}` follows.
- `done.stop_reason`: `"completed" | "cancelled" | "error"`. `usage` may be `null` in mock mode; real providers fill it. Always sent exactly once as the last event (except hard disconnect where nothing more can be sent).
- Keep-alive: server sends `: ping\n\n` every 15s (SSE comment, ignored by parser) so proxies don't kill idle tool gaps.

### 2. What the UI shows during a tool call
Assistant bubble streams tokens as plain left-aligned text (typing effect).
On `tool_call_start`: token appending PAUSES; an inline status row is inserted
INSIDE the same assistant bubble, below text so far: spinner + `label`
(e.g. "Checking weather for Paris…"), `--color-text-secondary`, small,
`--color-bg-subtle` pill. Visually distinct from tokens so a gap is never
mistaken for a stall.
On `tool_call_end`: row flips to done/error + `summary`, spinner stops,
collapses to a muted one-liner (expandable `<details>` shows args/timing).
Token streaming RESUMES below it in the same bubble. Multiple calls stack in
order. Tool failure does NOT hang: error row renders + stream continues or
ends with `done`, never a frozen spinner.

### 3. Behavior on disconnect / cancel
- Navigate away/close: `AbortController.abort()` on unmount. Backend polls
  `await request.is_disconnected()` each iteration + before/after each tool;
  on disconnect it breaks the LLM iterator and runs no further tools (no orphan).
- Drop mid-stream: partial tokens stay; inline warning "Connection lost — retry?"
  with Retry button re-sending same history (server stateless, retry is safe).
  No auto-retry loop (avoids runaway spend — Q4 tie-in).
- Stop/Cancel (BONUS, in scope): circular Stop button (square icon) replaces the
  Send button (arrow icon) in the composer while streaming. Click → `abort()` +
  `DELETE /api/q1/runs/{run_id}/cancel`
  (run_id from first `run` event). Server per-run `asyncio.Event` checked each
  iteration; if set, emits `done{stop_reason:"cancelled"}` and stops the LLM
  call. UI keeps partial tokens + "Cancelled" label. Server-effective, not UI-hide.

## Implementation choices to state explicitly
- **fetch + ReadableStream, NOT EventSource.** (1) Chat needs POST with JSON body;
  EventSource is GET-only. (2) Q6 needs custom auth headers; EventSource can't send
  them. (3) AbortController gives real cancel. Cost: ~20 lines of manual parsing.
- **Tool round trips server-side**: one HTTP stream stays open across the whole turn:
  LLM stream → tool_call_start → await tool() → tool_call_end → 2nd LLM stream
  (tool result in history) → done. Client only renders; never orchestrates tools.
  Max 3 tool rounds/turn (loop guard; Q4 tie-in).
- **Stateless server** apart from ephemeral run_id → cancel_event map (TTL 5 min).
  Full history re-POSTed each turn; retry/cancel stay simple, no DB.

## API contract (for plan/build)
- `POST /api/q1/chat` `{messages: [{role, content}]}` → SSE stream above.
- `DELETE /api/q1/runs/{run_id}/cancel` → `{"ok": true}`.
- `GET /api/q1/health` → `{"provider": "mock|openai|anthropic", "keys_configured": bool}` (no secrets) — drives UI "demo mode" badge.
- `GET /api/health` → `{"ok": true}`.

## UI notes (for design/build)
Visible token-by-token "typing" effect. Tool-call gaps must show something
meaningful (not a frozen screen) — a small inline status line per the protocol above.
If a stop/cancel button is built, it should be clearly reachable while a response is
streaming, not buried.
Design tokens (`docs/design/design-tokens.md`): cream bg, terracotta accent,
serif H1. Q1 page: header (title + provider badge "Demo mode"/"Live"), message
list (user right-subtle-fill, assistant left-plain), tool rows per §2, composer
with Send/Stop, empty state with 3 trigger suggestions. Home: 6 cards; only Q1
enabled, Q2–Q6 greyed "Coming soon" disabled (no dead links).

## Working system check (must pass before marking this question done)
"What's the weather in Paris?" → tokens visibly stream, "Checking weather…" row
appears in the gap, flips to summary, answer completes. Then Stop mid-stream →
backend log shows generation torn down, no further tokens. Report both.

## Decisions log
- Pluggable provider (user chose "both"): one interface, avoids rework on keys.
- Mock default: demoable with zero secrets; same protocol, no UI fork.
- 3 canned tools, no live APIs: zero creds, deterministic tests, no scope creep.
- Cap 3 tool rounds/turn: cheap loop guard, foreshadows Q4.
- Stateless + run cancel map: simplest server-effective cancel without DB/auth
  (Q6 adds owner scoping; flagged as breaking-change point).
