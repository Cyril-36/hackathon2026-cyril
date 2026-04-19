#!/usr/bin/env bash
# Launch the ShopWave Support Console web UI against the real agent.
#
#   ./scripts/demo_web.sh            # default port 8787 → http://127.0.0.1:8787/ui/
#   ./scripts/demo_web.sh 9000       # custom port
#   ./scripts/demo_web.sh 9000 --    # skip port-check (advanced; uvicorn will error out if busy)
#
# If the requested port is already bound (e.g. the server is still running
# from a previous invocation), we report the offending process and exit
# non-zero with an actionable hint instead of dumping a stack trace.
#
# The CLI submission path (`./scripts/demo.sh`, `run.py`, the verifier) is
# unaffected by this script — the web layer is additive.
set -euo pipefail

cd "$(dirname "$0")/.."

PORT="${1:-8787}"
SKIP_CHECK="${2:-}"

if [[ ! -d .venv ]]; then
  echo "[demo_web] Creating .venv…"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

python -c "import fastapi, uvicorn" 2>/dev/null || {
  echo "[demo_web] Installing web-UI dependencies…"
  pip install -q -r requirements.txt
}

# Pre-flight: refuse to stomp on an already-bound port.
if [[ -z "$SKIP_CHECK" ]]; then
  if command -v lsof >/dev/null 2>&1; then
    if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "[demo_web] port ${PORT} is already in use:" >&2
      lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >&2 || true
      cat >&2 <<EOF

[demo_web] Looks like a server is already running. Options:
  1. Open the existing instance:  http://127.0.0.1:${PORT}/ui/
  2. Stop it, then rerun:         kill \$(lsof -ti:${PORT})
  3. Use a different port:        ./scripts/demo_web.sh 9000
EOF
      exit 1
    fi
  elif command -v nc >/dev/null 2>&1; then
    if nc -z 127.0.0.1 "$PORT" 2>/dev/null; then
      echo "[demo_web] port ${PORT} is already in use. Try: ./scripts/demo_web.sh $((PORT+1))" >&2
      exit 1
    fi
  fi
fi

echo "[demo_web] Serving console on http://127.0.0.1:${PORT}/ui/"
exec uvicorn app.server:app --host 127.0.0.1 --port "$PORT" --reload
