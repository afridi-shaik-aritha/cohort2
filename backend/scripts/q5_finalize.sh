#!/bin/bash
# Waits for the Q5 trigger burst to finish, then runs the alert monitor.
# Keeps the whole "trigger → evaluate → log" sequence in one background job so
# the evidence log captures the firing without an interactive session.
set -u
cd "$(dirname "$0")/.."
BURST_LOG=/tmp/q5_burst2.log
OUT=/tmp/q5_post_burst.log

# wait for the burst process to disappear (bounded: 20 min)
for _ in $(seq 1 240); do
  pgrep -f q5_trigger_burst >/dev/null || break
  sleep 5
done

{
  echo "=== burst finished at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  tail -3 "$BURST_LOG"
  echo
  echo "=== alert monitor (post-burst, same metric/window/threshold) ==="
  python3 scripts/q5_alert_monitor.py --window 1
  echo "monitor_exit=$?"
} > "$OUT" 2>&1

# preserve the burst log as evidence next to the monitor log
mkdir -p ../docs/evidence
cp "$BURST_LOG" ../docs/evidence/q5-trigger-burst.log
echo "DONE" >> "$OUT"