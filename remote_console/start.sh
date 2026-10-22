#!/bin/sh
# 只在 Tailscale 上启动。没有 100.64.0.0/10 地址、或允许名单是空的，就退出。
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi
exec .venv/bin/python server.py
