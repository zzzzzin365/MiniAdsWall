#!/bin/bash
DEMO_ROOT="$(cd "$(dirname "$0")" && pwd)"
"$DEMO_ROOT/demo.sh" start
DEMO_RESULT=$?
if [ "$DEMO_RESULT" -ne 0 ]; then
  read -r -p '启动失败。按回车关闭窗口。'
fi
exit "$DEMO_RESULT"
