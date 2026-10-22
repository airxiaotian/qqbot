#!/bin/bash
# 用项目自带 venv 启动代理（直连 GitHub Copilot，不依赖 Hermes）

PROXY_DIR=~/qqbot/proxy
VENV="$PROXY_DIR/.venv/bin/python3"

rm -f "$PROXY_DIR/.keepalive_disabled"

if [ ! -x "$VENV" ]; then
  echo "创建 proxy/.venv ..."
  python3 -m venv "$PROXY_DIR/.venv"
fi

echo "正在安装依赖..."
"$VENV" -m pip install -q -r "$PROXY_DIR/requirements.txt"

echo "启动代理服务（端口 8766）..."
cd "$PROXY_DIR"
"$VENV" -m uvicorn main:app --host 0.0.0.0 --port 8766
