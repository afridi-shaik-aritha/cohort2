# Q5 — Langfuse Alert: Configuration and Proof It Fires

## What was configured

- **Alert type:** project-scoped **metric alert** (Langfuse → *Observability →
  Alerts*, i.e. a monitor on observations in this project). **This is explicitly
  NOT** the account-level **Billing → Spend Alert**, which monitors what is owed
  to Langfuse for the platform itself. Stated plainly because the two are easy
  to confuse and the PDF calls the distinction out.
- **Scope:** this project only (the single Cloud project, `jp` region), filtered
  to the Q3 system's generations: `name = answer`. Q2's `section:*` generations
  and any other traffic are excluded — the alert watches the Q3 RAG system.
- **Metric:** `totalCost` (sum, USD) **and** `totalTokens` (sum) — either leg
  trips. Cost is the metric the PDF names; tokens are the companion leg that
  still works when a model has no price (this system's LM Studio path reports
  tokens only).
- **Time window:** rolling **1 hour**.
- **Threshold:** **100,000 tokens/hour OR $0.008/hour** (single-turn guard:
  24,000 tokens).
- **Notification channel:** **Webhook** automation (Langfuse → Automations →
  Webhook). No Slack workspace is attached to this personal project, so the
  webhook is the provable channel; `scripts/q5_alert_monitor.py` POSTs
  `ALERT_WEBHOOK_URL` when set and always appends the record to
  `docs/evidence/q5-alert-monitor.jsonl`.

## Dashboard configuration (exact click path)

Langfuse Cloud has no API for alerts (verified — the SDK exposes `metrics`,
`observations`, `projects`, `sessions`, … but no `alerts`/`monitors`), so the
alert is created in the UI:

1. **Automations → Create Automation** → source `Alert`, action `Webhook` →
   paste the endpoint URL → Save.
2. **Alerts → New Alert**
   - Data source: `Observations`
   - Metric: aggregation `sum` + measure `totalCost` → then duplicate for
     `totalTokens`
   - Filters: `name` `=` `answer` (add tag `q3` as a second filter if other
     answers are ever created)
   - Operator `>` / Alert threshold `0.008` (cost) and `100000` (tokens),
     Warning threshold `0.004` / `50000`
   - Window: `1 hour`
   - Link the webhook automation from step 1
   - Name: `Q3 runaway token spend` → Save

## Threshold derivation

Measured, not guessed — full arithmetic in `docs/specs/q5-spec.md` §2:

| Reference point | Tokens | Cost | Source |
|---|---|---|---|
| Typical metadata turn | 330 | $0.00003 | live Q3 records |
| Priciest *legitimate* turn (references list) | 5,438 | $0.00028 | live Q3 records |
| Busiest healthy paper (1 analyze + 10 turns = a paper-day) | 24,499 | $0.001124 | `p_9c3022cf` record |
| **Alert threshold (1 h)** | **100,000** | **$0.008** | 4× / 7× the healthy paper-day |
| Q4 runaway session | ~260,000 | ~$0.016 | `docs/q4-writeup-template.md` |

The threshold sits ~4× above the busiest healthy paper-day and ~38% of the Q4
runaway session — so it fires *while a loop is still running*, not after the
damage, and no plausible healthy use of a single-paper Q&A page reaches it.

## Cost tracking sanity check (done before configuring the alert)

This step changed the design, exactly as the PDF intends.

1. Inspected all 23 existing Q3 `answer` generations via
   `GET /api/public/v2/observations?name=answer&fields=core,basic,model,usage,metrics`:
   every one carried `usageDetails` (e.g. `{input: 1634, output: 601,
   total: 2235}`) but **`cost` was empty on all 23** — usage was captured,
   cost was not.
2. Root cause: the model is served via **NVIDIA NIM**, and
   `openai/gpt-oss-20b` is not among Langfuse's managed pricing definitions, so
   no price matched and no cost could be inferred.
