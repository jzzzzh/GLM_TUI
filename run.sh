#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"

if [ ! -d ".venv" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

if ! .venv/bin/python -c 'import textual, rich, httpx' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r requirements.txt
fi

export ZHIPUAI_API_KEY=""

.venv/bin/python main.py
