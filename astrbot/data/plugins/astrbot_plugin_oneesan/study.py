"""从 B 站学一条内容，整理成 study 类 skill。不下载视频。"""

import html
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote

import aiohttp

from .memory import MemoryStore, parse_updates

_PLUGINS = Path(__file__).resolve().parents[1]
if str(_PLUGINS) not in sys.path:
    sys.path.insert(0, str(_PLUGINS))

from astrbot_plugin_listen_music.core.bilibili import BilibiliClient  # noqa: E402

_VIEW_URL = "https://api.bilibili.com/x/web-interface/wbi/view"
_PLAYER_URL = "https://api.bilibili.com/x/player/wbi/v2"
_POPULAR_URL = "https://api.bilibili.com/x/web-interface/popular?ps=20&pn=1"
_TAG_RE = re.compile(r"<[^>]*>")
_SKIP_TITLE_RE = re.compile(r"遇难|死亡|战争|军事|车祸|遗体")
_SUBTITLE_CHARS = 1500

PICK_PROMPT = """从下面这些弟弟在乎的事里，挑一个适合去B站搜索学习的具体主题。
选作品、歌曲、游戏、食物、爱好，不要选情绪、上班、吵架、吃饭睡觉这种日常。
已经学过的主题不要再选。没有合适的就让 query 为空。
只输出 JSON：{"query": "搜索词"}"""

LEARN_PROMPT = """你在把一条B站视频整理成聊天时能用的知识。只输出一个 JSON 数组，不要输出别的文字。
材料里没有的不要编。标题里出现的作品名、歌曲名、演唱者、游戏名必须写成 fact。简介和字幕里多出来的具体信息再追加，最多 6 条。
- category 固定是 study
- name 用主题的短名字，不要照抄整句标题
- aliases 写别人聊天时可能提到的叫法
- description 一句话
格式：[{"category":"study","name":"名字","aliases":["别名"],"description":"一句话","facts":["事实"]}]"""


async def learn_once(store: MemoryStore, ask, seen: list[str], brief: str, query: str = "") -> str:
    """学一条。成功时返回 bvid，没有可学的内容时返回空字符串。"""
    seen_set = set(seen)
    async with aiohttp.ClientSession() as session:
        client = BilibiliClient(session)
        query = query.strip()[:40] or await _pick_query(ask, brief, store)
        lessons = await _search_lessons(client, session, query, seen_set) if query else []
        if query and not any(len(item["desc"]) + len(item["subtitle"]) >= 80 for item in lessons):
            lessons.extend(await _search_lessons(client, session, f"{query} 介绍", seen_set))
        if not lessons:
            popular = await _popular_lesson(client, session, seen_set)
            if popular:
                lessons.append(popular)
        unique = []
        seen_ids = set()
        for lesson in lessons:
            if lesson["bvid"] in seen_ids:
                continue
            seen_ids.add(lesson["bvid"])
            unique.append(lesson)
        for lesson in unique[:3]:
            if _save_lesson(store, lesson, parse_updates(await ask(_lesson_prompt(lesson), LEARN_PROMPT))):
                return lesson["bvid"]
        return ""


async def _pick_query(ask, brief: str, store: MemoryStore) -> str:
    learned = "、".join(skill.name for skill in store.skills if skill.category == "study") or "无"
    text = await ask(f"【已经学过】{learned}\n【弟弟在乎的事】\n{brief or '（没有）'}", PICK_PROMPT)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return ""
    try:
        query = json.loads(text[start : end + 1]).get("query") or ""
    except ValueError:
        return ""
    return str(query).strip()[:40]


def _lesson_prompt(lesson: dict) -> str:
    return (
        f"标题：{lesson['title']}\nUP主：{lesson['owner']}\n分区：{lesson['tname']}\n"
        f"简介：{lesson['desc'] or '（无）'}\n字幕摘录：{lesson['subtitle'] or '（无）'}"
    )


def _save_lesson(store: MemoryStore, lesson: dict, updates: list[dict]) -> bool:
    source = (
        f"从B站视频《{lesson['title']}》学到的，BV号 {lesson['bvid']}，UP主 {lesson['owner']}。"
        "只看了简介和字幕，没有看完视频"
    )
    saved = False
    for update in updates:
        facts = [str(fact).strip() for fact in update.get("facts") or [] if str(fact).strip()]
        if not facts:
            continue
        facts.append(source)
        update["facts"] = facts
        update["category"] = "study"
        if store.apply(update, private=False, allowed={"study"}):
            saved = True
    return saved


async def _search_lessons(client: BilibiliClient, session, query: str, seen: set[str]) -> list[dict]:
    if not query:
        return []
    hits = await client.search_videos(query, limit=5)
    lessons = []
    for hit in hits:
        if hit.bvid in seen or _SKIP_TITLE_RE.search(hit.title):
            continue
        lesson = await _lesson_from_view(client, session, hit.bvid)
        if lesson:
            lessons.append(lesson)
    lessons.sort(key=lambda item: len(item["desc"]) + len(item["subtitle"]), reverse=True)
    return lessons


