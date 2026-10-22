#!/bin/bash
# 停止本机代理，并禁止 keepalive_proxy.sh 再次拉起

PORT=8766
PROXY_DIR="${QQBOT_PROXY_DIR:-$HOME/qqbot/proxy}"
DISABLED="$PROXY_DIR/.keepalive_disabled"

mkdir -p "$PROXY_DIR"
touch "$DISABLED"
echo "已禁用保活（$DISABLED）。再次 start_proxy.sh 会清除此标志。"

if ! command -v lsof >/dev/null 2>&1; then
  echo "未找到 lsof，无法按端口停止代理。"
  exit 1
fi

PIDS=$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | sort -u)
if [ -z "$PIDS" ]; then
  echo "端口 $PORT 上没有代理在运行。"
  exit 0
fi

echo "正在停止端口 $PORT 上的进程: $PIDS"
for pid in $PIDS; do
  kill "$pid" 2>/dev/null || true
done

for _ in 1 2 3 4 5; do
  sleep 1
  if ! lsof -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "代理已停止。"
    exit 0
  fi
done

echo "进程未退出，强制结束..."
lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | sort -u | while read -r pid; do
  kill -9 "$pid" 2>/dev/null || true
done

if lsof -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "仍有进程占用端口 $PORT，请手动检查。"
  exit 1
fi

echo "代理已停止。"
