#!/bin/sh
cd "$(dirname "$0")/remote_console"
if [ -f ./start.sh ]; then
  exec ./start.sh "$@"
fi
if command -v python3 >/dev/null 2>&1; then
  exec python3 server.py "$@"
fi
if command -v python >/dev/null 2>&1; then
  exec python server.py "$@"
fi
echo "没有可用的 Python 解释器，无法启动 remote_console" >&2
exit 1
