"""姐姐：主动消息与群聊回复。

- 弟弟私聊超过 5 小时没有找姐姐时，抓取百度热搜，让模型挑一条有趣的主动分享给他。
- 群聊默认开着。开着时不靠关键词，她觉得这句有兴趣才接。弟弟在私聊里说「打开群聊」或「关掉群聊」可以切换，说「群聊状态」可以看现在开没开。
- 较短的回复有时会用温柔少女声发成语音。
- 聊天里提到的人和事分类整理成 skill 记忆，再次提到时带进回复。
- 每隔几小时从 B 站学一条弟弟在乎的内容，并从 B 站和小红书学一种说法，避免总说同样几句。
- 情绪会停在开心、生气、伤心、抑郁或平静上。对着她说的话才改情绪，大约四十分钟缓一半。
- 语音默认开着。弟弟私聊说「打开语音」或「关掉语音」可以切换。
"""

import asyncio
import base64
import datetime
import json
import random
import re
import sys
import time
from pathlib import Path

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At, Image, Plain, Record, Reply
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.agent.message import TextPart
from astrbot.core.message.message_event_result import MessageChain

from .memory import EXTRACT_SYSTEM_PROMPT, MemoryStore, parse_updates
from .moegirl import learn_moegirl_persona
from .mood import concerns_her, mood_note, update_mood
from .media_perceive import perceive
from .persona_speak import OUTBOUND_SYSTEM, REWRITE_PROMPT
from .sovits_tts import REF_AUDIO, SOVITS_URL, synthesize_to_file
from .study import learn_once, learn_voice
from .tools import (
    CLASSIFY_SYSTEM,
    MAX_RESULT,
    ConfirmStore,
    GROUP_CONFIRM_HINT,
    append_audit,
    confirmation_text,
    effective_risk,
    get_executor_job,
    load_secret,
    is_brother,
    parse_decision,
    post_executor,
    redact,
    resolve_target,
    search_web,
)

_VENDOR = Path(__file__).resolve().parent / "vendor"
if _VENDOR.is_dir():
    sys.path.insert(0, str(_VENDOR))

PLUGIN_NAME = "astrbot_plugin_oneesan"
BROTHER_ID = "273585129"
BROTHER_SESSION = f"qq_napcat:FriendMessage:{BROTHER_ID}"
SHARED_MEMORY_MESSAGES = 8
_ID_RE = re.compile(r"User ID:\s*(\d+).*?Nickname:\s*([^\n]+)")
_GROUP_RE = re.compile(r"Group name:\s*([^\n]+)")
_REMINDER_RE = re.compile(r"<system_reminder>(.*?)</system_reminder>", re.S)
IDLE_SECONDS = 5 * 3600
CHECK_INTERVAL_SECONDS = 300
QUIET_HOURS = range(0, 8)
NEWS_URL = "https://top.baidu.com/api/board?tab=realtime"
NEWS_COUNT = 20
HISTORY_MESSAGES = 20
VOICE_CHANCE = 0.3
VOICE_MAX_CHARS = 80
MEMORY_MODEL = "oneesan-memory"
EXECUTOR_URL = "http://host.docker.internal:8792"
RESTART_INSTRUCTION = """只重启本机的 qqbot 服务：
重启 Docker 容器 astrbot。
如果 8766 没有进程在听，重新拉起 /Users/tciuser1/qqbot/proxy/main.py，工作目录是 /Users/tciuser1/qqbot/proxy。
不要修改群聊开关、语音开关、密钥、Tailscale 或 crontab。不要 git commit。
完成后用简短中文说明结果。"""
MEMORY_CONTEXT_MESSAGES = 4
MEMORY_BATCH_MESSAGES = 30
STUDY_INTERVAL_SECONDS = 6 * 3600
STUDY_RETRY_SECONDS = 1800
VOICE_INTERVAL_SECONDS = 6 * 3600
MOEGIRL_INTERVAL_SECONDS = 2 * 3600
MOEGIRL_RETRY_SECONDS = 20 * 60

# 群聊是否接收消息、语音是否发送。只由弟弟的私聊指示改，并写入 state。
_GROUP_ENABLED = {"on": True}
_VOICE_ENABLED = {"on": True}
_GROUP_PAUSE_LOGGED: set[str] = set()
_GROUP_ON_RE = re.compile(r"(?:把)?群聊(?:打开|开启|开了|开)|(?:打开|开启|恢复)群聊")
_GROUP_OFF_RE = re.compile(r"(?:把)?群聊(?:关闭|关掉|暂停|关了|关)|(?:关闭|关掉|暂停)群聊")
_GROUP_ASK = {"群聊开了吗", "群聊关了吗", "群聊开着吗", "群聊关着吗", "群聊状态"}
_VOICE_ON_RE = re.compile(r"(?:把)?语音(?:打开|开启|开了|开)|(?:打开|开启|恢复)语音")
_VOICE_OFF_RE = re.compile(r"(?:把)?语音(?:关闭|关掉|暂停|关了|关)|(?:关闭|关掉|暂停)语音")
_VOICE_ASK = {"语音开了吗", "语音关了吗", "语音开着吗", "语音关着吗", "语音状态"}

# 这些是别的插件的指令，不拿来判断她想不想接话。
_COMMAND_RE = re.compile(r"^(?:/|签到|搜索歌曲|下载|来点|来一份|来一张|刷新背景)")
FOLLOW_REPLY_SECONDS = 180
_THREADS: dict[str, dict] = {}
_INTEREST_SYSTEM = """你在决定要不要接群里刚来的这句。只输出「接」或「不接」，不要解释。
你是这个群里的姐姐。不是每句都接。
有兴趣就接：这句在跟你说、你想插一句、刚聊到一半你还想续、问了你想答的事。
没兴趣就不接：别人互相聊天、跟你无关、太碎、只是指令。
别人教你怎么说话，不因此就接。点了你的名时多半接，但这句要是跟你无关，也可以不接。"""

COMPOSE_PROMPT = """[{now}]（这条不是弟弟发的，是给你的提示）弟弟已经{hours}个多小时没找你了，你想主动找他说说话。
下面是现在的热搜。挑一条轻松有趣、他可能会感兴趣的（不要挑政治、灾难、伤亡这类沉重的），像自己刚看到、轻轻分享给他一样主动发消息。可以轻轻怪他这么久没说话，语气要温柔、有教养，不用"咋了""干嘛"这类糙口。
只根据下面写的内容说，不要编造没写的细节。一两句话就好，只输出你要发给他的消息本身。不要问吃饭、睡觉、在做什么。

{news}"""


