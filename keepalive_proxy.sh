#!/bin/bash
# 保活脚本，每分钟检查一次代理是否在运行，不在就重启
HERMES_VENV=~/.hermes/hermes-agent/venv/bin/python3
PROXY_DIR=~/qqbot/proxy
LOG=$PROXY_DIR/../proxy.log
PORT=8766

# 检查端口是否有进程在监听
if ! /usr/sbin/lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 代理未运行，启动中..." >> "$LOG"
    cd "$PROXY_DIR"
    nohup $HERMES_VENV main.py >> "$LOG" 2>&1 &
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 代理已启动 (PID $!)" >> "$LOG"
fi
