# Q3 Plan — RAG Extension: Question Answering Over the Paper

**Goal:** ask questions about an already-ingested paper and get grounded, cited
answers — three deliberate retrieval paths (metadata / references / semantic),
two-layer refusal, one Langfuse trace per turn.

**Spec:** `docs/specs/q3-spec.md` (APPROVED FOR BUILD 2026-09-17)
**Provider (user decision):** NVIDIA — chat `openai/gpt-oss-20b`, embeddings
`nvidia/llama-nemotron-embed-vl-1b-v2` (both verified live against the user's key).

**Architecture:** new backend module `app/q3_rag_qa/`:
`segmentation.py` (section segmenter + references extraction) → `indexer.py`
(chunking + embedding + persist into the paper record) → `retrieval.py`
(query router: metadata | references | semantic; cosine top-k) → `router.py`
(SSE ask endpoint + index endpoints). Shared additions: `llm_text.py` gains
non-streaming `complete_text()` + `embed_texts()` (NVIDIA OpenAI-compatible),
leaving Q1/Q2 untouched. Frontend: `/q3` chat-over-document page with source panel.

## Global constraints
- Do NOT modify Q1/Q2 behaviour: `protocol.py` additions purely additive; existing
  endpoints untouched. Index lives inside the paper record under `index`.
- `MAX_REFS_CHARS = 20_000`; `CHUNK_TARGET = 1_600`, `CHUNK_OVERLAP = 800`,
  `TOP_K = 4`, `REFUSAL_FLOOR = 0.18` cosine.
- Refusal markers: retrieval-gate short-circuit OR `NOT_IN_PAPER` from the model →
  friendly refusal, `refused: true` on `done`.
- Every turn = one trace `q3.paper_qa` (spans `retrieve`, gen `embed_query` when
  semantic, gen `answer`), session_id = paper_id, flush at end.
- No new palette/emoji; reuse `Icon.tsx`, design tokens, Q1 composer/markdown.

## File map
- Create: `backend/app/q3_rag_qa/{__init__,segmentation,indexer,retrieval,router}.py`
- Modify: `backend/app/shared/llm_text.py` (+`complete_text`, +`embed_texts`,
  +`embedding_model`), `backend/app/shared/protocol.py` (+`citation_event`),
  `backend/app/main.py` (mount), `backend/.env.example` (NVIDIA embedding knobs)
- Tests: `test_q3_segmentation.py`, `test_q3_indexer.py`, `test_q3_retrieval.py`,
  `test_q3_api.py`
- Frontend: create `pages/Q3RagQa.tsx`; modify `App.tsx`, `Home.tsx`, `sse.ts`,
  `styles/app.css`

### Task 1: Section segmentation + references extraction
`segment_text(text) -> list[Section{text_label, text, start}]`; heading patterns:
`^\d+(\.\d+)*\s+\S` (numbered), `^(abstract|references|appendix|acknowledg\w*)\b`
(case-insensitive), ALL-CAPS short lines, Title Case short lines followed by body;
drop false positives (lines ending in `.`/`:` with >8 words). `extract_references(
sections) -> str | None` (text after a `References` heading, capped 20k).
Test: attention-paper-shaped fixture → expected section labels; references captured.

### Task 2: Chunker
`chunk_sections(sections, target=1600, overlap=800) -> list[Chunk{id, section,
text}]` — paragraph-boundary splits first, sentence fallback, overlap via tail
carry; References section excluded (stored separately). Test: bounds, overlap,
section labels preserved, refs excluded.

### Task 3: Embeddings + completion in `llm_text.py`
`embedding_model() -> str` (env `NVIDIA_EMBEDDING_MODEL` default
`nvidia/llama-nemotron-embed-vl-1b-v2`); async `embed_texts(texts, input_type)`
(query|passage; list of vectors; usage dict); `complete_text(messages, *,
max_tokens)` (non-streaming, returns text+usage; same provider selection as
`stream_text`; mock returns canned text). Tests: mock paths, provider_model
defaults.

### Task 4: Indexer
`build_index(record) -> index dict`: segment → chunk → embed (batched) →
`{chunks:[{id, section, text}], vectors:[[f]], embedding_model, references,
built_at}` → persist into record, `save_paper`. Skips embedding if chunks
unchanged? (No — idempotent rebuild is fine at this scale.) Test with mocked
`embed_texts`.

### Task 5: Retrieval router
`classify(question) -> "metadata"|"references"|"semantic"` (author/title/who
patterns → metadata; reference/bibliography/cite patterns → references; else
semantic). `retrieve(record, question) -> RetrievalResult{path, citations,
context_blocks, top_score}`: metadata → authors/title blocks; references → refs
block (fallback: semantic over `*eference*` sections); semantic → embed query,
cosine top-k, gate at 0.18 (below → `refused`, `reason=retrieval_gate`).
Tests: classification table, gating, metadata path needs no embeddings.

### Task 6: QA generation + protocol
`answer_events(record, question, run_id, cancel_ev) -> AsyncIterator[dict]`:
`citation` events first → stream answer via `stream_text` with grounded prompt
(`NOT_IN_PAPER` contract, numbered claims) → translate marker to refusal →
`done{stop_reason, usage, refused, citations}`. Trace `q3.paper_qa` (retrieve span
+ embed_query gen + answer gen), flush, persist turn to record (`qa_history`).
`protocol.citation_event(label, snippet, position)` additive.

### Task 7: Router + wiring
`POST /api/q3/papers/{id}/index`, `GET /api/q3/papers/{id}/index`,
`POST /api/q3/papers/{id}/ask` (SSE, reuse cancel registry), `DELETE
/api/q3/runs/{run_id}/cancel`, `GET /api/q3/health`. Mount in `main.py`;
`.env.example` NVIDIA embedding block. API tests with mocked embed/complete.

### Task 8: Frontend `/q3`
Paper picker (Q2 papers list) → chat-over-document: Q1-style conversation,
`citation` chips → source panel, refusal notice state, provider/index chips,
Stop via cancel registry. `sse.ts` + `askQuestion()`, `Q3Event` types. `tsc`
+ `vite build` clean.

### Task 9: Working system check + docs
Real run on `p_9c3022cf`: three example questions + breakfast question; verify
cited answers + refusal, report usage; update README (Q3 row/section, layout,
endpoints); spec evidence block. Commit-ready (no commit until asked).

## Self-review
- Spec §1 chunking → Tasks 1–2; §2 three paths → Tasks 5; §3 refusal → Tasks 5–6;
  citations → Task 6; Langfuse → Task 6; API → Task 7; UI → Task 8; check → Task 9.
- `answer_events` shapes match Task 7's SSE framing; `RetrievalResult` matches
  Task 6 consumption; index dict matches Task 5 read path.
