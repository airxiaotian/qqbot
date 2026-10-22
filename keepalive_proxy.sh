#!/bin/bash
# 保活脚本，每分钟检查一次代理是否在运行，不在就重启
PROXY_DIR=~/qqbot/proxy
VENV="$PROXY_DIR/.venv/bin/python3"
LOG=$PROXY_DIR/../proxy.log
PORT=8766

if [ ! -x "$VENV" ]; then
  python3 -m venv "$PROXY_DIR/.venv"
  "$VENV" -m pip install -q -r "$PROXY_DIR/requirements.txt"
fi

# 检查端口是否有进程在监听
if ! /usr/sbin/lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 代理未运行，启动中..." >> "$LOG"
    cd "$PROXY_DIR"
    nohup "$VENV" -m uvicorn main:app --host 0.0.0.0 --port 8766 >> "$LOG" 2>&1 &
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 代理已启动 (PID $!)" >> "$LOG"
fi
