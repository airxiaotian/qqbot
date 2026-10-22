"""通过 Tailscale 接收修改指示，核对设备后再交给本机 Cursor Agent。"""

import hashlib
import hmac
import ipaddress
import sys
import json
import re
import secrets
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

PROJECT = Path("/Users/tciuser1/qqbot").resolve()
HOME_DIR = Path.home() / ".qqbot-remote"
CONFIG_PATH = HOME_DIR / "config.json"
AUTH_PATH = HOME_DIR / "auth.json"
JOBS_PATH = HOME_DIR / "jobs.jsonl"
KEY_PATH = HOME_DIR / "cursor_api_key"
PORT = 8791
MAX_BODY = 64_000
MAX_INSTRUCTION = 8_000
SESSION_SECONDS = 12 * 3600
LOCK_SECONDS = 600
MAX_FAILURES = 8

GUARD = """你在本机修改 /Users/tciuser1/qqbot。只改完成下面指示所必需的文件。
禁止读取、打印或提交密钥、令牌、cookie、密码、.env、auth.json、cursor_api_key。
禁止修改 /Users/tciuser1/.qqbot-remote。
禁止删除项目目录以外的文件，禁止修改系统服务、launchd、crontab、Tailscale 或防火墙。
禁止把任何服务改成监听 0.0.0.0 或公网。
改完后运行和改动相关的检查，最后用简短中文说明改了什么、怎么验证的。
用户指示：
"""

_SECRET_RE = re.compile(
    r"(cursor_[A-Za-z0-9_\-]{8,}|crsr_[A-Za-z0-9_\-]{8,}|sk-[A-Za-z0-9]{10,}|github_pat_[A-Za-z0-9_]+)"
)

_jobs_lock = threading.Lock()
_jobs: list[dict] = []
_running = False
_failures: dict[str, list[float]] = {}


def redact(text: str) -> str:
    return _SECRET_RE.sub("[已隐藏]", text or "")


def load_config() -> dict:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {
        "port": int(data.get("port") or PORT),
        "allowed_hostnames": [str(item) for item in data.get("allowed_hostnames") or []],
        "allowed_node_ids": [str(item) for item in data.get("allowed_node_ids") or []],
    }