async def _popular_lesson(client: BilibiliClient, session, seen: set[str]) -> dict | None:
    async with session.get(_POPULAR_URL, headers=_headers(), timeout=20) as response:
        payload = await response.json(content_type=None)
    for item in (payload.get("data") or {}).get("list") or []:
        bvid = str(item.get("bvid") or "")
        title = _plain(item.get("title"))
        if not bvid or bvid in seen or _SKIP_TITLE_RE.search(title):
            continue
        lesson = await _lesson_from_view(client, session, bvid)
        if lesson:
            return lesson
    return None


async def _lesson_from_view(client: BilibiliClient, session, bvid: str) -> dict | None:
    data = await client._wbi_data(_VIEW_URL, {"bvid": bvid})
    title = _plain(data.get("title"))
    if not title:
        return None
    cid = data.get("cid")
    subtitle = ""
    if cid:
        subtitle = await _subtitle_excerpt(client, session, bvid, cid)
    owner = data.get("owner") or {}
    return {
        "bvid": bvid,
        "title": title,
        "owner": _plain(owner.get("name")) or "未知",
        "tname": _plain(data.get("tname")),
        "desc": _plain(data.get("desc"))[:800],
        "subtitle": subtitle,
        "aid": data.get("aid") or 0,
    }


async def _subtitle_excerpt(client: BilibiliClient, session, bvid: str, cid) -> str:
    try:
        player = await client._wbi_data(_PLAYER_URL, {"bvid": bvid, "cid": cid})
    except Exception:
        return ""
    subtitles = ((player.get("subtitle") or {}).get("subtitles") or [])
    if not subtitles:
        return ""
    url = str(subtitles[0].get("subtitle_url") or "")
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith("https://"):
        return ""
    async with session.get(url, headers=_headers(), timeout=20) as response:
        payload = await response.json(content_type=None)
    parts = []
    total = 0
    for line in payload.get("body") or []:
        content = _plain(line.get("content"))
        if not content:
            continue
        parts.append(content)
        total += len(content)
        if total >= _SUBTITLE_CHARS:
            break
    return "\n".join(parts)


def _headers() -> dict[str, str]:
    return {
        "User-Agent": BilibiliClient.USER_AGENT,
        "Referer": "https://www.bilibili.com/",
        "Accept": "application/json, text/plain, */*",
    }


def _plain(value) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub("", html.unescape(str(value or "")))).strip()


_VOICE_QUERIES = ("朋友闲聊 怎么接话",)
_SKIP_VOICE_TITLE_RE = re.compile(r"教程|安装|桌宠|评测|开箱|配音|恋爱|追女|追男|表白|情话|撩妹|撩汉|机器人|学习资料")
_BAD_VOICE_RE = re.compile(r"咋|本鱼|这条鱼|小鱼|干嘛|教程|安装|桌宠|喜欢你|表白|恋爱")
_XHS_SEARCH = "https://www.xiaohongshu.com/search_result?keyword={keyword}&source=web_search_result_notes"
_XHS_API = "https://edith.xiaohongshu.com/api/sns/web/v1/search/notes"
_REPLY_URL = "https://api.bilibili.com/x/v2/reply?type=1&oid={aid}&sort=2&ps=8"

VOICE_PROMPT = """你在帮一个QQ上的姐姐学习说话，让她不要总用同样几句。材料来自B站或小红书。只输出一个 JSON 数组。
她是在和弟弟、朋友闲聊，不是恋爱话术。禁止表白，禁止"喜欢你"。
自称只能是"我"或"姐姐"。不要学视频角色的自称，不要出现鱼、桌宠、安装、教程。
3 条短句必须是三种不同接法，分别落到游戏、歌、工作这类不同话题上。不要照抄原文，不要粗口，不要"咋了""干嘛"。
- category 固定 voice
- name 是这种说法的短名字
- aliases 留空数组
- description 一句话，说明什么时候用
- facts 写 3 条不同话题的短句，外加 1 条以"少用："开头。少用里不要禁止"我"。
材料太空，或材料只是在讲某个产品怎么安装，就输出 []。
格式：[{"category":"voice","name":"名字","aliases":[],"description":"一句话","facts":["短句","短句","短句","少用：……"]}]"""


