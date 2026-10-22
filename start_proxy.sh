#!/bin/bash
# 用 hermes 自己的 venv Python 启动代理服务
# 这样 AIAgent 可以直接 import，不需要子进程

HERMES_VENV=~/.hermes/hermes-agent/venv/bin/python3
PROXY_DIR=~/qqbot/proxy

echo "正在安装依赖..."
$HERMES_VENV -m pip install -q fastapi uvicorn

echo "启动代理服务（端口 8766）..."
cd $PROXY_DIR
$HERMES_VENV -m uvicorn main:app --host 0.0.0.0 --port 8766
