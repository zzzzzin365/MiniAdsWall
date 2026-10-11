#!/bin/bash
set -euo pipefail
DEMO_ROOT="$(cd "$(dirname "$0")" && pwd)"
if [ ! -x "$DEMO_ROOT/.venv/bin/python" ]; then
  echo '缺少项目 Python 环境 .venv，请先安装 requirements.txt。' >&2
  exit 1
fi
exec "$DEMO_ROOT/.venv/bin/python" "$DEMO_ROOT/scripts/demo.py" "$@"
