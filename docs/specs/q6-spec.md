# Q6 Spec — RAG with Citations (Multi-Document, Access-Scoped)

Status: DRAFT

## Original ask (from assignment PDF)
Extend Q3 to support multiple papers from multiple users sharing one index. Retrieval
must figure out which document(s) an answer should draw from, and the system must
actively prevent one user's questions from ever surfacing another user's content —
not just fail to mention it, but be structurally incapable of retrieving it.

## Required sections (per the PDF's "What Openspec specs should cover")

### 1. Metadata schema
<!-- AGENT: exact fields tagged on every chunk in the vector index. At minimum
paper_id, owner_id, title, authors (extracted at ingestion per the Q3 lesson about
metadata not being reliably retrievable later). List the full schema here. -->

### 2. Cross-paper ambiguity resolution
A question like "how was this tested?" is ambiguous once more than one paper is in
scope. <!-- AGENT: state which strategy is used — conversation context resolving
"this" to a specific paper, an explicit paper selector in the UI, or retrieving
across all of a user's papers and letting top results decide. Don't leave this
accidental. -->

### 3. Citation format for multi-paper answers
<!-- AGENT: every answer must name the specific paper(s) it drew from by title, not
just an internal chunk ID. If an answer draws on two papers, both must be cited
distinctly — no silent blending of claims across papers without attribution. Define
the exact citation rendering format here. -->

### 4. Where and how access control is enforced — state this explicitly
**Access control MUST be a retrieval-time metadata filter on the vector search
itself** (e.g. `WHERE owner_id = current_user`), so a restricted document is never
even retrieved, let alone shown to the model. Enforcing this only via a system-prompt
instruction ("only discuss the user's own papers") is NOT sufficient — a motivated or
unlucky query can still surface leaked content because the model saw it in context.
This mirrors the "access control leakage" failure mode in production RAG systems.

<!-- AGENT: confirm and describe exactly how the filter is implemented in this
system's vector store (e.g. metadata filter param in the vector DB query call) —
do not describe this in the abstract, point to the actual mechanism used. -->

## Test requirement — not optional
Write an actual, automated test: as simulated user A, directly ask a question that
can only be answered from simulated user B's paper (name it explicitly — "what does
[user B's paper title] say about X?"). Correct behavior: a clear "I don't have access
to that paper" — not a hallucinated guess, not a leaked answer. This must be a
repeatable test case, not a one-off manual check.

## UI notes (for design/build)
Chat-over-documents interface, extending Q3's pattern, but with: a paper
selector/context indicator showing which paper(s) are in scope for the current
question; multi-paper citations rendered distinctly per source paper (not merged).
No UI element should ever reveal the existence of another user's paper titles,
counts, or metadata to a user who doesn't own them.

## Working system check
Upload at least two papers under two different simulated users. Ask a normal
question that gets correctly attributed to the right paper. Then run the
access-control test above and confirm it fails safely (refuses) rather than leaking
content.

## Decisions log
<!-- AGENT: record judgment calls (e.g. how owner_id/simulated users are modeled
without building full auth) here. -->
