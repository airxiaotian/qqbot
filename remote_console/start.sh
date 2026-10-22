#!/bin/sh
# 只在 Tailscale 上启动。没有 100.64.0.0/10 地址、或允许名单是空的，就退出。
cd "$(dirname "$0")"
if [ -x .venv/Scripts/python.exe ]; then
  exec .venv/Scripts/python.exe server.py "$@"
fi
if [ -x .venv/bin/python ]; then
  exec .venv/bin/python server.py "$@"
fi
python3 -m venv .venv
if [ -x .venv/Scripts/python.exe ]; then
  .venv/Scripts/python.exe -m pip install -r requirements.txt
  exec .venv/Scripts/python.exe server.py "$@"
fi
.venv/bin/pip install -r requirements.txt
exec .venv/bin/python server.py "$@"
