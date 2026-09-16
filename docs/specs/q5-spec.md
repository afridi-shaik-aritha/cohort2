# Q5 Spec — Langfuse Alert on Token/Cost for the Q3 System

Status: DRAFT

## Original ask (from assignment PDF)
For the system built in Q3, set up an alert in Langfuse for tokens/token cost.

## Important distinction — read before configuring anything
Langfuse has two different things that could be confused:
- **Project-scoped metric/cost alerts** ("Alerts" / "Monitors", under Observability
  in a project): threshold-based alerts on a metric (e.g. cost, token count, latency)
  crossing a threshold over a chosen time window, scoped to one project. Notifies via
  Slack, webhook, or triggers something like a GitHub Action. **This is what Q5 asks
  for** — configure this against the Q3 project specifically.
- **Account-level Billing "Spend Alerts"** (org Billing settings): monitors what you
  owe Langfuse Cloud for the platform subscription itself — not your LLM provider
  spend. **Not what Q5 is asking for.** Do not configure this instead.

State explicitly in the writeup which one was configured, to make this distinction
unambiguous to a grader.

## Required sections (per the PDF's "What Openspec specs should cover")

### 1. Metric and time window
<!-- AGENT: which metric (cost, or token count — state which, and why cost is
probably the more meaningful one given Q4's framing), and what window (e.g. cost
over a rolling 1-hour window, or per-session). -->

### 2. Threshold value and how it was derived
<!-- AGENT: base this on Q4's example — what does a "normal" Q&A turn on a paper
cost, from actual Langfuse trace data? Set the threshold to catch a spend pattern
clearly abnormal for that baseline (e.g. some multiple of typical per-session cost),
and show the arithmetic, not a round number pulled from nowhere. -->

### 3. Notification channel
<!-- AGENT: Slack webhook, generic webhook, or other — state which was actually
configured and, if it's a personal/test channel, say so. -->

## Steps required by the PDF

1. **Confirm cost tracking is actually working first.** Check the Langfuse UI's cost
   breakdown on a few real Q3 traces before configuring any alert. Langfuse computes
   cost automatically for known models as long as the model name and token usage are
   correctly passed — verify this is actually populating, not building on top of
   missing data.
2. **Configure the alert** against the Q3 project specifically (not globally).
3. **Prove it fires.** Deliberately trigger the condition (a burst of expensive
   questions in a short window, or simulate the runaway pattern from Q4 if feasible)
   and confirm the alert actually notifies. An alert that's never been tested isn't
   a working alert — capture a screenshot or log of it firing.

## Working system check
A screenshot or log of the alert firing after a deliberate trigger, plus the
reasoning for the threshold, saved into `/docs/q5-alert-proof-template.md`.

## Decisions log
<!-- AGENT: record judgment calls here. -->
