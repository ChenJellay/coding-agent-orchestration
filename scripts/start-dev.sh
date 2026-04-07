#!/usr/bin/env bash
# =============================================================================
# Agenti-Helix — Local Development Launcher
#
# Starts:
#   1. Judge service      → http://127.0.0.1:8000  (MLX local inference)
#   2. Control-plane API  → http://127.0.0.1:8001  (FastAPI)
#   3. Frontend dev server → http://localhost:5173  (Vite)
#
# Usage:
#   ./scripts/start-dev.sh [--repo /path/to/repo]
#
# Prerequisites:
#   - Python 3.11+ with dependencies installed:
#       cd backend && pip install -r ../requirements.txt
#   - Node.js 20+ with dependencies installed:
#       cd frontend && npm install
#   - (Optional) QWEN_MODEL_PATH or OPENAI_API_KEY set for inference
# =============================================================================
set -euo pipefail
# Give each background job its own process group so Ctrl+C reaches this shell
# while we can still tear down every service via `kill -- -$pgid`.
set -m

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEMO_REPO="${REPO_ROOT}/demo-repo"

# Allow --repo override.
TARGET_REPO="${AGENTI_HELIX_REPO_ROOT:-${DEMO_REPO}}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      TARGET_REPO="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

export AGENTI_HELIX_REPO_ROOT="${TARGET_REPO}"

echo ""
echo "=========================================="
echo "  Agenti-Helix — Local Dev"
echo "  Repo: ${AGENTI_HELIX_REPO_ROOT}"
echo "=========================================="
echo ""

# Load backend env files if present (.env.local overrides .env when loaded second).
for _envfile in "${REPO_ROOT}/backend/.env" "${REPO_ROOT}/backend/.env.local"; do
  if [[ -f "${_envfile}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${_envfile}"
    set +a
    echo "[env] Loaded ${_envfile#"${REPO_ROOT}/"}"
  fi
done

PIDS=()

cleanup() {
  # Avoid re-entrancy / duplicate EXIT after INT.
  trap - EXIT INT TERM

  echo ""
  echo "[start-dev] Shutting down all services..."

  local pid
  # Kill newest-first (UI, API, judge) after stacking PIDS in start order.
  for (( idx=${#PIDS[@]}-1; idx>=0; idx-- )); do
    pid="${PIDS[idx]}"
    if kill -0 "${pid}" 2>/dev/null; then
      # Negative PID = process group of job leader (see `set -m` above).
      kill -TERM -- "-${pid}" 2>/dev/null || kill -TERM -- "${pid}" 2>/dev/null || true
    fi
  done

  local _deadline=$((SECONDS + 8))
  while (( SECONDS < _deadline )); do
    local _any_alive=0
    for pid in "${PIDS[@]}"; do
      kill -0 "${pid}" 2>/dev/null && _any_alive=1 && break
    done
    ((_any_alive)) || break
    sleep 0.25
  done

  for (( idx=${#PIDS[@]}-1; idx>=0; idx-- )); do
    pid="${PIDS[idx]}"
    kill -KILL -- "-${pid}" 2>/dev/null || kill -KILL -- "${pid}" 2>/dev/null || true
  done

  wait 2>/dev/null || true
  echo "[start-dev] All services stopped."
}
trap cleanup EXIT INT TERM

# 1. Judge service (port 8000, localhost-only).
echo "[judge] Starting judge service on http://127.0.0.1:8000 ..."
(
  cd "${REPO_ROOT}/backend"
  python -m uvicorn agenti_helix.verification.judge_server:app \
    --host 127.0.0.1 --port 8000 --reload --log-level warning
) &
PIDS+=($!)
echo "[judge] PID=${PIDS[$(( ${#PIDS[@]} - 1 ))]} (pgid for teardown)"

# Wait briefly for judge to bind.
sleep 1

# 2. Control-plane API (port 8001).
echo "[api] Starting control-plane API on http://127.0.0.1:8001 ..."
(
  cd "${REPO_ROOT}/backend"
  python -m uvicorn agenti_helix.api.main:app \
    --host 127.0.0.1 --port 8001 --reload --log-level info
) &
PIDS+=($!)
echo "[api] PID=${PIDS[$(( ${#PIDS[@]} - 1 ))]} (pgid for teardown)"

# 3. Frontend (port 5173).
echo "[ui] Starting frontend dev server on http://localhost:5173 ..."
(
  cd "${REPO_ROOT}/frontend"
  # Load frontend .env.local if present.
  if [[ -f ".env.local" ]]; then
    echo "[ui] Loaded frontend/.env.local"
  fi
  npm run dev -- --host 127.0.0.1
) &
PIDS+=($!)
echo "[ui] PID=${PIDS[$(( ${#PIDS[@]} - 1 ))]} (pgid for teardown)"

echo ""
echo "All services started. Press Ctrl+C to stop."
echo ""
echo "  Dashboard:  http://localhost:5173"
echo "  API health: http://127.0.0.1:8001/api/health"
echo "  Judge:      http://127.0.0.1:8000/docs"
echo ""

wait
