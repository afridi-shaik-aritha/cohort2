# Q5 Spec — Langfuse Alert on Token/Cost for the Q3 System

Status: BUILT (2026-09-18 — see "Working system check" evidence at the end and
`/docs/q5-alert-proof-template.md` for the config path + firing log)

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

### 0. What the alert is scoped to (the metric's filter)

The alert is scoped **to this project** (the only Langfuse project, `jp` Cloud
region) and **to the Q3 system's generations** via a filter, not globally:
`name = answer`. Every Q3 Q&A turn creates exactly one observation named
`answer` (`backend/app/q3_rag_qa/analyzer.py`), so this filter isolates Q3 RAG
traffic from Q2's `section:*` generations and from anything else in the
project. Q4's failure mode is a Q3 follow-up loop, so the tripwire watches Q3.

### 1. Metric and time window

**Two metrics, both on `name = answer`:**

| Metric | Why |
|---|---|
| `totalCost` (sum, USD) | The metric the PDF asks for ("tokens/token cost") and the one Q4 frames the money story in. It is the metric a finance owner would alert on. |
| `totalTokens` (sum) | **Companion leg, not a duplicate.** Cost only exists when the model matches a priced model definition. This system's default local path (LM Studio) reports tokens but no cost, and a future unpriced model would silently blind a cost-only alert. Tokens always exist. |

**Window: 1 hour rolling.** Reasoning:
- Q3 turns are seconds-to-a-minute each; a runaway loop is an *hourly* event
  shape, not a daily one. A daily window would let a bad hour hide inside a
  normal day's total, which is exactly the "one bad night" failure the Q4
  sources describe.
- The healthy system's own cadence fits comfortably: Langfuse recomputes each
  evaluation over the trailing hour, and the burst that proves the alert
  (~120k tokens in ~18 min) is well inside it.

Rejected: per-session alerts (a session is one paper's whole history — too long
to catch a loop early), and daily windows (mask the tail this exists to catch).

### 2. Threshold value and how it was derived

**Thresholds: 100,000 tokens/hour OR $0.008/hour (either leg trips).**

Derivation from measured data, not a round number:

1. **Healthy baseline (real records).** The busiest paper in this repo
   (`p_9c3022cf`, the Attention paper, 10 Q&A turns) totals **24,499 tokens /
   $0.001124** — and that is a full heavy "paper-day" (one Q2 analyze + 10 Q3
   turns), matching Q4's stated $0.0015/paper-day baseline. The most expensive
   *legitimate single turn* measured is the references answer at **5,438
   tokens**; typical turns are 330–2,235.
2. **Runaway reference point (Q4).** Q4's runaway session — a follow-up loop
   re-sending full context with uncapped retries — costs ~10× a healthy
   paper-day: ~260,000 tokens / ~$0.016.
3. **The threshold sits between them, closer to the healthy side:**
   - **100,000 tokens/hour ≈ 4× the busiest healthy paper-day compressed into
     one hour**, and ~38% of the Q4 runaway session. Crossed within ~16 of the
     20 burst turns, i.e. it fires while the loop is still running — not after
     the damage.
   - **$0.008/hour ≈ 7× the healthy paper-day ($0.001124)** and ~5× Q4's
     $0.0015 baseline. The cost leg is deliberately the tighter of the two
     relative to baseline because a *priced frontier* model reaching the same
     token count would blow past it — the token leg catches volume, the cost
     leg catches expensive-volume.
   - A single-turn guard also exists in the ledger module: one turn ≥ 24,000
     tokens (4× the priciest legitimate turn) trips on its own.
4. **Why not lower?** Median turns are ~1,900 tokens, so 100k requires ~50
   normal turns in an hour or fewer than 20 expensive ones — no plausible
   healthy usage of a single-paper Q&A page reaches it. Verified: at the time
   of writing, every paper's ledger reads OK (max 18,401 tokens/h mid-burst,
   18% of threshold — the false-positive check).

