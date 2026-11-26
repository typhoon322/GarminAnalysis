#!/usr/bin/env bash
# Start the local report on 127.0.0.1. Creates .venv on first run.
set -euo pipefail
cd "$(dirname "$0")"

python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
  || { echo "需要 Python 3.11 或更高版本。"; exit 1; }

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi

if ! .venv/bin/python -c "import fastapi, uvicorn, plotly, httpx" >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r requirements.txt
fi

PORT_VALUE="${PORT:-}"
if [[ -z "$PORT_VALUE" && -f .env ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      ""|\#*) continue ;;
      PORT=*) PORT_VALUE="${line#PORT=}" ;;
    esac
  done < .env
  PORT_VALUE="${PORT_VALUE%\"}"
  PORT_VALUE="${PORT_VALUE#\"}"
  PORT_VALUE="${PORT_VALUE%\'}"
  PORT_VALUE="${PORT_VALUE#\'}"
fi
PORT_VALUE="${PORT_VALUE:-8765}"

echo "本机页面: http://127.0.0.1:${PORT_VALUE}"
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT_VALUE}"
