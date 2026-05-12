#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"

if [ ! -d ".venv" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

if ! .venv/bin/python -c 'import textual, rich, httpx' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r requirements.txt
fi

APIKEY_FILE=".apikey"

if [ -z "${ZHIPUAI_API_KEY:-}" ] && [ -f "$APIKEY_FILE" ]; then
  api_key="$(sed -n 's/^[[:space:]]*//; s/[[:space:]]*$//; /^#/d; /^$/d; 1p' "$APIKEY_FILE")"
  api_key="${api_key#ZHIPUAI_API_KEY=}"
  api_key="${api_key%\"}"
  api_key="${api_key#\"}"
  api_key="${api_key%\'}"
  api_key="${api_key#\'}"
  export ZHIPUAI_API_KEY="$api_key"
fi

if [ -z "${ZHIPUAI_API_KEY:-}" ]; then
  {
    echo "Missing ZHIPUAI_API_KEY."
    echo "Please register or sign in to Zhipu AI, create an API key, then put it in .apikey:"
    echo "通过我的邀请链接注册即可获得 2000万Tokens 大礼包；链接：https://www.bigmodel.cn/invite?icode=6abHw86%2Fo%2BSqRnDdQ%2B675eZLO2QH3C0EBTSr%2BArzMw4%3D"
    echo ""
    echo "Example .apikey:"
    echo "  ZHIPUAI_API_KEY=your_api_key_here"
    echo ""
    echo "You can also export it before running:"
    echo "  export ZHIPUAI_API_KEY=your_api_key_here"
  } >&2
  exit 1
fi

.venv/bin/python main.py
