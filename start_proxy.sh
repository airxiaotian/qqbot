#!/bin/bash
# 用 Hermes 自带的 venv Python 启动代理（Copilot 认证走 Hermes）

HERMES_VENV=~/.hermes/hermes-agent/venv/bin/python3
PROXY_DIR=~/qqbot/proxy

rm -f "$PROXY_DIR/.keepalive_disabled"

echo "正在安装依赖..."
"$HERMES_VENV" -m pip install -q fastapi uvicorn

echo "启动代理服务（端口 8766）..."
cd "$PROXY_DIR"
"$HERMES_VENV" -m uvicorn main:app --host 0.0.0.0 --port 8766
