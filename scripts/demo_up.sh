#!/usr/bin/env bash
# Bring the STRATA workstation online for the hosted demo:
#   1. copy the Revit extension (builder + listener) to the Windows-side STRATA folder,
#   2. start the STRATA API on 127.0.0.1:8001 (unless it is already running),
#   3. publish it through ngrok on your fixed domain (STRATA_NGROK_DOMAIN in .env),
#   4. check STAAD, Revit and the public address.
# Ctrl-C stops what this script started. Revit itself is started by hand on Windows (see docs/demo_runbook.md).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WIN_DIR="/mnt/c/Users/Public/Documents/STRATA"
LOGS="$ROOT/output/logs"; mkdir -p "$LOGS"
env_value() { grep -E "^$1=" "$ROOT/.env" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'"'"; }
DOMAIN="$(env_value STRATA_NGROK_DOMAIN)"
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

echo "== 1. Revit extension -> $WIN_DIR/revit"
mkdir -p "$WIN_DIR/revit"
rsync -a --exclude "__pycache__" "$ROOT/revit/" "$WIN_DIR/revit/"   # no --delete: the template lives there too
[ -f "$WIN_DIR/revit/STRATA_template.rte" ] && echo "   template found" \
  || echo "   !! STRATA_template.rte missing: save it from Revit once (docs/demo_runbook.md, step A3)"

echo "== 2. STRATA API on 127.0.0.1:8001"
if curl -sf -m 3 http://127.0.0.1:8001/api/health >/dev/null; then
  echo "   already running"
else
  (cd "$ROOT/web" && exec "$ROOT/.venv/bin/python" scripts/dev_api.py) >"$LOGS/api.log" 2>&1 &
  pids+=($!)
  for _ in $(seq 20); do curl -sf -m 2 http://127.0.0.1:8001/api/health >/dev/null && break; sleep 1; done
  curl -sf -m 3 http://127.0.0.1:8001/api/health >/dev/null || { echo "   !! API did not start; see $LOGS/api.log"; exit 1; }
  echo "   started (log: $LOGS/api.log)"
fi

echo "== 3. ngrok"
if [ -z "$DOMAIN" ]; then
  echo "   STRATA_NGROK_DOMAIN is not set in .env: local only (http://localhost:3000 with npm run dev)"
elif ! command -v ngrok >/dev/null; then
  echo "   !! ngrok is not installed (docs/demo_runbook.md, step A4)"
else
  ngrok http --url="$DOMAIN" 8001 --log=stdout >"$LOGS/ngrok.log" 2>&1 &
  pids+=($!)
  sleep 4
fi

echo "== 4. Checks"
curl -s -m 8 http://127.0.0.1:8001/api/health | "$ROOT/.venv/bin/python" -c '
import json, sys
h = json.load(sys.stdin)
ok = lambda b: "OK " if b else "-- "
print("   " + ok(h.get("staad")) + "STAAD.Pro")
print("   " + ok(h.get("revit")) + "Revit listener" + ("" if h.get("revit") else "  (" + str(h.get("revit_error")) + ")"))
print("   " + ok(h.get("revit_template")) + "Revit STRATA template")
print("   LLM: " + str(h.get("llm")) + " / " + str(h.get("llm_model")))'
if [ -n "$DOMAIN" ] && command -v ngrok >/dev/null; then
  if curl -sf -m 10 -H "ngrok-skip-browser-warning: 1" "https://$DOMAIN/api/health" >/dev/null; then
    echo "   OK  public address https://$DOMAIN"
  else
    echo "   --  https://$DOMAIN does not answer yet; see $LOGS/ngrok.log"
  fi
fi

if [ ${#pids[@]} -gt 0 ]; then
  echo "Running. Leave this terminal open during the demo; Ctrl-C stops the API and ngrok."
  wait
fi
