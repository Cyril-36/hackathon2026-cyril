#!/usr/bin/env bash
# One-command demo: create venv, install deps, run the full 20-ticket suite.
# Subsequent runs skip the install step.
#
#   ./scripts/demo.sh                       # clean rules-mode run, chaos=0
#   ./scripts/demo.sh --chaos 0.15          # chaos-injected run, archived to runs/
#   ./scripts/demo.sh --mode hybrid         # requires GROQ_API_KEY in .env
#
# The clean audit_log.json is the committed artifact; chaos reruns archive to
# runs/<run_id>.json so the reviewer-facing log is never silently overwritten.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"
PY="${PYTHON:-python3}"

if [[ ! -d "$VENV" ]]; then
  echo "[demo] bootstrapping venv in $VENV"
  "$PY" -m venv "$VENV"
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt
else
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
fi

# Default args: clean deterministic run. Any user args append/override.
DEFAULT_ARGS=(--mode rules --chaos 0 --seed 42)
USER_ARGS=("$@")

# If user passed --chaos with a non-zero value, archive so the clean log survives.
# --chaos 0 (or 0.0, or 0.00 …) is a clean run — keep writing to audit_log.json.
ARCHIVE_FLAG=""
for ((i=0; i<${#USER_ARGS[@]}; i++)); do
  arg="${USER_ARGS[$i]}"
  val=""
  if [[ "$arg" == "--chaos" ]]; then
    val="${USER_ARGS[$((i+1))]:-}"
  elif [[ "$arg" == --chaos=* ]]; then
    val="${arg#--chaos=}"
  else
    continue
  fi
  # Non-zero float? awk exits 0 when the numeric value is not zero.
  if [[ -n "$val" ]] && awk -v v="$val" 'BEGIN{exit !(v+0 != 0)}'; then
    ARCHIVE_FLAG="--archive"
  fi
done

echo "[demo] python run.py ${DEFAULT_ARGS[*]} ${USER_ARGS[*]:-} $ARCHIVE_FLAG"
python run.py "${DEFAULT_ARGS[@]}" ${USER_ARGS[@]:+"${USER_ARGS[@]}"} ${ARCHIVE_FLAG:-}
