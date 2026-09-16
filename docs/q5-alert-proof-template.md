# Q5 — Langfuse Alert: Configuration and Proof It Fires

## What was configured

- Alert type: <!-- AGENT: project-scoped metric/cost alert (confirm NOT the
  account-level Billing Spend Alert) -->
- Metric: <!-- AGENT: e.g. total cost -->
- Time window: <!-- AGENT: e.g. rolling 1 hour -->
- Threshold: <!-- AGENT: value -->
- Notification channel: <!-- AGENT: Slack / webhook / other -->

## Threshold derivation

<!-- AGENT: show the arithmetic — baseline per-turn/per-session cost from real Q3
Langfuse traces, multiplied/scaled to a level that's clearly abnormal, per the Q4
example. -->

## Cost tracking sanity check (done before configuring the alert)

<!-- AGENT: confirm cost is actually showing up per-generation (not just aggregate)
in the Langfuse UI on real Q3 traces, before this alert was configured. Link or
describe what was checked. -->

## Proof it fires

<!-- AGENT: describe how the condition was deliberately triggered (e.g. burst of
expensive questions in the window), and attach/describe the screenshot or log
showing the alert notification firing. -->