def save_config(config: dict) -> None:
    HOME_DIR.mkdir(mode=0o700, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    CONFIG_PATH.chmod(0o600)


def hash_password(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1).hex()


def load_auth() -> dict:
    return json.loads(AUTH_PATH.read_text(encoding="utf-8"))


def init_files() -> str | None:
    """创建配置和密码。新生成密码时返回明文，已存在则返回 None。"""
    HOME_DIR.mkdir(mode=0o700, exist_ok=True)
    if not CONFIG_PATH.exists():
        save_config({"port": PORT, "allowed_hostnames": [], "allowed_node_ids": []})
    if AUTH_PATH.exists():
        return None
    password = secrets.token_urlsafe(18)
    salt = secrets.token_bytes(16)
    auth = {
        "salt": salt.hex(),
        "password_hash": hash_password(password, salt),
        "session_secret": secrets.token_hex(32),
    }
    AUTH_PATH.write_text(json.dumps(auth), encoding="utf-8")
    AUTH_PATH.chmod(0o600)
    return password


def password_matches(password: str) -> bool:
    auth = load_auth()
    salt = bytes.fromhex(auth["salt"])
    expected = auth["password_hash"]
    return hmac.compare_digest(hash_password(password, salt), expected)


def sign_session(expiry: int) -> str:
    secret = bytes.fromhex(load_auth()["session_secret"])
    message = str(expiry).encode()
    digest = hmac.new(secret, message, hashlib.sha256).hexdigest()
    return f"{expiry}.{digest}"


def session_valid(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    expiry_text, digest = token.split(".", 1)
    try:
        expiry = int(expiry_text)
    except ValueError:
        return False
    if expiry < int(time.time()):
        return False
    secret = bytes.fromhex(load_auth()["session_secret"])
    expected = hmac.new(secret, expiry_text.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, expected)


def device_allowed(info: dict, config: dict) -> bool:
    allowed_ids = set(config["allowed_node_ids"])
    if info.get("id") and info["id"] in allowed_ids:
        return True
    allowed_names = {name.lower() for name in config["allowed_hostnames"]}
    names = set()
    for value in (info.get("hostname"), info.get("dns")):
        if not value:
            continue
        names.add(value.lower())
        names.add(value.split(".")[0].lower())
    names.discard("")
    return bool(names & allowed_names)


def tailscale_bin() -> str:
    candidates = [
        shutil.which("tailscale"),
        "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
        "/opt/homebrew/bin/tailscale",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise FileNotFoundError("未安装 Tailscale")


def is_tailscale_ipv4(address: str) -> bool:
    try:
        return ipaddress.ip_address(address) in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        return False


def tailscale_ipv4(binary: str) -> str:
    output = subprocess.check_output([binary, "ip", "-4"], text=True, timeout=5)
    address = output.strip().splitlines()[0].strip()
    if not is_tailscale_ipv4(address):
        raise RuntimeError(f"拒绝监听 {address}：只允许 Tailscale 的 100.64.0.0/10 地址")
    return address


def whois(binary: str, address: str) -> dict | None:
    if address.startswith("::ffff:"):
        address = address.removeprefix("::ffff:")
    try:
        output = subprocess.check_output(
            [binary, "whois", "--json", address],
            text=True,
            timeout=5,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    try:
        data = json.loads(output)
    except ValueError:
        return None
    node = data.get("Node") or data.get("node") or {}
    user = data.get("User") or data.get("user") or {}
    info = {
        "id": str(node.get("ID") or node.get("Id") or ""),
        "hostname": str(node.get("HostName") or node.get("Hostname") or ""),
        "dns": str(node.get("DNSName") or node.get("Name") or ""),
    }
    if not info["hostname"] and not info["id"]:
        return None
    info["login"] = str(user.get("LoginName") or "")
    return info


def locked(device_id: str) -> bool:
    now = time.time()
    recent = [stamp for stamp in _failures.get(device_id, []) if now - stamp < LOCK_SECONDS]
    _failures[device_id] = recent
    return len(recent) >= MAX_FAILURES


def record_failure(device_id: str) -> None:
    _failures.setdefault(device_id, []).append(time.time())


def clear_failures(device_id: str) -> None:
    _failures.pop(device_id, None)


def api_key() -> str:
    if KEY_PATH.is_file():
        return KEY_PATH.read_text(encoding="utf-8").strip()
    return ""


def execute(instruction: str) -> dict:
    key = api_key()
    if not key:
        return {
            "status": "error",
            "result": "没有 Cursor API key。请把 key 单独放进 ~/.qqbot-remote/cursor_api_key。",
        }
    from cursor_sdk import Agent, AgentOptions, CursorAgentError, LocalAgentOptions

    try:
        result = Agent.prompt(
            GUARD + instruction,
            AgentOptions(
                api_key=key,
                model="composer-2.5",
                local=LocalAgentOptions(
                    cwd=str(PROJECT),
                    setting_sources=["project"],
                    auto_review=False,
                ),
            ),
        )
    except CursorAgentError as error:
        return {"status": "startup_error", "result": redact(str(error))}
    return {
        "status": getattr(result, "status", "finished"),
        "result": redact(str(getattr(result, "result", "") or "")),
    }


def append_log(job: dict) -> None:
    with JOBS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(job, ensure_ascii=False) + "\n")


def start_job(instruction: str) -> dict | None:
    global _running
    job = {
        "id": secrets.token_hex(8),
        "instruction": instruction,
        "status": "running",
        "result": "",
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with _jobs_lock:
        if _running:
            return None
        _running = True
        _jobs.append(job)

    def worker() -> None:
        global _running
        try:
            outcome = execute(instruction)
            job.update(outcome)
        except Exception as error:
            job["status"] = "error"
            job["result"] = redact(str(error))
        finally:
            with _jobs_lock:
                _running = False
            append_log({key: job[key] for key in ("id", "instruction", "status", "result", "created")})

    threading.Thread(target=worker, daemon=False).start()
    return job


PAGE = """<!doctype html>
<html lang="zh">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>项目远程修改</title>
<style>
  body { margin: 0; font: 16px/1.5 sans-serif; background: #f4f1ea; color: #221e18; }
  main { max-width: 40rem; margin: 0 auto; padding: 1.5rem; }
  h1 { font-size: 1.4rem; }
  textarea, input, button { font: inherit; }
  textarea, input { width: 100%; box-sizing: border-box; padding: .7rem; border: 1px solid #c8bfb0; }
  textarea { min-height: 9rem; }
  button { margin-top: .8rem; padding: .7rem 1rem; background: #3d3428; color: white; border: 0; }
  pre { white-space: pre-wrap; background: white; padding: 1rem; border: 1px solid #ddd4c6; }
  .muted { color: #665c50; }
</style>
<main>
  <h1>修改 qqbot</h1>
  <p class="muted">只接受 Tailscale 允许名单里的设备。提交后会自动修改本项目。</p>
  <form id="login">
    <input id="password" type="password" placeholder="网页密码" autocomplete="current-password">
    <button>进入</button>
  </form>
  <form id="work" hidden>
    <textarea id="instruction" placeholder="要改什么"></textarea>
    <button>执行</button>
    <p id="state" class="muted"></p>
    <pre id="output"></pre>
  </form>
</main>
<script>
const login = document.querySelector("#login");
const work = document.querySelector("#work");
const state = document.querySelector("#state");
const output = document.querySelector("#output");
const headers = {"Content-Type": "application/json", "X-Requested-With": "qqbot-remote"};
async function refresh() {
  const response = await fetch("/api/session");
  const signedIn = response.ok;
  login.hidden = signedIn;
  work.hidden = !signedIn;
  if (!signedIn) return;
  const jobs = await (await fetch("/api/jobs")).json();
  const current = jobs.find((job) => job.status === "running") || jobs.at(-1);
  if (!current) { state.textContent = "还没有执行记录"; output.textContent = ""; return; }
  state.textContent = current.created + " · " + current.status;
  output.textContent = current.result || current.instruction;
  if (current.status === "running") setTimeout(refresh, 2000);
}
login.onsubmit = async (event) => {
  event.preventDefault();
  const response = await fetch("/api/login", {method: "POST", headers, body: JSON.stringify({password: password.value})});
  if (!response.ok) { alert(await response.text()); return; }
  refresh();
};
work.onsubmit = async (event) => {
  event.preventDefault();
  const response = await fetch("/api/jobs", {method: "POST", headers, body: JSON.stringify({instruction: instruction.value})});
  if (!response.ok) { alert(await response.text()); return; }
  instruction.value = "";
  refresh();
};
refresh();
</script>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "qqbot-remote"
    binary = ""

    def log_message(self, fmt: str, *args) -> None:
        print(f"[remote] {self.address_string()} {fmt % args}")

    def peer(self) -> dict | None:
        return whois(self.binary, self.client_address[0])

    def reject(self, status: int, message: str) -> None:
        body = message.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def authorized_device(self) -> dict | None:
        info = self.peer()
        if info is None:
            self.reject(403, "只能通过 Tailscale 访问")
            return None
        config = load_config()
        if not device_allowed(info, config):
            name = info.get("hostname") or info.get("id")
            self.reject(403, f"设备未授权：{name}")
            return None
        return info

    def cookie(self) -> str | None:
        header = self.headers.get("Cookie", "")
        for part in header.split(";"):
            name, separator, value = part.strip().partition("=")
            if separator and name == "session":
                return value
        return None

    def read_json(self) -> dict | None:
        if self.headers.get("X-Requested-With") != "qqbot-remote":
            self.reject(400, "缺少请求标记")
            return None
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            self.reject(400, "请求体无效")
            return None
        try:
            return json.loads(self.rfile.read(length))
        except ValueError:
            self.reject(400, "请求体不是 JSON")
            return None

    def send_json(self, status: int, payload, cookie: str | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store")
        if cookie is not None:
            self.send_header(
                "Set-Cookie",
                f"session={cookie}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS}",
            )
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.authorized_device() is None:
            return
        path = urlparse(self.path).path
        if path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)
            return
        if not session_valid(self.cookie()):
            self.reject(401, "未登录")
            return
        if path == "/api/session":
            self.send_json(200, {"ok": True})
            return
        if path == "/api/jobs":
            with _jobs_lock:
                public = [{key: job[key] for key in ("id", "status", "result", "created")} for job in _jobs[-20:]]
            self.send_json(200, public)
            return
        self.reject(404, "没有这个页面")

    def do_POST(self) -> None:
        info = self.authorized_device()
        if info is None:
            return
        path = urlparse(self.path).path
        payload = self.read_json()
        if payload is None:
            return
        device_id = info.get("id") or info.get("hostname") or self.client_address[0]
        if path == "/api/login":
            if locked(device_id):
                self.reject(429, "失败次数过多，十分钟后再试")
                return
            if not password_matches(str(payload.get("password") or "")):
                record_failure(device_id)
                self.reject(401, "密码不对")
                return
            clear_failures(device_id)
            self.send_json(200, {"ok": True}, sign_session(int(time.time()) + SESSION_SECONDS))
            return
        if not session_valid(self.cookie()):
            self.reject(401, "未登录")
            return
        if path != "/api/jobs":
            self.reject(404, "没有这个操作")
            return
        instruction = str(payload.get("instruction") or "").strip()
        if not instruction or len(instruction) > MAX_INSTRUCTION:
            self.reject(400, "指示为空或过长")
            return
        job = start_job(instruction)
        if job is None:
            self.reject(409, "上一条还在执行")
            return
        self.send_json(202, {"id": job["id"], "status": job["status"]})


def main() -> None:
    created = init_files()
    if "--init" in sys.argv:
        print("配置目录：", HOME_DIR)
        if created:
            print("网页密码（只显示这一次）：", created)
        else:
            print("密码已经存在，没有重新生成")
        return
    config = load_config()
    if not config["allowed_hostnames"] and not config["allowed_node_ids"]:
        raise SystemExit(
            "还没有指定设备，控制台不会启动。\n"
            "用 tailscale status 看主机名，写进 ~/.qqbot-remote/config.json 的 allowed_hostnames。"
        )
    try:
        binary = tailscale_bin()
        address = tailscale_ipv4(binary)
    except (FileNotFoundError, subprocess.CalledProcessError, RuntimeError, IndexError) as error:
        raise SystemExit(f"Tailscale 还不能用：{error}") from error
    Handler.binary = binary
    server = ThreadingHTTPServer((address, load_config()["port"]), Handler)
    print(f"监听 http://{address}:{server.server_address[1]} ，只接受 Tailscale 允许名单中的设备")
    server.serve_forever()


if __name__ == "__main__":
    main()
