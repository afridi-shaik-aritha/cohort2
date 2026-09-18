# Q6 Spec — RAG with Citations (Multi-Document, Access-Scoped)

Status: BUILT (2026-09-18 — see "Working system check" evidence at the end;
access-control test automated in `backend/tests/test_q6_access.py`)

## Original ask (from assignment PDF)
Extend Q3 to support multiple papers from multiple users sharing one index. Retrieval
must figure out which document(s) an answer should draw from, and the system must
actively prevent one user's questions from ever surfacing another user's content —
not just fail to mention it, but be structurally incapable of retrieving it.

## Required sections (per the PDF's "What Openspec specs should cover")

### 1. Metadata schema

Every chunk in the shared retrieval pool carries these fields, stamped when the
index is built (Q2 extracts them at ingestion; Q6 propagates them onto each
chunk — the PDF's Q3 lesson: metadata must be captured at ingestion, never
hoped for from retrieval later):

| Field | Source | Purpose |
|---|---|---|
| `paper_id` | Q2 ingest (`p_<8hex>`) | stable chunk→paper join key |
| `owner_id` | upload-time (`X-Owner-Id`, default `demo-user`) | **the access-control key** — the only field the retrieval filter reads |
| `paper_title` | Q2 page-1 extraction | per-answer attribution (title, not ID) |
| `authors` | Q2 page-1 extraction | attribution + metadata questions |
| `section` | Q3 segmenter | citation label within the paper |
| `id` | per-paper chunk ordinal | trace/debug joins |
| `text`, `vector` | Q3 chunker/embedder | retrieval payload |

Storage shape is unchanged from Q3 — chunks live in the paper record
(`index.chunks[]`), one JSON file per paper. The "shared index" is the union of
records; Q6's retrieval layer builds the pool by concatenating chunk lists from
records that pass the owner filter. Because the pool is materialised per query,
the metadata stamp is guaranteed fresh (title edits propagate without
re-embedding) and the filter cannot be bypassed by stale chunk data.

### 2. Cross-paper ambiguity resolution

**Strategy: explicit scope first, title-mention second, all-papers fallback —
in that fixed order, never accidental.**

1. **Explicit selector (UI + API).** The `/q6` page has a paper selector
   (single paper, or "All my papers"). A selector choice travels as
   `scope: {paper_ids: [...]}` on the ask request. When present, retrieval is
   restricted to those papers (still owner-filtered — see §4).
2. **Title mention in the question.** `scope.resolve_scope()` extracts
   title-like phrases (quoted strings, or a best fuzzy match against the
   accessible papers' titles — token-overlap ratio ≥ 0.6). A mention of one
   accessible paper narrows the scope to it, even in "all papers" mode
   ("what does *Attention Is All You Need* say about positional encoding?"
   → that paper only).
3. **All-papers fallback.** No selector, no mention → retrieve across **all of
   the user's papers** (owner-filtered, §4) and let the fused top-k decide. The
   answer then attributes whatever papers actually contributed (§3).

"This" in a follow-up is resolved by the UI's persistent selector (the user
pins the paper), not by an LLM guess — deterministic and inspectable.

### 3. Citation format for multi-paper answers

- Every context block is numbered `[n]` and rendered to the model as
  `[n] (paper_title — section)`, so every claim's citation carries its paper.
- SSE/API citation events gain `paper_id` + `paper_title` alongside
  `position/label/snippet`; `done.citations` carries the same.
