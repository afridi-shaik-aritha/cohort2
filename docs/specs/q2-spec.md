# Q2 Spec — Paper Inference Engine (with Langfuse built in)

Status: DRAFT

## Original ask (from assignment PDF)
Build an inference engine where the user uploads a paper, and the system drafts:
(1) Technical summary, (2) Intuition of the paper, (3) Prerequisite learning
required, (4) Summary of the paper. Must have Langfuse observability built in.

## Before starting: confirm with the user
Langfuse Cloud or self-hosted? Do not proceed with Langfuse setup until answered.

## Required sections (per the PDF's "What Openspec specs should cover")

### 1. Extraction pipeline
<!-- AGENT: which PDF text extraction library/approach, how two-column layouts and
figure/table garbage text are handled or mitigated. -->

### 2. Prompt strategy per section — one call vs. four, and why
<!-- AGENT: state the decision explicitly and the tradeoff reasoning (cost/latency
vs. control and independent evalability). This choice directly feeds Q4's example. -->

### 3. Chunking/truncation approach for long papers
<!-- AGENT: truncate vs. chunk-and-map-reduce vs. large-context model with full
paper — state which, and why, given expected paper lengths. -->

### 4. Langfuse trace/span structure
<!-- AGENT: one trace per paper-upload request, child generations per section
(and per extraction step if it uses an LLM). What metadata tags each trace
(paper title/ID, user ID if applicable). -->

## Output contract
The four sections and their intended register:
- Technical summary — precise, assumes field knowledge
- Intuition — plain language, "explain to a smart friend outside the field"
- Prerequisite learning — concrete named concepts, not vague ("attention
  mechanisms," "contrastive loss," "the paper this one builds on" — that level of
  specificity)
- Summary — standard abstract-style

## UI notes (for design/build)
PDF upload control. Clear loading/progress state while the four sections generate
(especially if using four separate calls — show per-section progress rather than one
opaque spinner). Four-section result layout, each section clearly labeled and
visually distinct enough to scan independently.

## Working system check (must pass before marking this question done)
Upload a real paper of nontrivial length (not a 2-page abstract). Confirm all four
sections render. Confirm the Langfuse trace tree shows the full generation pipeline
with token counts on each step (not just an aggregate).

## Decisions log
<!-- AGENT: record judgment calls (e.g. PDF library choice, chunking library) here. -->