def _occasion_note(event: AstrMessageEvent) -> str:
    hour = datetime.datetime.now().hour
    if hour < 6 or hour >= 23:
        when = "现在是夜里。语气放轻。对方没说困，就不要催睡。"
    elif hour < 9:
        when = "现在是早晨。语气轻一点。对方没提吃的，就不要问早饭。"
    elif hour < 18:
        when = "现在是白天。回复短一些。不要因为是白天就问在做什么。"
    else:
        when = "现在是晚上。可以多说一句。不要因为是晚上就问吃饭。"
    sender = str(event.get_sender_id())
    if sender == BROTHER_ID and event.is_private_chat():
        who = "现在是私聊，对方就是弟弟本人。可以用只给他的亲近语气。"
    elif sender == BROTHER_ID:
        who = "现在是群聊，这句话是弟弟说的。当众要短，不要肉麻。"
    else:
        who = (
            f"现在是群聊，这句话是群友「{event.get_sender_name()}」（QQ {sender}）说的。"
            "他喊姐姐只是在叫你，不是你弟弟。禁止叫他弟弟，禁止撒娇和查岗。"
        )
    return who + "\n" + when


def _memory_line(message: dict, place: str) -> str:
    content = message.get("content")
    raw = content if isinstance(content, str) else ""
    if isinstance(content, list):
        raw = " ".join(
            part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"
        )
    reminder = " ".join(_REMINDER_RE.findall(raw))
    text = _REMINDER_RE.sub("", raw).strip().replace("\n", " ")
    if not text:
        return ""
    who = "你"
    if message.get("role") == "user":
        match = _ID_RE.search(reminder)
        if match and match.group(1) == BROTHER_ID:
            who = f"弟弟({match.group(2).strip()})"
        elif match:
            who = f"{match.group(2).strip()}(QQ {match.group(1)})"
        else:
            who = "对方"
    group = _GROUP_RE.search(reminder)
    where = f"群 {group.group(1).strip()}" if group else place
    return f"[{where}] {who}：{text[:80]}"


def _assistant_text(message: dict) -> str:
    if message.get("role") != "assistant":
        return ""
    content = message.get("content")
    raw = content if isinstance(content, str) else ""
    if isinstance(content, list):
        raw = " ".join(
            part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"
        )
    text = _REMINDER_RE.sub("", raw).strip().replace("\n", " ")
    return text[:40]


def _group_enabled() -> bool:
    return bool(_GROUP_ENABLED["on"])


def _voice_enabled() -> bool:
    return bool(_VOICE_ENABLED["on"])


def _switch_order(text: str, on_re: re.Pattern, off_re: re.Pattern, ask: set[str]) -> str | None:
    text = re.sub(r"\s+", "", text.strip())
    text = re.sub(r"^[姐姐,，。!！~～:：]+", "", text)
    text = re.sub(r"[吧呀啊呢。！!？?~～]+$", "", text)
    if not text or len(text) > 16:
        return None
    if text in ask:
        return "ask"
    if off_re.fullmatch(text):
        return "off"
    if on_re.fullmatch(text):
        return "on"
    return None


def _group_order(text: str) -> str | None:
    return _switch_order(text, _GROUP_ON_RE, _GROUP_OFF_RE, _GROUP_ASK)


def _voice_order(text: str) -> str | None:
    return _switch_order(text, _VOICE_ON_RE, _VOICE_OFF_RE, _VOICE_ASK)


def _own_text(event: AstrMessageEvent) -> str:
    """只用弟弟自己打出来的字，不执行引用里的内容。"""
    parts = [comp.text for comp in event.get_messages() if isinstance(comp, Plain) and comp.text]
    return "".join(parts).strip() or event.get_message_str().strip()


def _group_key(event: AstrMessageEvent) -> str:
    return event.get_group_id() or event.unified_msg_origin


def _touch_thread(group: str, peer: str) -> None:
    if not group or not peer:
        return
    thread = _THREADS.setdefault(group, {"until": 0.0, "peers": set()})
    thread["peers"].add(peer)
    thread["until"] = time.time() + FOLLOW_REPLY_SECONDS


def _mentioned(event: AstrMessageEvent) -> bool:
    """点了她的名，或引用了她的消息。这只是交给兴趣判断的材料，不直接触发回复。"""
    self_id = str(event.get_self_id())
    for comp in event.get_messages():
        if isinstance(comp, At) and str(comp.qq) in (self_id, "all"):
            return True
        if isinstance(comp, Reply) and comp.sender_id and str(comp.sender_id) == self_id:
            return True
    return False


def _interest_yes(answer: str) -> bool:
    head = re.sub(r"\s+", "", answer or "")[:2]
    return head.startswith("接") and not head.startswith("不")


def _wake(event: AstrMessageEvent) -> None:
    event.is_at_or_wake_command = True
    event.is_wake = True
    event.set_extra("oneesan_interest", True)
    _touch_thread(_group_key(event), str(event.get_sender_id()))


