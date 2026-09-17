# Q2 Spec — Paper Inference Engine (with Langfuse built in)

Status: BUILT (implementation complete 2026-09-17 — see "Working system check"
at the bottom for live evidence; deviations from this spec are noted inline)

## Original ask (from assignment PDF)
Build an inference engine where the user uploads a paper, and the system drafts:
(1) Technical summary, (2) Intuition of the paper, (3) Prerequisite learning
required, (4) Summary of the paper. Must have Langfuse observability built in.

## Decisions locked before writing this spec
- **Langfuse Cloud** (user choice), wired now with `.env` placeholders; app must run
  and be testable with **no keys**, tracing no-ops with a visible "tracing disabled"
  indicator. Adding `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` /
  `LANGFUSE_HOST` (default `https://cloud.langfuse.com`) turns real tracing on with
  no code change.
- **Provider-agnostic generation** — uses the existing pluggable `llm_client`
  (`mock|lmstudio|openai|anthropic|openrouter|groq|deepinfra|nvidia`). Token usage is
  always recorded on generations, so cost auto-computes for models Langfuse has
  pricing for. (Flagged for later: Q4/Q5 cost evidence needs one run on a *priced*
  model; local LM Studio models report tokens but no cost.)
- **Extraction library: PyMuPDF** (`fitz`, already installed, v1.27.2). Chosen over
  pdfplumber/pypdf for speed + reliable text extraction across layouts.

## Evidence gathered (real run on the test paper, 2026-09-16)
`1706.03762v7.pdf` (Attention Is All You Need): 15 pages, **39,498 chars ≈ 9.9k
tokens**. PDF metadata **title and author are empty strings** → the file's own
metadata cannot be trusted; title/authors must be parsed from page-1 text. 24
hyphen-breaks found (`attribu-\ntion`), and figure/table text bleeds into the body.

### 1. Extraction pipeline
Steps, in order:
1. PyMuPDF `page.get_text("text")` per page → concatenated, page boundaries retained
   as markers so warnings can cite pages.
2. **De-hyphenation**: `re.sub(r"(\w)-\n(\w)", r"\1\2", text)` — fixes the 24
   observed breaks without joining real hyphenated compounds at line ends mid-word.
3. **Header/footer removal**: collect the first and last non-empty line of each page;
   any line appearing on ≥50% of pages (and <80 chars) is treated as a running
   header/footer and dropped (page numbers, journal footers, arXiv stamps).
4. **Figure/table garbage mitigation**: lines starting with `Figure <n>`, `Table <n>`,
   `Fig. <n>` are kept (real captions) but lines that are >60% non-alphanumeric or
   <4 words AND inside a detected table region are dropped. No OCR/vision pass —
   image-only content is out of scope.
5. **Metadata extraction (ingestion-time, not retrieval-time)**, because PDF metadata
   is empty and Q3 must not have to guess later:
   - Title: from `page.get_text("dict")` spans — the longest line among the top-3
     largest font sizes on page 1, excluding affiliation/email lines.
   - Authors: lines immediately after the title until `Abstract` / an email pattern /
     an affiliation keyword (`University`, `Google`, `Research`, `Lab`); names are
     split on commas/`and`/`*` footnote markers, deduped, capped at 20.
   - Fallback: first non-empty page-1 line as title; empty list for authors; both
     flagged in `extraction_warnings`.
6. **Quality gate**: if extracted text < 2,000 chars → upload returns
   `422 {"error": "no_extractable_text", "hint": "scanned/image-only PDF?"}` instead of
   producing garbage sections.