async def learn_voice(store: MemoryStore, ask, seen: list[str]) -> dict:
    """学一种说法。saved 是记下来的来源 id，xhs 是小红书这次的结果说明。"""
    seen_set = set(seen)
    query = _VOICE_QUERIES[len(seen) % len(_VOICE_QUERIES)]
    saved: list[str] = []
    async with aiohttp.ClientSession() as session:
        client = BilibiliClient(session)
        lessons = [
            lesson
            for lesson in await _search_lessons(client, session, query, seen_set)
            if not _SKIP_VOICE_TITLE_RE.search(lesson["title"])
        ]
        lessons.sort(
            key=lambda lesson: sum(word in lesson["title"] for word in ("接话", "闲聊", "情绪", "眼前")),
            reverse=True,
        )
        for lesson in lessons:
            comments = await _comments(session, lesson.get("aid"))
            material = _lesson_prompt(lesson) + "\n评论：\n" + ("\n".join(comments) or "（无）")
            if _save_voice(store, "B站", lesson["bvid"], lesson["title"], parse_updates(await ask(material, VOICE_PROMPT))):
                saved.append(lesson["bvid"])
                break
        notes, xhs = await _xhs_notes(session, query)
        if notes:
            material = "\n\n".join(f"标题：{note['title']}\n正文：{note['desc']}" for note in notes[:3])
            note_id = notes[0]["id"]
            if _save_voice(store, "小红书", note_id, notes[0]["title"], parse_updates(await ask(material, VOICE_PROMPT))):
                saved.append(note_id)
    return {"saved": saved, "xhs": xhs, "query": query}


def _save_voice(store: MemoryStore, site: str, source_id: str, title: str, updates: list[dict]) -> bool:
    source = f"从{site}学到的说法，来源 {source_id}《{title}》。换成自己的话，不要原句照搬"
    saved = False
    for update in updates:
        facts = [
            str(fact).strip()
            for fact in update.get("facts") or []
            if str(fact).strip() and not _BAD_VOICE_RE.search(str(fact))
        ]
        if len(facts) < 2 or _BAD_VOICE_RE.search(str(update.get("name") or "")):
            continue
        facts.append(source)
        update["facts"] = facts
        update["category"] = "voice"
        update["aliases"] = []
        if store.apply(update, private=False, allowed={"voice"}):
            saved = True
    return saved


async def _comments(session, aid) -> list[str]:
    if not aid:
        return []
    try:
        async with session.get(_REPLY_URL.format(aid=aid), headers=_headers(), timeout=20) as response:
            payload = await response.json(content_type=None)
    except Exception:
        return []
    lines = []
    for reply in (payload.get("data") or {}).get("replies") or []:
        message = _plain((reply.get("content") or {}).get("message"))
        if len(message) >= 4:
            lines.append(message[:80])
    return lines[:8]


async def _xhs_notes(session, keyword: str) -> tuple[list[dict], str]:
    headers = {
        "User-Agent": BilibiliClient.USER_AGENT,
        "Referer": "https://www.xiaohongshu.com/",
        "Accept": "text/html,application/json",
    }
    url = _XHS_SEARCH.format(keyword=quote(keyword))
    try:
        async with session.get(url, headers=headers, timeout=20) as response:
            html_text = await response.text()
    except Exception as error:
        return [], f"页面打不开：{error}"
    notes = _notes_from_state(html_text)
    if notes:
        return notes, f"从页面读到 {len(notes)} 条"
    try:
        async with session.post(
            _XHS_API,
            json={"keyword": keyword, "page": 1, "page_size": 10, "sort": "general", "note_type": 0},
            headers={**headers, "Origin": "https://www.xiaohongshu.com", "Content-Type": "application/json"},
            timeout=20,
        ) as response:
            payload = await response.json(content_type=None)
    except Exception as error:
        return [], f"接口打不开：{error}"
    if payload.get("code") != 0:
        return [], str(payload.get("msg") or "没有返回笔记")
    items = ((payload.get("data") or {}).get("items") or [])
    notes = []
    for item in items:
        card = item.get("note_card") or item
        title = _plain(card.get("display_title") or card.get("title"))
        desc = _plain(card.get("desc"))
        note_id = str(item.get("id") or card.get("note_id") or "")
        if title and note_id:
            notes.append({"id": note_id, "title": title, "desc": desc[:400]})
    if not notes:
        return [], "接口没有笔记正文"
    return notes, f"从接口读到 {len(notes)} 条"


def _notes_from_state(html_text: str) -> list[dict]:
    match = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*\})</script>", html_text)
    if not match:
        return []
    try:
        state = json.loads(match.group(1).replace("undefined", "null"))
    except ValueError:
        return []
    feeds = (state.get("search") or {}).get("feeds") or []
    notes = []
    for item in feeds:
        card = item.get("noteCard") or item.get("note_card") or {}
        title = _plain(card.get("displayTitle") or card.get("title"))
        note_id = str(item.get("id") or card.get("noteId") or "")
        if title and note_id:
            notes.append({"id": note_id, "title": title, "desc": _plain(card.get("desc"))[:400]})
    return notes
