"""弟弟口头指令的工具注册、风险和确认。不直接调用 QQ。"""

import hashlib
import hmac
import json
import re
import secrets
import time
import urllib.error
import urllib.request
from pathlib import Path

BROTHER_ID = "273585129"
CONFIRM_SESSION = f"qq_napcat:FriendMessage:{BROTHER_ID}"
GROUP_CONFIRM_HINT = "需要去私聊确认，群里不会说具体内容。"


def is_brother(sender: str) -> bool:
    return str(sender) == BROTHER_ID
CONFIRM_TTL = 10 * 60
MAX_RESULT = 800

TOOLS = {
    "status": {"risk": "low", "args": ()},
    "set_group": {"risk": "low", "args": ("enabled",)},
    "set_voice": {"risk": "low", "args": ("enabled",)},
    "mood": {"risk": "low", "args": ()},
    "memory_query": {"risk": "low", "args": ("text",)},
    "search": {"risk": "low", "args": ("query",)},
    "learn_bilibili": {"risk": "low", "args": ("query",)},
    "learn_moegirl": {"risk": "low", "args": ("topic",)},
    "learn_voice": {"risk": "low", "args": ()},
    "memory_extract": {"risk": "low", "args": ()},
    "send_private": {"risk": "low", "args": ("target", "text", "targets")},
    "send_group": {"risk": "low", "args": ("target", "text", "targets")},
    "reply_current": {"risk": "low", "args": ("text",)},
    "health": {"risk": "low", "args": ()},
    "logs": {"risk": "low", "args": ("lines",)},
    "job_status": {"risk": "low", "args": ()},
    "speak": {"risk": "low", "args": ("text",)},
    "proactive": {"risk": "low", "args": ()},
    "computer": {"risk": "high", "args": ("instruction",)},
    "edit_project": {"risk": "high", "args": ("instruction",)},
    "restart": {"risk": "high", "args": ()},
}

_DELETE_RE = re.compile(r"删除|清空|格式化|覆盖|drop\s|rm\s|git\s+reset|git\s+clean", re.I)
_SECRET_RE = re.compile(
    r"(cursor_[A-Za-z0-9_\-]{8,}|crsr_[A-Za-z0-9_\-]{8,}|sk-[A-Za-z0-9]{10,}|github_pat_[A-Za-z0-9_]+|password|token|cookie|api[_ ]?key)",
    re.I,
)
_CONFIRM_RE = re.compile(r"^确认(?:\s*([0-9a-fA-F]{4}))?$")
_CANCEL_RE = re.compile(r"^取消$")

CLASSIFY_SYSTEM = """你把弟弟的一句话解析成工具调用。只输出一个 JSON 对象，不要解释。
闲聊、提问、情绪，输出 {"tool":"none","args":{}}。
只有他明确让你做事才选工具。工具名只能是下面这些：
status 查看群聊、语音、情绪状态
set_group 开关群聊，args.enabled 为 true 或 false
set_voice 开关语音，args.enabled 为 true 或 false
mood 查看心情
memory_query 查询记得的人或事，args.text 是要查的内容
search 搜索公开资料，args.query
learn_bilibili 现在去B站学，args.query 可空
learn_moegirl 现在从萌娘百科学姐姐人格，args.topic 可空
learn_voice 学习说话方式
memory_extract 整理记忆
send_private 私聊一个人，args.target 是QQ或唯一称呼，args.text 是要发的话
send_group 往一个群发一条，args.target 是群号或“这个群”，args.text
reply_current 在当前会话回复指定内容，args.text
health 检查程序是否在运行
logs 看最近日志，args.lines 数字
job_status 查询上一个本机任务
speak 把一句话发成语音，args.text
proactive 现在主动给弟弟发一条
computer 操作电脑、浏览器或系统，args.instruction
edit_project 修改本项目文件，args.instruction
restart 重启机器人或代理
不能发明工具。一次只选一个。"""


def redact(text: str) -> str:
    return _SECRET_RE.sub("[已隐藏]", text or "")


