# Q3 Spec — RAG Extension: Question Answering Over the Paper

Status: DRAFT

## Original ask (from assignment PDF)
Extend Q2 into a RAG system where the user can ask questions about the paper
(e.g. "how was this tested?", "what are the references?", "who are the authors?")
and the system answers from the paper's content.

## Required sections (per the PDF's "What Openspec specs should cover")

### 1. Chunking strategy and why
<!-- AGENT: chunk by section (methodology, results, references) if extraction
preserves structure, vs. fixed-size windows — state the choice and reasoning. -->

### 2. How each of the three example question types is handled
State the decision explicitly even if the underlying mechanism ends up the same
for more than one.
- **"How was this tested?"** (semantic/conceptual) — <!-- AGENT: retrieval path -->
- **"What are the references?"** (structural, not semantic) — <!-- AGENT: whole
  references section as a retrieved block, or a separately parsed structured
  field captured at ingestion? State which and why. -->
- **"Who are the authors?"** (metadata, often header/title block, may get
  poorly chunked or dropped by extraction) — <!-- AGENT: captured explicitly at
  Q2 ingestion time rather than relying on retrieval, per the PDF's own
  recommendation — confirm this is how it's implemented. -->

### 3. Grounding / refusal behavior
<!-- AGENT: what happens when a question can't be answered from the paper (e.g.
"what did the authors have for breakfast") — must refuse rather than hallucinate.
Describe the mechanism (e.g. retrieval score threshold + explicit refusal prompt). -->

## Citations
Every answer must indicate which part of the paper it came from (section name or a
snippet) so the user can verify the claim against the source.

## Langfuse continuity
Each Q&A turn is a traced generation with retrieved context visible in the trace
(so Q5's alerting has something real to monitor).

## UI notes (for design/build)
Chat-over-document interface. Citations rendered as small inline references pointing
to a source panel (per design tokens), not raw footnote numbers. Clear "I don't have
enough information to answer that from this paper" state for refusals — should read
as an honest limitation, not an error.

## Working system check (must pass before marking this question done)
Run all three example questions ("how was this tested?", "what are the references?",
"who are the authors?") plus one deliberately unanswerable question. Confirm correct,
cited answers for the first three and an honest refusal for the fourth.

## Decisions log
<!-- AGENT: record judgment calls here. -->