class OneesanPlugin(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)
        self._state_path = StarTools.get_data_dir(PLUGIN_NAME) / "state.json"
        self._state = self._load_state()
        _GROUP_ENABLED["on"] = bool(self._state.get("group_enabled", True))
        _VOICE_ENABLED["on"] = bool(self._state.get("voice_enabled", True))
        self._memories = MemoryStore(StarTools.get_data_dir(PLUGIN_NAME) / "skills", BROTHER_ID)
        self._task: asyncio.Task | None = None
        self._background: set[asyncio.Task] = set()
        self._group_record_lock = asyncio.Lock()

    async def initialize(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def terminate(self) -> None:
        if self._task:
            self._task.cancel()

    def _load_state(self) -> dict:
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"last_active": time.time(), "last_proactive": 0.0}

    def _save_state(self) -> None:
        self._state_path.write_text(json.dumps(self._state), encoding="utf-8")

    def _set_group_enabled(self, enabled: bool) -> None:
        _GROUP_ENABLED["on"] = enabled
        self._state["group_enabled"] = enabled
        self._save_state()
        logger.info(f"[姐姐] 群聊{'开' if enabled else '关'}")

    def _set_voice_enabled(self, enabled: bool) -> None:
        _VOICE_ENABLED["on"] = enabled
        self._state["voice_enabled"] = enabled
        self._save_state()
        logger.info(f"[姐姐] 语音{'开' if enabled else '关'}")

    async def _media_digest(self, event: AstrMessageEvent) -> str:
        cached = event.get_extra("oneesan_media_digest")
        if isinstance(cached, str):
            return cached
        lines: list[str] = []
        for comp in event.get_messages():
            try:
                if isinstance(comp, Image):
                    b64 = await comp.convert_to_base64()
                    desc = await perceive("image", base64.b64decode(b64))
                    lines.append(f"【图片】{desc}")
                elif isinstance(comp, Record):
                    path = await comp.convert_to_file_path()
                    wav = Path(path).read_bytes()
                    said = await perceive("audio", wav)
                    lines.append(f"【语音】{said}")
            except Exception:
                logger.exception("[姐姐] 媒体识别失败")
        note = "\n".join(lines)
        event.set_extra("oneesan_media_digest", note)
        return note

    def _message_with_media(self, event: AstrMessageEvent, digest: str) -> str:
        text = event.get_message_str().strip()
        if digest:
            return f"{text}\n{digest}".strip() if text else digest
        return text

    @filter.on_llm_request()
    async def attach_media_understanding(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        note = await self._media_digest(event)
        if not note:
            return
        req.extra_user_content_parts.append(
            TextPart(
                text="<system_reminder>对方发了图片或语音，内容如下（已识别）：\n"
                + note
                + "</system_reminder>"
            ).mark_as_temp()
        )
        req.prompt = self._message_with_media(event, note)

    @filter.on_llm_request()
    async def record_activity(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        sender = str(event.get_sender_id())
        if sender == BROTHER_ID:
            self._state["last_active"] = time.time()
        personal = (event.is_private_chat() and sender == BROTHER_ID) or concerns_her(event.get_message_str())
        if event.get_extra("oneesan_interest"):
            personal = True
        changed = update_mood(self._state, event.get_message_str(), personal=personal)
        self._save_state()
        if changed:
            logger.info(f"[姐姐] 情绪 {changed}")
        notes = [_occasion_note(event), mood_note(self._state)]
        if event.get_extra("oneesan_interest"):
            notes.append("你是因为这句有兴趣才接的。接住就好，一两句。")
        recent = await self._recent_replies(event.unified_msg_origin)
        if recent:
            notes.append("你最近已经说过这些，换说法，不要复用里面的开头、问法和句尾：\n" + recent)
        memory = await self._shared_memory(event.unified_msg_origin)
        if memory:
            notes.append("其他场合的近期对话，是你记得的事，不要说自己是从别处看到的：\n" + memory)
        recalled = self._memories.render(
            self._memories.recall(event.get_message_str(), sender), event.is_private_chat()
        )
        if recalled:
            notes.append("你记得的相关的人、事，以及从B站学到的，自然地用上，不要像背资料：\n" + recalled)
        voice = self._memories.render(self._memories.latest("voice", 2), private=True)
        if voice:
            notes.append("你学过这些说法。这次只借用一种，换成自己的话，不要和最近几句用同一种：\n" + voice)
        persona = self._memories.render(self._memories.latest("persona", 6), private=True)
        if persona:
            notes.append(
                "这是你从姐姐角色样本中逐渐形成的喜恶、观点、兴趣和习惯。"
                "它们是你自己的稳定倾向，但不是你的真实经历；自然表现，不要逐条宣读：\n"
                + persona
            )
        req.extra_user_content_parts.append(
            TextPart(text="<system_reminder>" + "\n".join(notes) + "</system_reminder>").mark_as_temp()
        )

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=2000)
    async def brother_private_command(self, event: AstrMessageEvent):
        reply = await self._brother_command(event)
        if reply:
            yield event.plain_result(redact(reply)[:MAX_RESULT])
            event.stop_event()

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=2000)
    async def brother_group_command(self, event: AstrMessageEvent):
        reply = await self._brother_command(event)
        if reply:
            yield event.plain_result(redact(reply)[:MAX_RESULT])
            event.stop_event()

    async def _brother_command(self, event: AstrMessageEvent) -> str | None:
        if not is_brother(event.get_sender_id()):
            return None
        text = _own_text(event)
        if not text:
            return None
        store = ConfirmStore(self._state_path.parent / "command_state.json")
        session = event.unified_msg_origin
        outcome = store.match(session, BROTHER_ID, text, time.time())
        if outcome["action"] == "cancel":
            self._audit(event, "cancel", {})
            return "取消了。"
        if outcome["action"] == "invalid":
            return outcome["error"]
        if outcome["action"] == "pending":
            pending = outcome["pending"]
            origin = pending.get("origin_session") or pending.get("session") or session
            if pending.get("phase") == 1 and effective_risk(pending["tool"], pending["args"]) == "delete":
                return await self._offer_confirmation(
                    event,
                    store,
                    pending["tool"],
                    pending["args"],
                    "再确认一次才会删除或覆盖。",
                    "delete",
                    phase=2,
                )
            return await self._run_tool(event, pending["tool"], pending["args"], origin_session=origin)
        switched = self._switch_reply(text)
        if switched:
            return switched
        if len(text) < 4:
            return None
        decision = await self._classify_command(text)
        if not decision or decision["tool"] == "none":
            return None
        return await self._dispatch(event, store, decision["tool"], decision["args"])

    def _switch_reply(self, text: str) -> str | None:
        order = _group_order(text)
        voice = None if order else _voice_order(text)
        if order is None and voice is None:
            return None
        if order == "on":
            self._set_group_enabled(True)
            return "群聊打开了。"
        if order == "off":
            self._set_group_enabled(False)
            return "群聊关掉了。"
        if order == "ask":
            return "群聊开着。" if _group_enabled() else "群聊关着。"
        if voice == "on":
            self._set_voice_enabled(True)
            return "语音打开了。"
        if voice == "off":
            self._set_voice_enabled(False)
            return "语音关掉了。"
        return "语音开着。" if _voice_enabled() else "语音关着。"

    async def _classify_command(self, text: str) -> dict | None:
        provider = self.context.get_using_provider(BROTHER_SESSION)
        if provider is None:
            return None
        try:
            resp = await asyncio.wait_for(
                provider.text_chat(
                    prompt=text,
                    system_prompt=CLASSIFY_SYSTEM,
                    contexts=[],
                    model=MEMORY_MODEL,
                ),
                timeout=8,
            )
        except Exception:
            logger.exception("[姐姐] 口头指令没有解析出来")
            return None
        return parse_decision(resp.completion_text or "")

    async def _dispatch(self, event: AstrMessageEvent, store: ConfirmStore, tool: str, args: dict) -> str:
        prepared = await self._prepare_tool(event, tool, args)
        if prepared.get("error"):
            return prepared["error"]
        args = prepared["args"]
        risk = prepared["risk"]
        if risk == "low":
            return await self._run_tool(event, tool, args)
        return await self._offer_confirmation(event, store, tool, args, prepared["summary"], risk)

    async def _offer_confirmation(
        self,
        event: AstrMessageEvent,
        store: ConfirmStore,
        tool: str,
        args: dict,
        summary: str,
        risk: str,
        phase: int = 1,
    ) -> str:
        code = store.issue(event.unified_msg_origin, BROTHER_ID, tool, args, summary, time.time(), phase=phase)
        self._audit(event, tool, args, stage="等待确认")
        body = confirmation_text(summary, code, delete=risk == "delete")
        if event.is_private_chat():
            return body
        await self._tell(BROTHER_SESSION, body)
        return GROUP_CONFIRM_HINT

    async def _prepare_tool(self, event: AstrMessageEvent, tool: str, args: dict) -> dict:
        if tool in {"send_private", "send_group"}:
            targets = [item for item in (args.get("targets") or []) if item]
            if len(targets) > 1 or (args.get("target") and targets):
                return {"error": "一次只发给一个地方。"}
            if not args.get("text"):
                return {"error": "要发什么，说具体一点。"}
            if not args.get("target") and len(targets) == 1:
                args = {**args, "target": targets[0]}
            people = self._people()
            groups = await self._known_groups()
            current = event.get_group_id() or ""
            resolved = resolve_target(tool, args.get("target", ""), people, groups, current)
            if not resolved["ok"]:
                return {"error": resolved["error"]}
            args = {**args, "session": resolved["session"], "label": resolved["label"]}
            risk = "high" if resolved.get("known") is False else "low"
            label = resolved["label"]
            return {"args": args, "risk": risk, "summary": f"要发给{label}：{args['text'][:80]}"}
        if tool == "reply_current" and not args.get("text"):
            return {"error": "要我回什么。"}
        if tool in {"computer", "edit_project"} and not args.get("instruction"):
            return {"error": "要我做什么。"}
        if tool == "speak" and not args.get("text"):
            return {"error": "要念哪一句。"}
        risk = effective_risk(tool, args)
        summary = {
            "computer": "要在这台电脑上做：" + args.get("instruction", "")[:120],
            "edit_project": "要改项目：" + args.get("instruction", "")[:120],
            "restart": "要重启机器人和代理。",
        }.get(tool, tool)
        return {"args": args, "risk": risk, "summary": summary}

    async def _run_tool(
        self,
        event: AstrMessageEvent,
        tool: str,
        args: dict,
        origin_session: str | None = None,
    ) -> str:
        self._audit(event, tool, args, stage="执行")
        session = origin_session or event.unified_msg_origin
        deliver_here = session == event.unified_msg_origin
        try:
            if tool == "status":
                mood = self._state.get("mood") or "平静"
                return f"群聊{'开着' if _group_enabled() else '关着'}，语音{'开着' if _voice_enabled() else '关着'}。现在{mood}。"
            if tool == "set_group":
                if "enabled" not in args:
                    return "说开还是关。"
                self._set_group_enabled(bool(args["enabled"]))
                return "群聊打开了。" if args["enabled"] else "群聊关掉了。"
            if tool == "set_voice":
                if "enabled" not in args:
                    return "说开还是关。"
                self._set_voice_enabled(bool(args["enabled"]))
                return "语音打开了。" if args["enabled"] else "语音关掉了。"
            if tool == "mood":
                return f"现在{self._state.get('mood') or '平静'}。"
            if tool == "memory_query":
                recalled = self._memories.render(
                    self._memories.recall(args.get("text") or "", BROTHER_ID), private=True
                )
                return recalled or "这块没什么记得的。"
            if tool == "search":
                return await asyncio.to_thread(search_web, args.get("query") or "") or "没有查到。"
            if tool == "learn_bilibili":
                self._later(self._report_bilibili(session, args.get("query") or ""))
                return "去B站学了，学完告诉你。"
            if tool == "learn_moegirl":
                self._later(self._report_moegirl(session, args.get("topic") or ""))
                return "去萌娘百科学了，学完告诉你。"
            if tool == "learn_voice":
                self._later(self._report_voice(session))
                return "去学说法了，学完告诉你。"
            if tool == "memory_extract":
                await self._update_memories()
                return "记忆整理过了。"
            if tool == "send_private" or tool == "send_group":
                spoken = await self._as_oneesan(args["text"], session=args["session"])
                await self.context.send_message(args["session"], MessageChain().message(spoken))
                return f"发给{args.get('label') or '那边'}了。"
            if tool == "reply_current":
                spoken = await self._as_oneesan(args["text"], session=session)
                if deliver_here:
                    return spoken
                await self.context.send_message(session, MessageChain().message(spoken))
                return "在那个聊天里回复了。"
            if tool == "health":
                return await self._health_text()
            if tool == "logs":
                return self._log_text(int(args.get("lines") or 20))
            if tool == "job_status":
                return await self._job_status_text()
            if tool == "speak":
                spoken = await self._as_oneesan(args["text"], event=event, session=session)
                path = await self._synthesize(spoken[:VOICE_MAX_CHARS])
                if not path:
                    return "语音没合成出来。"
                await self.context.send_message(session, MessageChain([Record.fromFileSystem(path)]))
                return "语音发了。" if deliver_here else "语音发到那边了。"
            if tool == "proactive":
                sent = await self._maybe_send_proactive(force=True)
                return "主动消息发出去了。" if sent else "这回没什么可说的。"
            if tool in {"computer", "edit_project", "restart"}:
                instruction = RESTART_INSTRUCTION if tool == "restart" else args.get("instruction", "")
                return await self._submit_computer(session, instruction)
        except Exception as error:
            logger.exception("[姐姐] 工具执行失败")
            return f"这步没做成：{error}"
        return "这个我还做不了。"

    def _later(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _ask_memory(self, prompt: str, system_prompt: str) -> str:
        provider = self.context.get_using_provider(BROTHER_SESSION)
        if provider is None:
            raise RuntimeError("没有可用模型")
        resp = await provider.text_chat(
            prompt=prompt,
            system_prompt=system_prompt,
            contexts=[],
            model=MEMORY_MODEL,
        )
        return resp.completion_text or ""

    async def _report_bilibili(self, session: str, query: str) -> None:
        try:
            bvid = await learn_once(
                self._memories, self._ask_memory, self._state.get("studied_bvids") or [], self._interest_brief(), query
            )
        except Exception as error:
            await self._tell(session, f"B站这次没学成：{error}")
            return
        if bvid:
            bvids = self._state.setdefault("studied_bvids", [])
            bvids.append(bvid)
            self._state["studied_bvids"] = bvids[-200:]
            self._state["last_study"] = time.time()
            self._save_state()
        await self._tell(session, f"B站学完了 {bvid}。" if bvid else "这次没找到新的。")

    async def _report_moegirl(self, session: str, topic: str) -> None:
        try:
            learned = await learn_moegirl_persona(
                self._memories, self._ask_memory, self._state.get("moegirl_seen") or [], topic
            )
        except Exception as error:
            await self._tell(session, f"萌娘百科这次没学成：{error}")
            return
        if learned:
            seen = self._state.setdefault("moegirl_seen", [])
            seen.extend(learned)
            self._state["moegirl_seen"] = list(dict.fromkeys(seen))[-500:]
            self._state["last_moegirl_study"] = time.time()
            self._save_state()
        await self._tell(session, f"萌娘百科读了 {len(learned)} 个角色。" if learned else "这次没读到新的角色。")

    async def _report_voice(self, session: str) -> None:
        try:
            result = await learn_voice(self._memories, self._ask_memory, self._state.get("voice_seen") or [])
        except Exception as error:
            await self._tell(session, f"说法这次没学成：{error}")
            return
        seen = self._state.setdefault("voice_seen", [])
        seen.extend(result.get("saved") or [])
        self._state["voice_seen"] = seen[-200:]
        self._state["last_voice_study"] = time.time()
        self._save_state()
        await self._tell(session, f"说法记下了：{result.get('saved') or '没有新的'}。")

    async def _submit_computer(self, session: str, instruction: str) -> str:
        secret = load_secret(self._state_path.parent / "executor_secret")
        if not secret:
            return "执行器未配置"
        posted = await asyncio.to_thread(post_executor, EXECUTOR_URL, secret, instruction)
        if not posted.get("ok"):
            error = str(posted.get("error") or "")
            if "未配置" in error:
                return "执行器未配置"
            if "上一条" in error:
                return "上一条还在执行。"
            if "timed out" in error.lower() or "refused" in error.lower() or "61" in error:
                return "执行器没开。"
            return "执行器没有接住。"
        self._state["last_job_id"] = posted["id"]
        self._save_state()
        self._later(self._watch_job(session, posted["id"], secret))
        return "已经交给本机。做完我再告诉你。"

    async def _watch_job(self, session: str, job_id: str, secret: str) -> None:
        for _ in range(60):
            await asyncio.sleep(3)
            found = await asyncio.to_thread(get_executor_job, EXECUTOR_URL, secret, job_id)
            status = found.get("status")
            if status and status != "running":
                await self._tell(session, redact(found.get("result") or status)[:MAX_RESULT] or "做完了。")
                return
        await self._tell(session, "任务还在跑，过会儿问我进度。")

    async def _job_status_text(self) -> str:
        job_id = self._state.get("last_job_id")
        if not job_id:
            return "没有本机任务。"
        secret = load_secret(self._state_path.parent / "executor_secret")
        if not secret:
            return "执行器未配置"
        found = await asyncio.to_thread(get_executor_job, EXECUTOR_URL, secret, job_id)
        if not found.get("ok"):
            return "执行器没开。"
        return redact(f"{found.get('status')} {found.get('result') or ''}".strip())[:MAX_RESULT]

    async def _health_text(self) -> str:
        def probe() -> str:
            import urllib.request

            try:
                with urllib.request.urlopen(EXECUTOR_URL + "/internal/health", timeout=3) as response:
                    data = json.loads(response.read().decode("utf-8"))
            except Exception:
                return "执行器没开"
            return "执行器已配置" if data.get("configured") else "执行器未配置"

        executor = await asyncio.to_thread(probe)
        return f"群聊{'开着' if _group_enabled() else '关着'}，语音{'开着' if _voice_enabled() else '关着'}。{executor}。"

    def _log_text(self, lines: int) -> str:
        path = self._state_path.parent / "command_audit.jsonl"
        try:
            rows = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return "还没有操作记录。"
        return "\n".join(rows[-lines:]) or "还没有操作记录。"

    def _people(self) -> list[dict]:
        return [
            {"name": skill.name, "aliases": skill.aliases, "qq": skill.qq}
            for skill in self._memories.skills
            if skill.category == "people"
        ]

    async def _known_groups(self) -> list[dict]:
        convs = await self.context.conversation_manager.get_conversations(platform_id="qq_napcat")
        groups = []
        seen = set()
        for conv in convs:
            user_id = conv.user_id or ""
            if "GroupMessage:" not in user_id:
                continue
            group_id = user_id.rsplit(":", 1)[-1]
            if group_id in seen:
                continue
            seen.add(group_id)
            groups.append({"id": group_id, "name": group_id})
        return groups

    def _occasion_for_session(self, session: str) -> str:
        if session == BROTHER_SESSION:
            return "现在是私聊，对方就是弟弟本人。可以用亲近语气。\n"
        if "GroupMessage:" in session:
            return "这是发到群里的消息。要短，不要肉麻。\n"
        if "FriendMessage:" in session:
            return "这是私聊别人。礼貌自然，不要肉麻。\n"
        return ""

    async def _as_oneesan(
        self,
        raw: str,
        *,
        event: AstrMessageEvent | None = None,
        session: str | None = None,
    ) -> str:
        text = (raw or "").strip()
        if not text:
            return text
        if event is not None:
            occasion = _occasion_note(event) + "\n"
        else:
            occasion = self._occasion_for_session(session or "")
        provider = self.context.get_using_provider(BROTHER_SESSION)
        if provider is None:
            return text
        try:
            resp = await asyncio.wait_for(
                provider.text_chat(
                    prompt=REWRITE_PROMPT.format(raw=text),
                    system_prompt=OUTBOUND_SYSTEM + "\n" + occasion,
                    contexts=[],
                    model=MEMORY_MODEL,
                ),
                timeout=12,
            )
        except Exception:
            logger.exception("[姐姐] 发出去的话没能套上人格")
            return text
        spoken = (resp.completion_text or "").strip()
        return spoken or text

    async def _tell(self, session: str, text: str) -> None:
        spoken = await self._as_oneesan(text, session=session)
        await self.context.send_message(session, MessageChain().message(redact(spoken)[:MAX_RESULT]))

    def _audit(self, event: AstrMessageEvent, tool: str, args: dict, stage: str = "") -> None:
        append_audit(
            self._state_path.parent / "command_audit.jsonl",
            {
                "time": str(int(time.time())),
                "session": event.unified_msg_origin,
                "tool": tool,
                "stage": stage,
                "args": json.dumps(args, ensure_ascii=False),
            },
        )

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=1000)
    async def set_group_from_private(self, event: AstrMessageEvent):
        """弟弟私聊说开或关时，切换群聊并直接回复，不交给模型。"""
        if str(event.get_sender_id()) != BROTHER_ID:
            return
        text_in = event.get_message_str()
        order = _group_order(text_in)
        voice = None if order else _voice_order(text_in)
        if order is None and voice is None:
            return
        if order == "on":
            self._set_group_enabled(True)
            text = "群聊打开了。"
        elif order == "off":
            self._set_group_enabled(False)
            text = "群聊关掉了。"
        elif order == "ask":
            text = "群聊开着。" if _group_enabled() else "群聊关着。"
        elif voice == "on":
            self._set_voice_enabled(True)
            text = "语音打开了。"
        elif voice == "off":
            self._set_voice_enabled(False)
            text = "语音关掉了。"
        else:
            text = "语音开着。" if _voice_enabled() else "语音关着。"
        yield event.plain_result(text)
        event.stop_event()

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=1000)
    async def pause_group_messages(self, event: AstrMessageEvent) -> None:
        """关掉时丢掉全部群消息。开着时，不回复的消息也写入对话记录。"""
        if not _group_enabled():
            group = event.get_group_id() or event.unified_msg_origin
            if group not in _GROUP_PAUSE_LOGGED:
                _GROUP_PAUSE_LOGGED.add(group)
                logger.info(f"[姐姐] 已暂停接收群消息: {group}")
            event.stop_event()
            return
        if str(event.get_sender_id()) == str(event.get_self_id()):
            return
        digest = await self._media_digest(event)
        text = self._message_with_media(event, digest)
        if not text or _COMMAND_RE.match(text):
            if not event.is_at_or_wake_command:
                try:
                    await self._remember_group_message(event, digest)
                except Exception:
                    logger.exception("[姐姐] 记下群消息失败，这句先跳过")
            return
        mentioned = _mentioned(event) or event.is_at_or_wake_command
        event.is_at_or_wake_command = False
        try:
            interested = await self._wants_to_reply(event, mentioned, text)
        except Exception:
            logger.exception("[姐姐] 兴趣判断失败，这句不接")
            interested = False
        if interested:
            _wake(event)
            logger.info(f"[姐姐] 有兴趣接话: {text[:40]}")
            return
        logger.info(f"[姐姐] 没兴趣，只记下: {text[:40]}")
        try:
            await self._remember_group_message(event, digest)
        except Exception:
            logger.exception("[姐姐] 记下群消息失败，这句先跳过")

    async def _remember_group_message(self, event: AstrMessageEvent, digest: str = "") -> None:
        """AstrBot 只在调用模型时写会话。没接话的群消息在这里补记。"""
        text = self._message_with_media(event, digest or await self._media_digest(event))
        if not text:
            return
        personal = concerns_her(text)
        changed = update_mood(self._state, text, personal=personal)
        self._save_state()
        if changed:
            logger.info(f"[姐姐] 情绪 {changed}")
        sender = event.get_sender_name() or "未知"
        reminder = f"User ID: {event.get_sender_id()}, Nickname: {sender}"
        group = getattr(event.message_obj, "group", None)
        group_name = getattr(group, "group_name", None) if group else None
        if group_name:
            reminder += f"\nGroup name: {group_name}"
        now = datetime.datetime.now().astimezone()
        weekday = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")[now.weekday()]
        reminder += f"\nCurrent datetime: {now.strftime('%Y-%m-%d %H:%M (%Z)')}, Weekday: {weekday}"
        message = {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {"type": "text", "text": f"<system_reminder>{reminder}</system_reminder>"},
            ],
        }
        umo = event.unified_msg_origin
        async with self._group_record_lock:
            conv_mgr = self.context.conversation_manager
            cid = await conv_mgr.get_curr_conversation_id(umo)
            if cid:
                conv = await conv_mgr.get_conversation(umo, cid)
                history = conv.history if conv else []
                if isinstance(history, str):
                    history = json.loads(history or "[]")
                await conv_mgr.update_conversation(umo, cid, history=(history or []) + [message])
            else:
                await conv_mgr.new_conversation(umo, platform_id="qq_napcat", content=[message])
        logger.info(f"[姐姐] 记下群消息 {sender}: {text[:40]}")

    async def _wants_to_reply(self, event: AstrMessageEvent, mentioned: bool, text: str) -> bool:
        """问她这句有没有兴趣接。不靠关键词。"""
        provider = self.context.get_using_provider(event.unified_msg_origin) or self.context.get_using_provider(
            BROTHER_SESSION
        )
        if provider is None:
            return False
        recent = await self._recent_group_lines(event)
        prompt = (recent + "\n") if recent else ""
        persona = self._memories.render(self._memories.latest("persona", 6), private=True)
        if persona:
            prompt += "你现在稳定的兴趣和性格：\n" + persona + "\n"
        if mentioned:
            prompt += "这句点到你了，或引用了你。\n"
        prompt += "这句：" + text.strip()[:400]
        resp = await asyncio.wait_for(
            provider.text_chat(
                prompt=prompt,
                system_prompt=_INTEREST_SYSTEM,
                contexts=[],
                model=MEMORY_MODEL,
            ),
            timeout=8,
        )
        return _interest_yes(getattr(resp, "completion_text", "") or "")

    async def _recent_group_lines(self, event: AstrMessageEvent) -> str:
        conv_mgr = self.context.conversation_manager
        cid = await conv_mgr.get_curr_conversation_id(event.unified_msg_origin)
        if not cid:
            return ""
        conv = await conv_mgr.get_conversation(event.unified_msg_origin, cid)
        history = conv.history if conv else []
        if isinstance(history, str):
            history = json.loads(history or "[]")
        lines = []
        for message in (history or [])[-8:]:
            line = _memory_line(message, "群聊")
            if line:
                lines.append(line)
        return "\n".join(lines)

    @filter.on_decorating_result()
    async def maybe_send_voice(self, event: AstrMessageEvent) -> None:
        """发出去前套上姐姐人格；较短的纯文字回复有时改成语音。"""
        result = event.get_result()
        if result is None or not result.chain:
            return
        if any(not isinstance(comp, Plain) for comp in result.chain):
            return
        text = "\n".join(comp.text.strip() for comp in result.chain if comp.text and comp.text.strip())
        if not text:
            return
        if not result.is_llm_result():
            spoken = await self._as_oneesan(text, event=event)
            result.chain = [Plain(spoken)]
            return
        if not event.is_private_chat():
            _touch_thread(_group_key(event), str(event.get_sender_id()))
        if not _voice_enabled():
            return
        if self._state.get("mood") in ("生气", "伤心", "抑郁"):
            return
        if random.random() >= VOICE_CHANCE:
            return
        if len(text) > VOICE_MAX_CHARS:
            return
        path = await self._synthesize(text)
        if path:
            result.chain = [Record.fromFileSystem(path)]

    async def _loop(self) -> None:
        await asyncio.sleep(60)
        while True:
            try:
                await self._maybe_send_proactive()
                await self._update_memories()
                await self._maybe_learn_moegirl()
                await self._maybe_study()
                await self._maybe_learn_voice()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[姐姐] 定时任务出错")
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    async def _maybe_send_proactive(self, force: bool = False) -> bool:
        now = time.time()
        idle = now - self._state["last_active"]
        if not force and (idle < IDLE_SECONDS or now - self._state["last_proactive"] < IDLE_SECONDS):
            return False
        if not force and datetime.datetime.now().hour in QUIET_HOURS:
            return False

        news = await self._fetch_news()
        if not news:
            return False
        history, cid = await self._load_history()
        memory = await self._shared_memory(BROTHER_SESSION)
        prompt = COMPOSE_PROMPT.format(
            now=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            hours=int(idle // 3600),
            news=news,
        )
        if memory:
            prompt += "\n\n你还记得这些其他场合的对话，别把群里的话当私聊原样复述：\n" + memory
        recalled = self._memories.render(self._memories.recall("", BROTHER_ID), private=True)
        if recalled:
            prompt += "\n\n你记得的关于弟弟的事，合适的话可以顺带关心一句：\n" + recalled
        provider = self.context.get_using_provider(BROTHER_SESSION)
        if provider is None:
            return False
        resp = await provider.text_chat(prompt=prompt, contexts=history[-HISTORY_MESSAGES:])
        text = (resp.completion_text or "").strip()
        if not text:
            return False
        spoken = await self._as_oneesan(text, session=BROTHER_SESSION)

        voice_path = None
        if _voice_enabled() and len(spoken) <= VOICE_MAX_CHARS and random.random() < VOICE_CHANCE:
            voice_path = await self._synthesize(spoken)
        if voice_path:
            await self.context.send_message(
                BROTHER_SESSION, MessageChain([Record.fromFileSystem(voice_path)])
            )
        else:
            for line in [l.strip() for l in spoken.splitlines() if l.strip()]:
                await self.context.send_message(BROTHER_SESSION, MessageChain().message(line))
                await asyncio.sleep(random.uniform(1.5, 3.5))

        await self._save_history(history, cid, spoken)
        self._state["last_proactive"] = now
        self._save_state()
        logger.info(f"[姐姐] 已主动发消息: {text}")
        return True

    async def _synthesize(self, text: str) -> str | None:
        voice_dir = StarTools.get_data_dir(PLUGIN_NAME) / "voices"
        voice_dir.mkdir(parents=True, exist_ok=True)
        stamp = str(time.time_ns())
        raw_path = voice_dir / f"{stamp}_sovits.wav"
        out_path = voice_dir / f"{stamp}.wav"
        if not REF_AUDIO:
            logger.error("[姐姐] 未配置 SoVITS 参考音频 QQBOT_SOVITS_REF_AUDIO")
            return None
        try:
            await synthesize_to_file(text, raw_path)
        except Exception as error:
            logger.error(f"[姐姐] SoVITS 合成失败 ({SOVITS_URL}): {error}")
            return None
        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-y",
                "-i",
                str(raw_path),
                "-acodec",
                "pcm_s16le",
                "-ar",
                "24000",
                "-ac",
                "1",
                str(out_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            if await process.wait() != 0 or not out_path.exists() or out_path.stat().st_size == 0:
                logger.error("[姐姐] SoVITS 语音转码失败")
                return None
            return str(out_path)
        except Exception:
            logger.exception("[姐姐] SoVITS 语音转码失败")
            return None
        finally:
            raw_path.unlink(missing_ok=True)

    async def _fetch_news(self) -> str:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.get(NEWS_URL, headers={"User-Agent": "Mozilla/5.0"}) as r:
                data = await r.json(content_type=None)
        items = data["data"]["cards"][0]["content"][:NEWS_COUNT]
        return "\n".join(
            f"- {item.get('word', '')}：{(item.get('desc') or '')[:80]}" for item in items
        )

    async def _maybe_learn_moegirl(self) -> None:
        """分批学习萌娘百科姐姐角色，持续补充独立人格。"""
        now = time.time()
        if now - self._state.get("last_moegirl_study", 0) < MOEGIRL_INTERVAL_SECONDS:
            return
        if now - self._state.get("last_moegirl_attempt", 0) < MOEGIRL_RETRY_SECONDS:
            return
        if datetime.datetime.now().hour in QUIET_HOURS:
            return
        provider = self.context.get_using_provider(BROTHER_SESSION)
        if provider is None:
            logger.info("[姐姐] 没有可用模型，跳过萌娘百科人格学习")
            return
        self._state["last_moegirl_attempt"] = now
        self._save_state()

        async def ask(prompt: str, system_prompt: str) -> str:
            resp = await provider.text_chat(
                prompt=prompt,
                system_prompt=system_prompt,
                contexts=[],
                model=MEMORY_MODEL,
            )
            return resp.completion_text or ""

        try:
            learned = await learn_moegirl_persona(
                self._memories,
                ask,
                self._state.get("moegirl_seen") or [],
            )
        except Exception:
            logger.exception("[姐姐] 萌娘百科人格学习失败")
            return
        if not learned:
            return
        seen = self._state.setdefault("moegirl_seen", [])
        seen.extend(learned)
        self._state["moegirl_seen"] = list(dict.fromkeys(seen))[-500:]
        self._state["last_moegirl_study"] = now
        self._save_state()
        logger.info(f"[姐姐] 萌娘百科人格学习完成：读取 {len(learned)} 个角色")

    async def _maybe_study(self) -> None:
        """按弟弟在乎的事去 B 站学一条，写进 study skill。失败 30 分钟后再试，成功后 6 小时再学。"""
        now = time.time()
        if now - self._state.get("last_study", 0) < STUDY_INTERVAL_SECONDS:
            return
        if now - self._state.get("last_study_attempt", 0) < STUDY_RETRY_SECONDS:
            return
        if datetime.datetime.now().hour in QUIET_HOURS:
            return
        provider = self.context.get_using_provider(BROTHER_SESSION)
        if provider is None:
            logger.info("[姐姐] 没有可用模型，跳过 B 站学习")
            return
        self._state["last_study_attempt"] = now
        self._save_state()

        async def ask(prompt: str, system_prompt: str) -> str:
            resp = await provider.text_chat(
                prompt=prompt,
                system_prompt=system_prompt,
                contexts=[],
                model=MEMORY_MODEL,
            )
            return resp.completion_text or ""

        try:
            bvid = await learn_once(
                self._memories,
                ask,
                self._state.get("studied_bvids") or [],
                self._interest_brief(),
            )
        except Exception:
            logger.exception("[姐姐] B站学习失败")
            return
        self._state["last_study"] = now
        if bvid:
            bvids = self._state.setdefault("studied_bvids", [])
            bvids.append(bvid)
            self._state["studied_bvids"] = bvids[-200:]
            logger.info(f"[姐姐] 从B站学完了 {bvid}")
        self._save_state()

    async def _maybe_learn_voice(self) -> None:
        """从 B 站评论和小红书笔记学一种说法。小红书没返回笔记时只记 B 站。"""
        now = time.time()
        if now - self._state.get("last_voice_study", 0) < VOICE_INTERVAL_SECONDS:
            return
        if now - self._state.get("last_voice_attempt", 0) < STUDY_RETRY_SECONDS:
            return
        if datetime.datetime.now().hour in QUIET_HOURS:
            return
        provider = self.context.get_using_provider(BROTHER_SESSION)
        if provider is None:
            return
        self._state["last_voice_attempt"] = now
        self._save_state()

        async def ask(prompt: str, system_prompt: str) -> str:
            resp = await provider.text_chat(
                prompt=prompt,
                system_prompt=system_prompt,
                contexts=[],
                model=MEMORY_MODEL,
            )
            return resp.completion_text or ""

        try:
            result = await learn_voice(self._memories, ask, self._state.get("voice_seen") or [])
        except Exception:
            logger.exception("[姐姐] 说法学习失败")
            return
        self._state["last_voice_study"] = now
        seen = self._state.setdefault("voice_seen", [])
        seen.extend(result.get("saved") or [])
        self._state["voice_seen"] = seen[-200:]
        self._save_state()
        logger.info(f"[姐姐] 说法学习 {result.get('query')}：记下 {result.get('saved') or '无'}；小红书：{result.get('xhs')}")

    def _interest_brief(self) -> str:
        lines = []
        for skill in self._memories.skills:
            if skill.category == "people" and skill.name == "弟弟":
                lines.append(skill.description)
                lines.extend(skill.facts)
            elif skill.category == "events":
                lines.append(f"{skill.name} {skill.description}")
        return "\n".join(lines)

    async def _update_memories(self) -> None:
        """把各会话里新增的对话整理成人和事的记忆。"""
        progress = self._state.setdefault("memory_progress", {})
        convs = await self.context.conversation_manager.get_conversations(platform_id="qq_napcat")
        for conv in convs:
            history = conv.history
            if isinstance(history, str):
                history = json.loads(history or "[]")
            history = history or []
            done = progress.get(conv.user_id, 0)
            if done > len(history):
                done = 0
            if len(history) - done < 2:
                continue
            end = min(len(history), done + MEMORY_BATCH_MESSAGES)
            private = "FriendMessage" in conv.user_id
            place = "私聊" if private else "群聊"
            context_lines = [
                line for m in history[max(0, done - MEMORY_CONTEXT_MESSAGES) : done]
                if (line := _memory_line(m, place))
            ]
            new_lines = [line for m in history[done:end] if (line := _memory_line(m, place))]
            if new_lines:
                prompt = (
                    f"【已有记忆】\n{self._memories.index()}\n\n"
                    f"【前文，只作参考】\n{chr(10).join(context_lines) or '（无）'}\n\n"
                    f"【新对话】\n{chr(10).join(new_lines)}"
                )
                provider = self.context.get_using_provider(conv.user_id)
                resp = await provider.text_chat(
                    prompt=prompt,
                    system_prompt=EXTRACT_SYSTEM_PROMPT,
                    contexts=[],
                    model=MEMORY_MODEL,
                )
                for update in parse_updates(resp.completion_text or ""):
                    skill = self._memories.apply(update, private)
                    if skill:
                        logger.info(f"[姐姐] 记住了{skill.category}/{skill.name}: {update.get('facts')}")
            progress[conv.user_id] = end
            self._save_state()

    async def _shared_memory(self, current_umo: str) -> str:
        convs = await self.context.conversation_manager.get_conversations(platform_id="qq_napcat")
        lines = []
        for conv in convs:
            if conv.user_id == current_umo:
                continue
            history = conv.history
            if isinstance(history, str):
                history = json.loads(history or "[]")
            place = "私聊" if "FriendMessage" in (conv.user_id or "") else "群聊"
            for message in (history or [])[-SHARED_MEMORY_MESSAGES:]:
                if message.get("role") != "user":
                    continue
                line = _memory_line(message, place)
                if line:
                    lines.append(line)
        return "\n".join(lines[-16:])

    async def _recent_replies(self, umo: str) -> str:
        conv_mgr = self.context.conversation_manager
        cid = await conv_mgr.get_curr_conversation_id(umo)
        if not cid:
            return ""
        conv = await conv_mgr.get_conversation(umo, cid)
        history = conv.history if conv else []
        if isinstance(history, str):
            history = json.loads(history or "[]")
        lines = []
        for message in reversed(history or []):
            text = _assistant_text(message)
            if not text:
                continue
            lines.append(text)
            if len(lines) >= 6:
                break
        lines.reverse()
        return "\n".join(f"- {line}" for line in lines)

    async def _load_history(self) -> tuple[list, str | None]:
        conv_mgr = self.context.conversation_manager
        cid = await conv_mgr.get_curr_conversation_id(BROTHER_SESSION)
        if not cid:
            return [], None
        conv = await conv_mgr.get_conversation(BROTHER_SESSION, cid)
        history = conv.history if conv else []
        if isinstance(history, str):
            history = json.loads(history or "[]")
        return history or [], cid

    async def _save_history(self, history: list, cid: str | None, text: str) -> None:
        conv_mgr = self.context.conversation_manager
        message = {"role": "assistant", "content": text}
        if cid:
            await conv_mgr.update_conversation(BROTHER_SESSION, cid, history=history + [message])
        else:
            await conv_mgr.new_conversation(
                BROTHER_SESSION, platform_id="qq_napcat", content=[message]
            )
