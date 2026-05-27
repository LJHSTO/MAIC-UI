#!/usr/bin/env bash
set -euo pipefail

case "${BASH_SOURCE[0]}" in
  */*) SCRIPT_DIR="${BASH_SOURCE[0]%/*}" ;;
  *) SCRIPT_DIR="." ;;
esac
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PORT="${MAIC_PORT:-8000}"

cd "$ROOT_DIR/backend"

echo "Starting MAIC-UI backend on 0.0.0.0:${PORT}"
echo "Other machines on the same network can use: http://YOUR_LAN_IP:${PORT}/api"

uvicorn main:app --host 0.0.0.0 --port "$PORT" --reload
