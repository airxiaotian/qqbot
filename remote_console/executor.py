"""只在本机回环地址接收已经确认的任务，交给 Cursor Agent SDK。"""

import json
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, "/Users/tciuser1/qqbot/astrbot/data/plugins/astrbot_plugin_oneesan")
from tools import verify_request  # noqa: E402

from server import _jobs, _jobs_lock, api_key, redact, start_job  # noqa: E402

HOST = "127.0.0.1"
PORT = 8792
SECRET_PATH = Path("/Users/tciuser1/qqbot/astrbot/data/plugin_data/astrbot_plugin_oneesan/executor_secret")
BACKUP_NOTE = """修改项目文件之前，先把将要改动的文件复制到
/Users/tciuser1/qqbot/astrbot/data/plugin_data/astrbot_plugin_oneesan/backups/ 下带时间的目录。
不要改群聊开关、语音开关，不要打印或提交密钥。
"""

_seen: set[str] = set()


def ensure_secret(path: Path = SECRET_PATH) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        path.write_text(secrets.token_hex(32), encoding="utf-8")
        path.chmod(0o600)
    return path.read_text(encoding="utf-8").strip()


def wrapped_instruction(instruction: str) -> str:
    return BACKUP_NOTE + "\n用户确认过的任务：\n" + instruction.strip()


class Handler(BaseHTTPRequestHandler):
    server_version = "qqbot-executor"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[executor] {fmt % args}")

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0 or length > 64_000:
            return b""
        return self.rfile.read(length) if length else b""

    def _authorized(self, body: bytes) -> bool:
        reason = verify_request(
            ensure_secret(),
            self.headers.get("X-Timestamp", ""),
            self.headers.get("X-Nonce", ""),
            body,
            self.headers.get("X-Signature", ""),
            time.time(),
            _seen,
        )
        if reason:
            self._send(401, {"ok": False, "error": reason})
            return False
        return True

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/internal/health":
            self._send(200, {"ok": True, "configured": bool(api_key())})
            return
        if not path.startswith("/internal/jobs/"):
            self._send(404, {"ok": False, "error": "没有这个任务"})
            return
        if not self._authorized(b""):
            return
        job_id = path.rsplit("/", 1)[-1]
        with _jobs_lock:
            found = next((job for job in _jobs if job["id"] == job_id), None)
        if found is None:
            self._send(404, {"ok": False, "error": "没有这个任务"})
            return
        self._send(200, {"ok": True, "id": found["id"], "status": found["status"], "result": redact(found.get("result") or "")})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/internal/jobs":
            self._send(404, {"ok": False, "error": "没有这个操作"})
            return
        body = self._read_body()
        if not self._authorized(body):
            return
        try:
            payload = json.loads(body.decode("utf-8"))
        except ValueError:
            self._send(400, {"ok": False, "error": "请求体不是 JSON"})
            return
        instruction = str(payload.get("instruction") or "").strip()
        if not instruction:
            self._send(400, {"ok": False, "error": "没有任务内容"})
            return
        if not api_key():
            self._send(503, {"ok": False, "error": "执行器未配置"})
            return
        job = start_job(wrapped_instruction(instruction))
        if job is None:
            self._send(409, {"ok": False, "error": "上一条还在执行"})
            return
        self._send(202, {"ok": True, "id": job["id"], "status": job["status"]})


def main() -> None:
    ensure_secret()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"执行器监听 http://{HOST}:{PORT} ，只接受本机签名请求")
    server.serve_forever()


if __name__ == "__main__":
    main()
