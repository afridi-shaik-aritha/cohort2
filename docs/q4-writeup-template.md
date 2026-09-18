# Q4 — "Runaway Token Spend": Definition, Source, and a Grounded Example

## Definition

Runaway token spend is when an LLM system's token consumption — and therefore
its cost — grows unexpectedly and uncontrollably instead of scaling
proportionally with usage. The engine is almost always one of four mechanisms:
an agent or retry loop that never terminates, per-attempt context that grows
with each iteration, a chain of dependent calls that keeps expanding, or a
batch job that re-pays the full prompt cost for work already done.

## Source

- Link: https://machinelearningmastery.com/identifying-token-costs-hiding-in-your-agentic-loop/
- Title: "Identifying Token Costs Hiding in Your Agentic Loop"
- Publisher: MachineLearningMastery.com (Vinod Chugani, 2026-08-07)
- What it says, in our own words: token costs in agentic loops compound rather
  than adding up, because each step re-sends an ever-growing context; the
  article names five concrete traps and prescribes treating context as a
  constrained resource — compact history, prune failures, filter payloads,
  route easy work to small models, and inject only what each step needs.

A second, corroborating source: PromptRails, "Runaway AI costs are an
architecture problem" (2026-07-08,
https://promptrails.ai/blog/runaway-ai-costs-budgets-and-kill-switches) — in
our own words, it argues runaway cost is missing safety architecture, not an
expensive model, and prescribes per-request limits, per-workflow budgets,
anomaly detection, and a tested kill switch.

## Example — grounded in this system's Q2/Q3 implementation

**Trigger:** three compounding habits already visible in this codebase's design
space. First, full-context re-send per call: Q2 sends the paper text with every
section call, and Q3 sends up to 12,000 chars of retrieved blocks with every
question. Today those bounds are deliberate; a follow-up feature that appends
whole conversation history plus prior blocks, or an uncapped retry wrapper,
would make per-turn input grow with history. Second, retry storms: each failed
attempt appends prior output plus errors, so retry N costs more than retry N-1;
the references path already needed its cap raised twice because a 40-entry list
plus reasoning overhead kept hitting the ceiling. Third, digest amplification:
map-reduce runs one LLM call per chunk before four section calls, so
re-summarizing per follow-up instead of reusing stored sections/index would
multiply every Q&A turn by the full ingestion cost.

**What it would look like in the Langfuse trace:** the healthy shape is small —
one `q3.paper_qa` trace per turn: root span, one `retrieve` span, one `answer`
generation with roughly 1–3k input tokens. The runaway shape is unmistakable:
dozens of `answer` generations in one trace/session, climbing input counts turn
over turn, or repeated near-identical generations from retries. The `retrieve`
span would show growing block counts while the question stays the same.

**Cost impact at scale:** built from this system's measured tokens and public
pricing for the model actually used (`openai/gpt-oss-20b` via OpenRouter: $0.02
/ 1M input, $0.10 / 1M output; cross-checked at $0.03 / $0.13 on llm-cost.io, so
the OpenRouter figures are the conservative basis). Healthy baseline per
paper-day — one analyze plus ten Q&A turns — is about 26k input and 10k output
tokens, or about $0.0015. A runaway follow-up loop that re-sends full context
and retries long answers four times costs about $0.016 per bad session, roughly
ten times the healthy day. At 1,000 papers/day with 1% going runaway, that is
about $0.15/day extra ($4–5/month) on this cheap model; the same tenfold pattern
on a frontier-class model is about $20/day ($600/month), and an unattended
overnight loop is the five-figure story both sources warn about.

## Link forward to Q5

Q5's alert exists because of the tenfold session above: normal turns cost only
hundreds to a few thousand tokens, so a threshold set at a small multiple of
the healthy baseline can only fire when the trace shape changes to repeated
generations and climbing inputs. Q5 derives that threshold from the
$0.0015/paper-day baseline computed here and proves it fires on a deliberately
triggered burst.