def parse_decision(raw: str) -> dict | None:
    """校验模型输出。未知工具返回 None，闲聊返回 tool=none。"""
    start, end = (raw or "").find("{"), (raw or "").rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    tool = str(data.get("tool") or "").strip()
    if tool in {"none", "chat", ""}:
        return {"tool": "none", "args": {}}
    if tool not in TOOLS:
        return None
    incoming = data.get("args") if isinstance(data.get("args"), dict) else {}
    args = {}
    for key in TOOLS[tool]["args"]:
        if key not in incoming or incoming[key] is None:
            continue
        value = incoming[key]
        if key == "targets" and isinstance(value, list):
            args[key] = [re.sub(r"\s+", "", str(item))[:40] for item in value if str(item).strip()][:10]
            continue
        if key == "enabled":
            if value is True or str(value).lower() in {"true", "1", "on", "开"}:
                args[key] = True
            elif value is False or str(value).lower() in {"false", "0", "off", "关"}:
                args[key] = False
            continue
        if key == "lines":
            try:
                args[key] = max(1, min(40, int(value)))
            except (TypeError, ValueError):
                args[key] = 20
            continue
        text = re.sub(r"[\r\n]+", " ", str(value)).strip()
        limit = 2000 if key == "instruction" else 500 if key == "text" else 80
        if text:
            args[key] = text[:limit]
    return {"tool": tool, "args": args}


def _targets(args: dict) -> list[str]:
    values = []
    if args.get("target"):
        values.append(str(args["target"]))
    extra = args.get("targets")
    if isinstance(extra, list):
        values.extend(str(item) for item in extra if str(item).strip())
    return values


def effective_risk(tool: str, args: dict, known_targets: set[str] | None = None) -> str:
    """注册表决定风险，模型不能把它改成低风险。"""
    if tool not in TOOLS:
        return "high"
    risk = TOOLS[tool]["risk"]
    if tool in {"send_private", "send_group"}:
        targets = _targets(args)
        if len(targets) > 1 or args.get("batch"):
            return "high"
        if targets and known_targets is not None and targets[0] not in known_targets and targets[0] not in {"这个群", "这里", "群里", "当前群"}:
            if re.fullmatch(r"\d{5,12}", targets[0]):
                return "high"
    instruction = str(args.get("instruction") or "")
    if tool in {"computer", "edit_project"} and _DELETE_RE.search(instruction):
        return "delete"
    return risk


def resolve_target(tool: str, target: str, people: list[dict], groups: list[dict], current_group: str = "") -> dict:
    """唯一目标才返回 session。重名或找不到时要求说明，不猜测。"""
    text = (target or "").strip()
    if tool == "send_group" and text in {"这个群", "这里", "群里", "当前群"}:
        if current_group:
            return {"ok": True, "session": f"qq_napcat:GroupMessage:{current_group}", "label": "当前群", "known": True}
        if len(groups) == 1:
            return {"ok": True, "session": f"qq_napcat:GroupMessage:{groups[0]['id']}", "label": groups[0].get("name") or groups[0]["id"]}
        return {"ok": False, "error": "要发到哪个群，把群号给我。"}
    if re.fullmatch(r"\d{5,12}", text):
        kind = "GroupMessage" if tool == "send_group" else "FriendMessage"
        return {"ok": True, "session": f"qq_napcat:{kind}:{text}", "label": text, "known": _known_id(text, people, groups)}
    pool = groups if tool == "send_group" else people
    hits = []
    for item in pool:
        names = [item.get("name") or "", *(item.get("aliases") or [])]
        if text and text in names:
            hits.append(item)
    if len(hits) == 1:
        item = hits[0]
        if tool == "send_group":
            return {"ok": True, "session": f"qq_napcat:GroupMessage:{item['id']}", "label": item.get("name") or item["id"], "known": True}
        if not item.get("qq"):
            return {"ok": False, "error": f"我记得{item.get('name')}，但没有QQ号。"}
        return {"ok": True, "session": f"qq_napcat:FriendMessage:{item['qq']}", "label": item.get("name") or item["qq"], "known": True}
    if len(hits) > 1:
        return {"ok": False, "error": "这个称呼对上了不止一个人，把QQ号给我。"}
    return {"ok": False, "error": "我没对上这个人。" if tool == "send_private" else "我没对上这个群。"}


def _known_id(value: str, people: list[dict], groups: list[dict]) -> bool:
    return any(str(item.get("qq") or item.get("id") or "") == value for item in [*people, *groups])


def sign_request(secret: str, timestamp: str, nonce: str, body: bytes) -> str:
    message = timestamp.encode() + b"." + nonce.encode() + b"." + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_request(secret: str, timestamp: str, nonce: str, body: bytes, signature: str, now: float, seen: set[str]) -> str:
    """通过返回空字符串，失败返回原因。"""
    try:
        stamp = int(timestamp)
    except (TypeError, ValueError):
        return "时间无效"
    if abs(now - stamp) > 120:
        return "请求过期"
    if not nonce or nonce in seen:
        return "请求重复"
    expected = sign_request(secret, timestamp, nonce, body)
    if not hmac.compare_digest(expected, signature or ""):
        return "签名不对"
    seen.add(nonce)
    if len(seen) > 500:
        seen.clear()
        seen.add(nonce)
    return ""


