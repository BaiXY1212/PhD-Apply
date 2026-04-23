#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

APP_FILE="${APP_FILE:-app.py}"
APP_HOST="${APP_HOST:-0.0.0.0}"
APP_PORT="${APP_PORT:-8501}"
CONDA_ENV_NAME="${CONDA_ENV_NAME:-phdhub}"
USE_CONDA="${USE_CONDA:-1}"

if [[ "${1:-}" == "--no-conda" ]]; then
  USE_CONDA=0
  shift
fi

if [[ ! -f "$APP_FILE" ]]; then
  echo "[ERROR] App entry not found: $APP_FILE" >&2
  exit 1
fi

if [[ "$USE_CONDA" == "1" ]] && command -v conda >/dev/null 2>&1; then
  CONDA_BASE="$(conda info --base)"
  # shellcheck source=/dev/null
  source "$CONDA_BASE/etc/profile.d/conda.sh"
  conda activate "$CONDA_ENV_NAME"
fi

exec streamlit run "$APP_FILE" --server.address "$APP_HOST" --server.port "$APP_PORT" "$@"
