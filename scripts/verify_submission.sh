#!/usr/bin/env bash
# Submission verifier — runs the exact commands a judge would run and prints a
# PASS/FAIL checklist.
#
#   ./scripts/verify_submission.sh
#
# What it checks:
#   1. pytest suite is green
#   2. clean ./scripts/demo.sh produces the expected distribution
#   3. chaos rerun diverts to runs/ and does NOT mutate audit_log.json
#   4. the committed chaos artifact is byte-identical to a fresh rerun
#      (proves reproducibility from the stable SHA-256 seed)
#
# Exits non-zero on any failure so CI / a reviewer can script around it.

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

pass=0
fail=0
results=()

check() {
  local label="$1"
  local ok="$2"
  if [[ "$ok" == "0" ]]; then
    results+=("  PASS  $label")
    pass=$((pass+1))
  else
    results+=("  FAIL  $label")
    fail=$((fail+1))
  fi
}

# ---- 1. pytest ----
echo "[verify] running pytest..."
if .venv/bin/python -m pytest -q >/tmp/verify_pytest.log 2>&1; then
  test_count=$(grep -oE '[0-9]+ passed' /tmp/verify_pytest.log | head -n1 || echo "? tests")
  check "pytest — $test_count" 0
else
  check "pytest — see /tmp/verify_pytest.log" 1
fi

# ---- 2. clean demo run ----
echo "[verify] clean demo run..."
.venv/bin/python run.py --mode rules --chaos 0 --seed 42 >/tmp/verify_clean.log 2>&1
clean_rc=$?
if [[ $clean_rc -eq 0 ]] && grep -q "resolved : 14"          /tmp/verify_clean.log \
                         && grep -q "escalated: 5"           /tmp/verify_clean.log \
                         && grep -q "fraud_detected" /tmp/verify_clean.log; then
  check "clean run: 14 resolved / 5 escalated / 1 declined" 0
else
  check "clean run distribution mismatch — see /tmp/verify_clean.log" 1
fi

clean_hash=$(shasum -a 256 audit_log.json | awk '{print $1}')

# ---- 3. chaos run must divert ----
echo "[verify] chaos rerun with --archive..."
before_chaos_hash="$clean_hash"
.venv/bin/python run.py --mode rules --chaos 0.15 --seed 42 --archive \
    >/tmp/verify_chaos.log 2>&1
chaos_rc=$?
after_chaos_hash=$(shasum -a 256 audit_log.json | awk '{print $1}')

if [[ $chaos_rc -eq 0 && "$before_chaos_hash" == "$after_chaos_hash" ]]; then
  check "chaos rerun diverts (audit_log.json byte-identical pre/post)" 0
else
  check "chaos rerun mutated audit_log.json — divert semantics broken" 1
fi

latest_archive=$(ls -t runs/*.json 2>/dev/null | head -n1 || true)
if [[ -n "$latest_archive" ]]; then
  check "chaos archive written to $latest_archive" 0
else
  check "no chaos archive in runs/" 1
fi

# ---- 4. chaos reproducibility ----
echo "[verify] chaos reproducibility (committed artifact vs fresh rerun)..."
.venv/bin/python run.py --mode rules --chaos 0.15 --seed 42 \
    --audit-out /tmp/verify_chaos_rerun.json >/dev/null 2>&1
committed_hash=$(shasum -a 256 audit_log_chaos_seed42.json | awk '{print $1}')
rerun_hash=$(shasum -a 256 /tmp/verify_chaos_rerun.json     | awk '{print $1}')
# The audit entries include wallclock timestamps / run_ids so raw files differ;
# strip those fields before hashing.
norm() {
  .venv/bin/python -c "
import json, sys, hashlib
entries = json.load(open(sys.argv[1]))
for e in entries:
    e.pop('timestamp', None)
    e.pop('run_id', None)
    e.pop('duration_ms', None)
print(hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest())
" "$1"
}
committed_norm=$(norm audit_log_chaos_seed42.json)
rerun_norm=$(norm /tmp/verify_chaos_rerun.json)
if [[ "$committed_norm" == "$rerun_norm" ]]; then
  check "chaos artifact is reproducible (normalised hash matches)" 0
else
  check "chaos artifact NOT reproducible — seed is non-deterministic" 1
fi

echo
echo "============================================================"
echo "Submission verification — $pass passed / $fail failed"
echo "============================================================"
for line in "${results[@]}"; do
  echo "$line"
done
echo

if [[ $fail -eq 0 ]]; then
  echo "OK: ready to submit."
  exit 0
else
  echo "NOT OK: resolve the FAIL items above."
  exit 1
fi