class ConfirmStore:
    def __init__(self, path: Path):
        self.path = path

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data.setdefault("pending", None)
        data.setdefault("used", [])
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)

    def issue(self, origin_session: str, sender: str, tool: str, args: dict, summary: str, now: float, phase: int = 1) -> str:
        data = self._load()
        used = set(data.get("used") or [])
        code = secrets.token_hex(2)
        while code in used:
            code = secrets.token_hex(2)
        data["pending"] = {
            "origin_session": origin_session,
            "sender": sender,
            "tool": tool,
            "args": args,
            "summary": summary,
            "code": code,
            "phase": phase,
            "expires": now + CONFIRM_TTL,
        }
        self._save(data)
        return code

    def match(self, session: str, sender: str, text: str, now: float) -> dict:
        """返回 action: none、cancel、invalid、pending。确认和取消只在私聊进行。"""
        cleaned = re.sub(r"\s+", "", text.strip())
        data = self._load()
        pending = data.get("pending")
        if _CANCEL_RE.match(cleaned):
            if not pending:
                return {"action": "none"}
            if session != CONFIRM_SESSION:
                return {"action": "invalid", "error": "取消请在私聊进行。"}
            data["pending"] = None
            self._save(data)
            return {"action": "cancel"}
        matched = _CONFIRM_RE.match(cleaned)
        if not matched:
            return {"action": "none"}
        if session != CONFIRM_SESSION:
            return {"action": "invalid", "error": "确认请在私聊进行。"}
        if not pending or pending.get("expires", 0) < now:
            data["pending"] = None
            self._save(data)
            return {"action": "invalid", "error": "现在没有要确认的操作。"}
        if pending.get("sender") != sender:
            return {"action": "invalid", "error": "只有弟弟能确认。"}
        code = (matched.group(1) or "").lower()
        if not code:
            return {"action": "invalid", "error": "把四个字符的确认码一起发来。"}
        if code != str(pending.get("code")):
            return {"action": "invalid", "error": "确认码不对。"}
        if code in data.get("used", []):
            return {"action": "invalid", "error": "这个确认码已经用过了。"}
        data["used"] = [*data.get("used", []), code][-50:]
        data["pending"] = None
        self._save(data)
        return {"action": "pending", "pending": pending}


def append_audit(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    safe = {key: redact(str(value))[:500] for key, value in record.items()}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(safe, ensure_ascii=False) + "\n")


def load_secret(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def post_executor(url: str, secret: str, instruction: str, timeout: float = 10) -> dict:
    body = json.dumps({"instruction": instruction, "id": secrets.token_hex(8)}, ensure_ascii=False).encode()
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(8)
    request = urllib.request.Request(
        url.rstrip("/") + "/internal/jobs",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Timestamp": timestamp,
            "X-Nonce": nonce,
            "X-Signature": sign_request(secret, timestamp, nonce, body),
        },
        method="POST",
    )
    return _executor_json(request, timeout)


def get_executor_job(url: str, secret: str, job_id: str, timeout: float = 10) -> dict:
    path = "/internal/jobs/" + job_id
    body = b""
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(8)
    request = urllib.request.Request(
        url.rstrip("/") + path,
        headers={
            "X-Timestamp": timestamp,
            "X-Nonce": nonce,
            "X-Signature": sign_request(secret, timestamp, nonce, body),
        },
        method="GET",
    )
    return _executor_json(request, timeout)


def _executor_json(request: urllib.request.Request, timeout: float) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        return {"ok": False, "error": detail or str(error)}
    except Exception as error:
        return {"ok": False, "error": str(error)}


def search_web(query: str, timeout: float = 8) -> str:
    import urllib.parse

    text = re.sub(r"\s+", " ", query or "").strip()[:80]
    if not text:
        return ""
    request = urllib.request.Request(
        "https://lite.duckduckgo.com/lite/",
        data=urllib.parse.urlencode({"q": text}).encode(),
        headers={"User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            page = response.read().decode("utf-8", "replace")
    except Exception as error:
        return f"搜索失败：{error}"
    lines = []
    for href, label in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, re.I | re.S):
        if "duckduckgo.com" in href or href.startswith("/"):
            continue
        title = re.sub(r"<[^>]+>", "", label)
        title = re.sub(r"\s+", " ", title).strip() or href
        lines.append(f"{title} {href}".strip())
        if len(lines) >= 3:
            break
    return "\n".join(lines) or "没有查到直接链接。"


def confirmation_text(summary: str, code: str, delete: bool = False) -> str:
    if delete:
        return f"{summary}\n这一步会删东西。先回复：确认 {code}"
    return f"{summary}\n回复：确认 {code}"
