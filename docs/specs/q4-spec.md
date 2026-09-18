# Q4 Spec — Explain "Runaway Token Spend" with a Cited Example

Status: BUILT (writeup complete 2026-09-18 — see `/docs/q4-writeup-template.md`)

Note: Q4 is a written deliverable, not a UI feature. The output is
`/docs/q4-writeup-template.md` filled in, plus a small Home card ("Not built" →
"Done") that links to that doc (or a simple rendered page for it — agent's choice,
keep it minimal). This spec just tracks the research/reasoning steps; there's no
separate frontend component to build here.

## Original ask (from assignment PDF)
What is the meaning of "runaway token spend"? Explain through an example and cite
where you got the answer from.

## Steps required by the PDF

### 1. Find a real source
Search for the term, read at least one credible source (vendor docs, an engineering
blog post from a company running LLM systems in production, or a
research/practitioner write-up). Cite properly: link, title, one-line note on what it
says, in your own words — no pasted quotes.

<!-- AGENT: record the source(s) found here before writing the final doc. -->
Primary: MachineLearningMastery.com, "Identifying Token Costs Hiding in Your
Agentic Loop" (Chugani, 2026-08-07) — chosen because it names concrete,
checkable compounding mechanisms (context accumulation, failure retention,
payload bloat, model oversizing, prompt duplication) rather than generic cost
advice. Corroborating: PromptRails, "Runaway AI costs are an architecture
problem" (2026-07-08) — chosen for the fuse-box framing (limits, budgets,
anomaly detection, kill switch) that maps directly onto this system's existing
defenses and Q5's alert.

### 2. Definition (in your own words)
Runaway token spend: an LLM system's token consumption (and therefore cost) grows
unexpectedly and uncontrollably — usually due to loops, retries, unbounded context
growth, or a chain of tool calls that keeps expanding rather than terminating —
rather than growing proportionally and predictably with usage.

### 3. Example grounded in this actual system (Q2/Q3), not a hypothetical

Filled in `docs/q4-writeup-template.md` §Example: the Q3 follow-up loop
(re-send full context + uncapped retries), with trigger, trace shape, and
cost arithmetic from live traces and public pricing.

### 4. Connect forward to Q5
This definition is the reason a Langfuse alert (Q5) is being set up — state the link
explicitly so Q5's threshold reasoning reads as a continuation, not a fresh start.

## Working system check
A clear, correctly-cited definition (link + source name) and an example specific to
the system built this week, with rough real numbers — not a generic "imagine a
chatbot" scenario.

## Decisions log
Chosen example: the Q3 follow-up loop (re-send full context + uncapped retries
on long answers), because it is the closest real failure mode to code already
in this repo (context budgets, references answer-cap history) rather than an
invented scenario. Baseline numbers come from Q3's live traces; pricing from
OpenRouter's gpt-oss-20b page ($0.02/$0.10 per 1M), cross-checked on
llm-cost.io. Q4 intentionally ships as docs-only (Home card links to the
writeup page) — no backend/frontend feature work, per the master prompt.
