#!/bin/bash
# 用 Hermes 自带的 venv Python 启动代理，可在 QQBOT_USE_HERMES 开关控制处理链路。

export QQBOT_USE_HERMES="${QQBOT_USE_HERMES:-true}"
HERMES_VENV=~/.hermes/hermes-agent/venv/bin/python3
PROXY_DIR=~/qqbot/proxy

rm -f "$PROXY_DIR/.keepalive_disabled"

echo "正在安装依赖..."
"$HERMES_VENV" -m pip install -q fastapi uvicorn

echo "启动代理服务（端口 8765，QQBOT_USE_HERMES=${QQBOT_USE_HERMES}）..."
cd "$PROXY_DIR"
"$HERMES_VENV" -m uvicorn main:app --host 0.0.0.0 --port 8765