### 3. Notification channel

**Webhook automation** (Langfuse Alerts → Automations → Webhook), with the
payload also written to a JSONL evidence log by the monitor script. A Slack
channel was not used: this is a personal/test project with no workspace to
notify, and a webhook is the channel that can be *proved* from the repo
(`backend/scripts/q5_alert_monitor.py` POSTs `ALERT_WEBHOOK_URL` when set and
always appends the record to `docs/evidence/q5-alert-monitor.jsonl`). The
dashboard alert and this script watch the same metric, window, and thresholds,
so the script's log is a faithful proxy for the notification the dashboard
sends — stated explicitly rather than implied.

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

**The alert fired on a deliberate trigger, with the full progression logged.**
`docs/evidence/q5-alert-monitor.jsonl` shows three evaluations through the
Metrics API v2 (same metric, window, filter and threshold as the dashboard
alert): 05:01 **OK** at 18,401 tokens (the false-positive check), 05:05 **OK**
at 41,022, and 05:23 **ALERT** at 104,824 tokens after
`scripts/q5_trigger_burst.py` replayed Q4's failure shape — 20 consecutive
references answers on `p_9c3022cf`, 110,022 tokens in ~23 minutes. The token
leg carried the firing (cost leg $0.006031 < $0.008 on this cheap model —
documented, designed behaviour; see the proof doc's "What tripped" note). The
per-paper ledger independently agrees (`alert: true, trips: {tokens: true,
cost: false}`). 78 backend tests pass (68 prior + 10 Q5, including four that pin
the monitor's trip logic); `tsc` + `vite build` clean; `/q5` page renders the
alert definition, the live ledger and the proof document.

## Decisions log

- **Two legs (cost + tokens) instead of cost alone** — cost is the metric the
  PDF names and the money story Q4 tells, but cost is only computed when the
  model matches a priced definition. This project had *no* cost on any Q3
  generation until this question added a custom model definition (see below),
  which is direct evidence a cost-only alert can sit on missing data.
- **Custom model definition added for `openai/gpt-oss-20b`** — the model is
  served through NVIDIA NIM, which is not among Langfuse's managed pricing
  definitions, so `totalCost` was `null` for all 23 pre-existing Q3 answers.
  Added via `POST /api/public/models` with `matchPattern (?i)^(openai/gpt-oss-20b)$`
  at OpenRouter's published rate ($0.02/1M input, $0.10/1M output — the
  conservative of the two sources used in Q4). Langfuse applies price changes to
  *new* generations only, which is why the proof burst is also the cost
  backfill: verified live, one metadata turn after the change carries
  `costDetails {input: 4.7e-06, output: 8.2e-06, total: 1.29e-05}`.
  **This is a write to the user's Langfuse project and is called out explicitly.**
- **Alert configured in the dashboard, proved by an in-repo monitor** — Langfuse
  Cloud exposes no API for Alerts/Monitors (verified: the SDK's `api` resource
  list has no `monitors`/`alerts`; only `projects`, `metrics`, `observations`,
  `sessions`, ...). Rather than pretend otherwise, Q5 does both halves: the
  spec/proof doc gives the exact dashboard path for the real alert, and
  `scripts/q5_alert_monitor.py` applies the *same* metric, window and threshold
  through the Metrics API v2 so the firing is reproducible and logged in-repo.
- **Tripwire thresholds live in one module** (`app/q5_alert/__init__.py`) so the
  dashboard value, the monitor, and the `/q5` page cannot drift apart.
- **No new store, no scheduler** — the ledger is computed from data Q3 already
  persists (`qa_history[].usage`); the monitor is a script the user can cron,
  not a background service added to the app (master prompt: no scope creep).
- **`/q5` is a status/proof page, not an app section** — per the master prompt's
  "Q5 is mostly a small status/proof page"; it renders the alert definition, the
  live per-paper ledger, and the proof document.