- UI renders one chip per citation: **`[n] PaperTitle — Section`** — chips from
  different papers are visually distinct (title prefix), never merged. The done
  panel groups the papers the answer drew from ("Sources: Attention Is All You
  Need (3), BERT: Pre-training… (1)").
- The system prompt additionally instructs: attribute each claim to its block
  number; do not blend claims across papers without their citations.

### 4. Where and how access control is enforced — state this explicitly
**Access control MUST be a retrieval-time metadata filter on the vector search
itself** (e.g. `WHERE owner_id = current_user`), so a restricted document is never
even retrieved, let alone shown to the model. Enforcing this only via a system-prompt
instruction ("only discuss the user's own papers") is NOT sufficient — a motivated or
unlucky query can still surface leaked content because the model saw it in context.
This mirrors the "access control leakage" failure mode in production RAG systems.

**Enforced in `backend/app/q6_multi_paper_rag/scope.py::scoped_records()` — the
candidate pool itself, before any embedding or scoring runs.**

The mechanism, concretely:

1. Every ask resolves `owner_id` from the `X-Owner-Id` header (simulated users;
   no auth by design — the owner is chosen in the UI switcher).
2. `scoped_records(owner_id)` walks the record store and returns **only records
   where `record["owner_id"] == owner_id`**. This is the structural filter — the
   equivalent of `WHERE owner_id = current_user` in a vector-DB metadata filter.
3. The retrieval pool passed to the vector search is built exclusively from
   those records' chunks (`pool = [chunk | record in scoped_records]`). There is
   **no code path** by which another user's chunk can enter scoring: pool
   construction is the only place chunks are gathered, and it reads only
   owner-scoped records. A foreign chunk is never embedded-against, never
   ranked, never shown to the model.
4. The explicit-title denial (§2) also fires **before** retrieval — a question
   naming an inaccessible paper refuses at the scope layer and never reaches the
   generator.
5. The system prompt contains **no** access-control language at all — it doesn't
   say "only discuss the user's papers", because access is already structural.
   Deliberate: a prompt rule implies the model might see foreign content and
   must be told to ignore it; Q6's model never sees it.

Why this satisfies "structurally incapable": the PDF's failure mode is a model
that *saw* leaked content and must be instructed to hide it. Here the leak
cannot occur — filtering happens on the record set before vectors exist in
memory. The automated test (`tests/test_q6_access.py`) asserts both the refusal
message **and** that zero context blocks carry a foreign `paper_id`, so a future
regression (e.g. moving the filter into a prompt) fails CI.

## Test requirement — not optional
Write an actual, automated test: as simulated user A, directly ask a question that
can only be answered from simulated user B's paper (name it explicitly — "what does
[user B's paper title] say about X?"). Correct behavior: a clear "I don't have access
to that paper" — not a hallucinated guess, not a leaked answer. This must be a
repeatable test case, not a one-off manual check.

**Automated in `backend/tests/test_q6_access.py`:** two papers under two
simulated users; user A asks about user B's paper by title; the test asserts the
access refusal (not Q3's evidence-gap refusal), that no LLM generation ran, and
— the structural claim — that no context block from user B's paper entered the
retrieval pool.

## UI notes (for design/build)
Chat-over-documents interface, extending Q3's pattern, but with: a paper
selector/context indicator showing which paper(s) are in scope for the current
question; multi-paper citations rendered distinctly per source paper (not merged).
No UI element should ever reveal the existence of another user's paper titles,
counts, or metadata to a user who doesn't own them.

**As built (`frontend/src/pages/Q6MultiPaperRag.tsx`):**
- **User switcher** — `alice` / `bob` / `demo-user` chips at the top; the active
  simulated user travels as `X-Owner-Id` on every request. Switching re-fetches
  that user's library only — foreign titles, counts, and metadata never reach the
  browser for the signed-in user, satisfying the no-existence-reveal rule above.
- **Paper scope selector** — "All my papers (n)" or toggle specific papers
  (multi-select); the choice travels as `scope: {paper_ids: [...]}` on `/api/q6/ask`
  (strategy §2, step 1: explicit selector first).
- **Upload dropper** — a PDF dropped while signed in as user X is ingested via Q2's
  pipeline and stamped `owner_id = X` at upload; the record lands in X's library only.
- **Per-paper citations** — one chip per citation: `[n] PaperTitle — Section`,
  tinted with a per-paper hue derived from `paper_id` (chips from different papers
  are visually distinct, never merged), tooltip = chunk snippet. When an answer
  draws on more than one paper, a `Sources: Title (blocks) · Title (blocks)` line
  groups the papers the answer drew from.
- **Access denial rendering** — a foreign-title question renders the deterministic
  `I don't have access to that paper.` refusal with an error-tinted left rule,
  visually distinct from Q3-style evidence-gap refusals; a tip under the example
  prompts invites trying to name another user's paper to see the denial fire.## Working system check

Upload at least two papers under two different simulated users. Ask a normal
question that gets correctly attributed to the right paper. Then run the
access-control test above and confirm it fails safely (refuses) rather than leaking
content.

### Verified live — 2026-09-18, one process on :8000 (`nvidia` / `openai/gpt-oss-20b`,
local MiniLM embeddings, Langfuse Cloud tracing on)

Simulated users sharing the one record store: **alice** → *Attention Is All You Need*
(`p_d332adfd`), **bob** → *BERT: Pre-training of Deep Bidirectional Transformers for
Language Understanding* (`p_c907c606`). Both records carry `owner_id` + inherited index.

1. **Correct attribution (alice, no selector):** "How was the model evaluated?" →
   grounded answer (newstest2013 perplexity, BLEU, beam search, checkpoint averaging),
   5 citation events, every block labelled *Attention Is All You Need*,
   `papers_cited: [{p_d332adfd, "Attention Is All You Need", blocks: 5}]`,
   2,172 in / 418 out tokens. Nothing from bob's BERT chunks appeared — they were not
   in the pool.
2. **THE access-control check (alice names bob's paper):** "What does 'BERT:
   Pre-training of Deep Bidirectional Transformers for Language Understanding' say
   about masked language modeling?" → SSE exactly `run → token → done`, token text
   `I don't have access to that paper.`, `stop_reason: access_denied`, `refused: true`,
   empty citations/papers. No embedding ran, no LLM call, and no event carries bob's
   `paper_id` or title — refuses safely instead of leaking.
3. **Symmetry (bob, own paper):** "What is masked language modeling used for in this
   paper?" → grounded MLM answer, 5 citations attributed to BERT,
   `papers_cited: [{p_c907c606, "BERT: …", blocks: 5}]`.
4. **Owner-scoped listing:** `GET /api/q6/papers` as alice returns only Attention; as
   bob only BERT. Foreign titles never cross the wire.

Automated and repeatable in `backend/tests/test_q6_access.py` (9 tests): the structural
assertion that alice's candidate pool contains zero bob chunks; scope-layer denial on a
foreign title; the no-existence-leak equivalence (foreign title ≡ nonexistent title);
own-title narrowing; selector scoping (a foreign selector id denies); and the
end-to-end HTTP test where embed/LLM are patched to *fail the test if invoked* during a
denial. Full suite: **87 passed**; frontend `tsc --noEmit` + `vite build` clean.

## Decisions log

- **Simulated users, no auth**: `owner_id` arrives on every Q6 request via the
  `X-Owner-Id` header (default `demo-user`), picked in a UI switcher. The
  assignment's point is the *scoping mechanism*, not identity management; a real
  deployment swaps the header for a session/JWT claim and nothing else changes
  (`scoped_records()` already treats it as the filter key).
- **Owner stamping at upload**: `POST /api/q6/papers` tags the record at
  ingestion (`owner_id` beside `paper_id`), so every derived artifact (sections,
  index chunks, qa_history) inherits it. Re-uploading the same PDF under a
  different user creates a *separate* record — no shared-pool mutation.
- **Chunks stamped at pool build, not persisted twice**: the paper record is the
  storage unit and already carries owner/title; the retrieval pool materialises
  per query and stamps each chunk (`paper_id`, `owner_id`, `paper_title`,
  `authors`) as it is gathered — every chunk the vector search ever sees carries
  both tags, and a title edit propagates without re-embedding.
- **Q2/Q3 untouched**: Q6 is a new module reusing Q3's `chunk_sections`,
  `embed_texts`, `stream_text` and storage; `build_index` is invoked as-is and
  the metadata stamp is additive at pool build. No approved Q2/Q3 behaviour
  changed (78 prior tests still pass).
- **Refusal wording**: "I don't have access to that paper." — distinct from
  Q3's "not enough information in this paper" so a grader can tell an
  access denial from an evidence gap. The denial is decided at the scope layer
  from the user's OWN catalog (never by consulting foreign records), so it does
  not leak the *existence* of other users' papers: an unowned title and a
  nonexistent title produce the identical refusal.
- **Langfuse continuity**: one trace per turn, `q6.multi_paper_qa`, with
  `owner_id` metadata and the scoped `paper_ids` — Q5's cost alert (filtered on
  `name = answer`) sees Q6 turns too because Q6's generation keeps the same
  observation name.