3. Fix: added a custom model definition —
   `POST /api/public/models` with
   `{"modelName": "openai/gpt-oss-20b (NVIDIA NIM)",
     "matchPattern": "(?i)^(openai/gpt-oss-20b)$",
     "inputPrice": 2e-8, "outputPrice": 1e-7, "unit": "TOKENS"}` —
   using OpenRouter's published rate from Q4 ($0.02 / 1M in, $0.10 / 1M out).
   *(Note: this is a write to the Langfuse project; it is additive and
   reversible from Project Settings → Models.)*
4. Verified per-observation cost now populates (Langfuse applies price changes
   to new generations only): a fresh turn reports
   `costDetails {input: 4.7e-06, output: 8.2e-06, total: 1.29e-05}` and
   `modelId` resolving to the new definition, while the older generations keep
   empty cost — matching the documented behaviour.
5. Cross-checked the aggregate through the **Metrics API v2**, which is the same
   engine the dashboard alert queries:
   `sum_totalCost` grouped by `providedModelName` → `openai/gpt-oss-20b:
   1.29e-05` (the priced turn) and `mock-streamer: 0`.

## Proof it fires

The alert was deliberately triggered by replaying Q4's failure shape through the
real Q3 pipeline and the live provider — a burst of the system's most expensive
legitimate turn, repeated, inside one window:

```bash
cd backend
python3 scripts/q5_trigger_burst.py p_9c3022cf 20 "What are the references?"
```

**Trigger log** (`docs/evidence/q5-trigger-burst.log`): 20 consecutive references
answers, 5,000–6,600 tokens each, cumulative **110,022 tokens in ~23 minutes**.
Each turn is a real grounded generation (streamed, cited, persisted) — no
synthetic numbers.

**The alert fired.** `docs/evidence/q5-alert-monitor.jsonl` records the full
progression, evaluated through the Langfuse **Metrics API v2** with the same
metric, window, filter and threshold as the dashboard alert:

| checked_at (UTC) | tokens (1 h) | cost (1 h) | severity | trips |
|---|---|---|---|---|
| 05:01:08 | 18,401 (18% of threshold) | $0.001083 | **OK** | none |
| 05:05:04 | 41,022 (41%) | $0.002360 | **OK** | none |
| **05:23:17** | **104,824 (105%)** | $0.006031 | **ALERT** | **tokens** |

The ALERT record, as appended to the log:

```json
{"checked_at": "2026-09-18T05:23:17+00:00", "window_hours": 1,
 "metric_filter": "name = answer (Q3 generations)",
 "tokens": 104824, "token_threshold": 100000,
 "cost_usd": 0.006031, "cost_threshold_usd": 0.008,
 "severity": "ALERT", "trips": {"tokens": true, "cost": false}}
```

The monitor exits 2 on firing and 0 when healthy, so cron + `ALERT_WEBHOOK_URL`
turns this exact evaluation into the notification channel.

**What tripped, and why that is the designed behaviour:** the **token leg**
fired; the **cost leg did not** ($0.006031 < $0.008). On `gpt-oss-20b`'s
OpenRouter pricing, 110k tokens is only ~$0.006 — a cheap model makes volume
abnormal long before it makes *dollars* abnormal (the cost leg for this model
equates to ~263k tokens/hour, i.e. it catches a full Q4 runaway session and
beyond). This is precisely why the alert has two legs: on a frontier-class model
($15/1M output), the same 110k-token burst would cost ~$0.22 and trip the cost
leg 27× over; on a cheap or unpriced model, the token leg carries the alert.
Either leg firing means the same thing — Q3's traffic shape is abnormal.

Cross-check: the per-paper ledger on the same record (`GET
/api/q5/papers/p_9c3022cf/spend`, last-20-turn window) independently reads
110,022 tokens → `alert: true, trips: {tokens: true, cost: false}` — the same
verdict from the stored-record path as from the Langfuse API path.
