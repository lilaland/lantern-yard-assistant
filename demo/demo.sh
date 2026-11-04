#!/usr/bin/env bash
# Smoke demo: plays both terminals in one run so the whole path is exercised end to end.
#
#   Terminal A (operator)  -> simulator.py serve / scenario / approve      (shown as OPERATOR below)
#   Terminal B (assistant) -> python -m assistant ...                        (shown as ASSISTANT below)
#
# Usage:  demo/demo.sh [--live] [--no-pause]
#   --live      let the model narrate (needs ANTHROPIC_API_KEY in .env or the environment); default is --dry
#   --no-pause  do not wait for Enter between steps (CI / quick smoke)
#
# Every run uses a fresh simulator state file and a fresh assistant state directory, both under
# gitignored paths, so nothing from one rehearsal leaks into the next.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${DEMO_PORT:-8765}"
STAMP="$(date +%Y%m%d-%H%M%S)"
SIM_STATE=".state/demo-$STAMP.sqlite"
SIM_LOG=".state/demo-$STAMP.sim.log"
export MARBLE_API_BASE="http://127.0.0.1:$PORT"
export MARBLE_STATE_DIR=".marble/demo-$STAMP"
PY="${PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3

MODE="--dry"; PAUSE=1
for a in "$@"; do
  case "$a" in
    --live) MODE="" ;;
    --no-pause) PAUSE=0 ;;
    *) echo "unknown option $a"; exit 2 ;;
  esac
done
if [ -z "$MODE" ] && [ -z "${ANTHROPIC_API_KEY:-}" ] && ! grep -Eq '^ANTHROPIC_API_KEY=.+' .env 2>/dev/null; then
  echo "--live needs ANTHROPIC_API_KEY in .env or the environment; running --dry instead."
  MODE="--dry"
fi

ASSISTANT() { echo "  ASSISTANT> python -m assistant $MODE $*"; "$PY" -m assistant $MODE "$@"; }
OPERATOR()  { echo "  OPERATOR > python3 simulator.py $*"; python3 simulator.py "$@" --state "$SIM_STATE" --port "$PORT"; }
step() {
  echo; echo "======================================================================"
  echo "STEP $1"; echo "$2"; echo "----------------------------------------------------------------------"
  if [ "$PAUSE" = 1 ]; then read -r -p "[Enter to run] " _; fi
}

# ----- fixture --------------------------------------------------------------------------
mkdir -p .state "$MARBLE_STATE_DIR"
if curl -sf "$MARBLE_API_BASE/health" >/dev/null 2>&1; then
  echo "Something already answers on $MARBLE_API_BASE. Stop it or set DEMO_PORT." ; exit 1
fi
echo "Starting the fixture: simulator.py serve --mode agent --port $PORT --state $SIM_STATE"
python3 simulator.py serve --mode agent --port "$PORT" --state "$SIM_STATE" >"$SIM_LOG" 2>&1 &
SIM_PID=$!
cleanup() { kill "$SIM_PID" 2>/dev/null || true; }
trap cleanup EXIT
for _ in $(seq 1 50); do curl -sf "$MARBLE_API_BASE/health" >/dev/null 2>&1 && break; sleep 0.1; done
curl -sf "$MARBLE_API_BASE/health" >/dev/null || { echo "simulator did not start; see $SIM_LOG"; exit 1; }
echo "Assistant state: $MARBLE_STATE_DIR   (model: ${MODE:-live}; token: demo-agent-site-01 only)"

step 1 "Onboard with the documented defaults. Each line is a belief with a source; a person can change any of them."
ASSISTANT onboard --defaults

step 2 "Morning brief at dataset revision 1. Headline, evidence, uncertainty, next step, runway, then retained vs rechecked."
ASSISTANT brief

step 3 "Why does the assistant believe something? The ledger keeps the chain, including who said it."
ASSISTANT why invoice.INV-099.delivery_date
ASSISTANT why vendor.V1.delivery_days

step 4 "The office corrects INV-099 (operator action). The brief rechecks: observation cleared, assertions superseded, human beliefs untouched."
OPERATOR scenario --name delivery-correction
ASSISTANT brief

step 5 "Order proposal with the rationale table and the exact JSON. Nothing is sent."
ASSISTANT order --propose-only

step 6 "Full order path with a lost response: draft -> operator approves -> submit (response dropped) -> verify by reference."
ORDER_LOG="$MARBLE_STATE_DIR/order.log"
ASSISTANT order --yes --timeout 90 --poll-seconds 1 >"$ORDER_LOG" 2>&1 &
ORDER_PID=$!
DRAFT_ID=""
for _ in $(seq 1 300); do
  DRAFT_ID="$(grep -oE '\[drafted\] D-[A-Za-z0-9]+' "$ORDER_LOG" 2>/dev/null | head -1 | awk '{print $2}')" || true
  [ -n "$DRAFT_ID" ] && break
  sleep 0.2
done
if [ -z "$DRAFT_ID" ]; then echo "no draft appeared; order log:"; cat "$ORDER_LOG"; exit 1; fi
echo "  (assistant printed the draft and is polling for approval: $DRAFT_ID)"
OPERATOR scenario --name supplier-timeout
OPERATOR approve --draft-id "$DRAFT_ID"
RC=0; wait "$ORDER_PID" || RC=$?
echo "----- assistant output (order) -----"
cat "$ORDER_LOG"
echo "----- exit code $RC (0 = accepted and verified) -----"

step 7 "After a restart nothing is pending; the ledger remembers the order and the beliefs."
ASSISTANT order --resume
ASSISTANT memory

echo
echo "Done. Simulator state: $SIM_STATE ; assistant state: $MARBLE_STATE_DIR ; simulator log: $SIM_LOG"
echo "Tests: .venv/bin/python -m pytest -q"
