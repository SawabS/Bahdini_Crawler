#!/bin/bash
# Unattended overnight driver for generate_qa_openrouter.py.
#
# Three things this adds over running the generator directly:
#
#  1. It survives the terminal. Launched via run_overnight_start.sh, which
#     puts it in its own session, so closing the terminal or ending a Claude
#     Code session does not take it down -- exactly how the first attempt
#     died, since that job was a child of the session's shell.
#  2. It keeps the Mac awake, via `caffeinate -ims` in the launcher.
#  3. It restarts the generator if it exits with work still pending, and
#     stops on its own once the queue is empty.
#
# Budget safety. `--budget-usd` in the generator is PER RUN, so a naive
# restart loop would silently reset the cap on every iteration. Instead the
# remaining credit is read back from OpenRouter before each attempt and
# passed as that attempt's budget, minus a small reserve -- so the cap
# tracks real cumulative spend across restarts. The key's own server-side
# limit is the hard backstop underneath that: 402 makes the generator stop
# cleanly rather than retry.
#
#   bash qa_generation/run_overnight_start.sh     # detached; the entry point
#   bash qa_generation/run_overnight.sh           # foreground, for debugging

set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QA="$REPO/qa_generation"
PY=/opt/miniconda3/envs/ai/bin/python
LOG_DIR="$QA/output"
LOG="${OVERNIGHT_LOG:-$LOG_DIR/overnight_$(date +%Y%m%d_%H%M%S).log}"

CONCURRENCY=${CONCURRENCY:-32}
BATCH_SIZE=${BATCH_SIZE:-128}
MAX_ATTEMPTS=${MAX_ATTEMPTS:-40}
RESERVE_USD=${RESERVE_USD:-3}      # stop just short of 402 rather than on it
COOLDOWN_S=${COOLDOWN_S:-30}

mkdir -p "$LOG_DIR"
exec >>"$LOG" 2>&1

say() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

api_key() { sed -n 's/^OPENROUTER_API_KEY=//p' "$REPO/.env" | tr -d ' \r\n'; }

# Echoes remaining USD, or nothing on any failure. An empty result means
# "unknown" and must never be treated as zero -- a transient curl failure
# should not look like an exhausted key.
credit_remaining() {
  curl -sS --max-time 30 https://openrouter.ai/api/v1/key \
    -H "Authorization: Bearer $(api_key)" 2>/dev/null \
  | "$PY" -c 'import json,sys
try:
    print("%.2f" % json.load(sys.stdin)["data"]["limit_remaining"])
except Exception:
    pass' 2>/dev/null
}

say "=== overnight run starting ==="
say "repo=$REPO  concurrency=$CONCURRENCY  batch=$BATCH_SIZE  max_attempts=$MAX_ATTEMPTS"

cd "$QA" || { say "FATAL: cannot cd to $QA"; exit 1; }

attempt=0
while [ "$attempt" -lt "$MAX_ATTEMPTS" ]; do
  attempt=$((attempt + 1))

  remaining=$(credit_remaining)
  if [ -z "$remaining" ]; then
    say "attempt $attempt: could not read remaining credit; retrying in ${COOLDOWN_S}s"
    sleep "$COOLDOWN_S"
    continue
  fi

  budget=$(awk "BEGIN{b=$remaining-$RESERVE_USD; print (b>0?b:0)}")
  if awk "BEGIN{exit !($budget <= 0)}"; then
    say "credit exhausted (remaining \$$remaining); stopping."
    break
  fi

  say "attempt $attempt: credit \$$remaining, this run capped at \$$budget"
  "$PY" -u generate_qa_openrouter.py \
      --concurrency "$CONCURRENCY" \
      --batch-size "$BATCH_SIZE" \
      --budget-usd "$budget"
  say "attempt $attempt: generator exited rc=$?"

  # The generator prints "<N> chunks total, <M> pending after resuming ..."
  # at startup. Reuse that value instead of paying for another full index
  # pass over the 700MB chunk queue just to ask whether anything is left.
  pending=$(grep -o '[0-9][0-9]* pending after resuming' "$LOG" | tail -1 | awk '{print $1}')
  say "attempt $attempt: pending at last index = ${pending:-unknown}"

  if [ "${pending:-1}" = "0" ]; then
    say "queue empty; done."
    break
  fi

  sleep "$COOLDOWN_S"
done

say "=== overnight run finished after $attempt attempt(s) ==="
say "final credit remaining: \$$(credit_remaining)"