### 2. Prompt strategy per section — FOUR calls, one per section
**Decision: four separate LLM calls** (not one call returning JSON with four keys).
Reasoning:
- **Per-section progress** in the UI is genuine, not faked — each section streams
  independently (spec's own UI note asks for this).
- **Independent retry/evalability**: one weak section can be regenerated without
  redoing the other three; each is a separately gradable Langfuse generation.
- **Q4 dependency**: the runaway-token-spend writeup needs *real per-call* cost data;
  four generations give four real per-section token/cost figures instead of one blob.
- **Register control**: each section has a different register (see Output contract);
  one prompt asking for all four dilutes instructions and the model averages them.
Tradeoffs accepted: ~4× input tokens (the paper text is re-sent per call) and ~4×
latency (calls run **sequentially** to keep the trace tree ordered and the UI
progressive). Mitigations: cap per-call input via §3's budget; if a provider supports
prompt caching, the shared paper prefix benefits automatically.
Alternatives rejected: single JSON call (loses progress + per-section retry, risks
truncated JSON on long outputs); two batched calls (arbitrary split, same downsides).

### 3. Chunking/truncation approach for long papers
Measured: a typical paper is ~40k chars / ~10k tokens, so most fit one context.
- `MAX_INPUT_CHARS = 40_000` (≈10k tokens) per section call.
- **If text ≤ budget → single pass** (`strategy: "single_pass"`): full extracted text
  in each of the four calls.
- **If text > budget → chunk-and-map-reduce** (`strategy: "map_reduce"`): split into
  8,000-char chunks with 1,000-char overlap (so sentences straddling a boundary are
  seen whole at least once) → one LLM call per chunk producing a ≤250-word factual
  digest (traced as `chunk_digest:<n>`) → the four section calls then run against the
  concatenated digests (itself capped at the budget, keeping the most even spread of
  chunks if digests overflow).
- **Hard ceiling**: extracted text > 400,000 chars (e.g. the 15MB PostgreSQL manual)
  → refuse with `413 {"error": "paper_too_large", "limit_chars": 400000}` and a clear
  message; the engine accepts papers, not books.
- Chosen over blind head-truncation because truncation silently loses Results /
  References — exactly the parts Q3 must later answer questions about.

### 4. Langfuse trace/span structure
One **trace per analyze request**, named `q2.paper_analysis`:
- Trace metadata: `paper_id`, `title`, `owner_id` (`"demo-user"` in Q2; Q6 uses real
  users), `provider`, `model`, `pages`, `chars`, `strategy` (`single_pass|map_reduce`),
  `sections` (list requested).
- Trace tags: `["q2", "paper-analysis", provider]`; `session_id = paper_id` so all
  future per-paper activity (Q3 Q&A turns) groups under the same session.
- Children, in order:
  1. **span** `extract_pdf` — no tokens; metadata `{pages, chars, warnings, strategy}`.
  2. **generations** `section:technical`, `section:intuition`, `section:prerequisites`,
     `section:summary` — one per section, each with `input` (prompt + paper excerpt),
     `output` (generated text), `model`, and explicit `usage_details`
     (`input`/`output`/`total` tokens) so Langfuse computes cost per step.
  3. In map-reduce mode, **generations** `chunk_digest:0..n` appear *before* the four
     section generations, so the trace tree shows the full pipeline, not an aggregate.
- Implementation: `backend/app/shared/langfuse_client.py` exports `analysis_trace()`
  / `observation()` context managers (spec drafted them as `trace_analysis()` /
  `generation()`; built names match the plan). If keys are absent they return **no-op**
  context managers (app fully works; `GET /api/q2/health` reports `tracing: false` and
  the UI shows a muted "Observability: off (add Langfuse keys)" chip). Flush on completion.
  Deviation note: LM Studio's host key is accepted from `LANGFUSE_HOST` **or**
  `LANGFUSE_BASE_URL` (regional Cloud orgs configure the latter); and LM Studio
  **does** honour `stream_options.include_usage` on current versions (verified live
  2026-09-17), so per-generation token counts come from the server, not estimates —
  the plan's "LM Studio must not receive stream_options" note is outdated.
## Output contract
Each section is markdown, no preamble ("Here is…"), no meta-commentary:
- **technical** — 250–400 words, precise, assumes field knowledge; names the method,
  architecture, datasets, metrics, and headline results with numbers where present.
- **intuition** — 200–350 words, plain language, no unexplained jargon; an analogy is
  welcome; "explain to a smart friend outside the field".
- **prerequisites** — 5–10 **named** concepts, each as `- **Name** — one line on why
  it's needed here` (e.g. "attention mechanisms", "contrastive loss", "the paper this
  one builds on"). No vague filler.
- **summary** — 150–250 words, standard abstract register, paper-grounded.

## API contract
- `POST /api/q2/papers` — multipart `file` (PDF). Validates type/size (≤25MB), extracts,
  persists, returns `{paper_id, title, authors, pages, chars, strategy, warnings}`.
- `POST /api/q2/papers/{paper_id}/analyze` — SSE, reusing Q1's framing: `run` →
  per section `section_start{key,label}` → `token{section,text}` →
  `section_done{key,chars,usage}` → `done{stop_reason, totals}`; `error` on failure.
  Cancellable via `DELETE /api/q2/runs/{run_id}/cancel` (same cancel registry as Q1).
- `GET /api/q2/papers/{paper_id}` — stored record (metadata + sections) for reloading.
- `GET /api/q2/papers` — list stored papers (id, title, created_at) for the UI.
- `GET /api/q2/health` — `{provider, tracing, langfuse_host, model}`.
- **Persistence:** `backend/data/papers/{paper_id}.json` with `{paper_id, owner_id,
  title, authors, pages, chars, created_at, extraction:{strategy, warnings}, text,
  sections:{technical, intuition, prerequisites, summary}, usage}`. Filesystem JSON
  (not a DB) — simplest thing that lets Q3 reuse the same artifact; Q6 layers
  `owner_id` filtering on top.

## UI notes (for design/build)
New page `/q2`; Home card flips to "Ready to test" when built.
- Upload: drag-and-drop zone + file picker, file name/size shown; reject non-PDF inline.
- After upload: paper header (title, authors, pages), chips for provider + tracing on/off.
- **Four panels** (2×2 desktop, stacked mobile), each labeled with its section name and
  a status chip: `queued` → `generating` (spinner) → `done` (check icon). Streaming text
  lands in the generating panel via markdown + KaTeX (reusing Q1's renderer). This is
  the spec's "per-section progress, not one opaque spinner".
- Extraction warnings (if any) shown as a muted note under the header.
- Reuses: design tokens, `Icon.tsx`, SSE parser (`sse.ts`), cancel pattern,
  markdown/KaTeX rendering. No new palette, no emoji.

## Working system check (must pass before marking this question done)

EVIDENCE (2026-09-17, provider `lmstudio` / `lfm2.5-2.6b`, Langfuse Cloud jp):
1. ✅ Upload of `1706.03762v7.pdf` → 15 pages, 39,379 chars, title
   "Attention Is All You Need" + all 8 authors parsed from page-1 fonts (PDF
   metadata empty). Deviation found & fixed during the check: the loaded local
   model had an 8192-token context, so the adaptive budget
   (`llm_text.input_char_budget()`, probed via LM Studio's REST API) switched
   the run to map-reduce (6 × 8k chunks) instead of the spec's fixed single-pass
   assumption — sections otherwise failed with `exceed_context_size_error`.
2. ✅ With Langfuse keys configured → ONE trace `q2.paper_analysis` (verified via
   `/api/public/v2/observations?fields=core,basic,model,usage,metrics`; note the
   v2 API omits model/usage fields unless those field groups are requested):
   root `q2.paper_analysis` span → `extract_pdf` span → `chunk_digest:0..5`
   generations (in 1882–2559 in / 500 out each) → `section:*` generations:
   technical 2731/1132, intuition 2724/1099, prerequisites 2738/806,
   summary 2692/659 tokens — model recorded on every generation.
   Root cause fixed en route: `propagate_attributes` must wrap the creation of
   the root span (a bare propagate context leaves every observation an orphan
   trace); client is now a process-wide singleton.
3. ✅ With no keys → the run completes end-to-end untraced (unit tests pin
   this path: `test_q2_langfuse.py::test_disabled_without_keys`, and the API
   test asserts `"tracing": false` while four sections still complete).
Per-section `chars`/`usage` figures stream on every `section_done` event —
those are the baseline numbers Q4/Q5 build on.

## Decisions log
- Langfuse **Cloud** with placeholder keys (user choice) + no-op fallback so Q2 is
  testable today; cost data arrives when a priced model is used.
- **Four calls, sequential** — progress, retry, per-section cost granularity, register
  control; accepted 4× input cost/latency (see §2).
- **PyMuPDF** — installed, fast, layout-tolerant; no OCR (image-only PDFs get a clear
  error rather than silently producing garbage).
- **Metadata parsed from page-1 text** — the test paper's PDF metadata is empty
  (evidence above), the same lesson the assignment highlights for Q3.
- **map-reduce above 40k chars, refuse above 400k** — avoids silently dropping
  Results/References, and keeps the 15MB manual from being treated as a "paper".
- **Filesystem JSON persistence with `owner_id` from day one** — Q3 reuses the artifact;
  adding `owner_id` now avoids the Q6 breaking-change the master prompt warns about.
- **SSE progress reusing Q1's protocol** — one event vocabulary across the repo, and the
  cancel button behaves identically on both pages.
