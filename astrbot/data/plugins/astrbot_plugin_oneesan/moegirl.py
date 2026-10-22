"""从萌娘百科的姐姐角色中提炼可用的人格特征。

只学习喜恶、观点、兴趣、习惯和相处方式，不把角色姓名、剧情或经历
冒充成姐姐自己的真实经历。
"""

import asyncio
import random
import re
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup

from .memory import MemoryStore, parse_updates

CATEGORY_URL = "https://zh.moegirl.org.cn/Category:%E5%A7%90%E5%A7%90"
BASE_URL = "https://zh.moegirl.org.cn/"
BATCH_SIZE = 8
MAX_PAGES = 240
MAX_PAGE_CHARS = 4500

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; oneesan-personality-study/1.0)",
    "Accept": "text/html,application/xhtml+xml",
}
_SECTIONS = ("喜好", "讨厌", "观点", "兴趣", "习惯", "相处方式")
_BAD_TRAIT_RE = re.compile(r"恋爱|接吻|色情|性行为|杀人|伤害|违法|血缘|结婚")

PERSONA_PROMPT = """你在帮QQ机器人“姐姐”从萌娘百科的姐姐角色样本中形成独立人格。
材料是虚构角色资料，只能提炼可迁移的性格倾向，绝不能把角色姓名、职业、能力、剧情、家人或遭遇写成她自己的经历。
只输出 JSON 数组，每项格式：
{"category":"persona","name":"姐姐人格-喜好","aliases":[],"description":"一句概括","facts":["第一人称、具体、可长期保持的特征"]}

name 只能是以下六个之一：姐姐人格-喜好、姐姐人格-讨厌、姐姐人格-观点、姐姐人格-兴趣、姐姐人格-习惯、姐姐人格-相处方式。
要求：
- 选择彼此协调、适合温柔但有主见的姐姐的内容，不要把所有角色特点都堆上去。
- facts 必须以“我”开头，是日常聊天能自然表现出来的具体倾向。
- 不学习恋爱、色情、暴力、违法、超能力、职业技能、外貌、服装和虚构剧情。
- 不写“我喜欢弟弟”这种已有设定；重点补充她自己的那一格。
- 每类最多 3 条，材料不足的类别不输出；不要重复【已有特征】。
- 材料只是样本，不要照抄句子。
"""


async def learn_moegirl_persona(
    store: MemoryStore,
    ask,
    seen: list[str],
    topic: str = "",
) -> list[str]:
    """读取一批未学习角色，提炼人格并返回本次看过的 URL。"""
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, headers=_HEADERS) as session:
        urls = await _category_urls(session)
        candidates = [url for url in urls if url not in set(seen)]
        random.shuffle(candidates)
        selected = candidates[:BATCH_SIZE]
        materials = await asyncio.gather(
            *(_page_material(session, url) for url in selected),
            return_exceptions=True,
        )

    pages = [
        material
        for material in materials
        if isinstance(material, str) and len(material) >= 120
    ]
    if not pages:
        return []

    existing = store.render(store.latest("persona", 6), private=True)
    focus = f"这次优先提炼和「{topic.strip()[:40]}」有关、并且适合她的特征。\n" if topic.strip() else ""
    prompt = (
        focus
        + "【已有特征】\n"
        + (existing or "无")
        + "\n\n【角色样本】\n"
        + "\n\n".join(pages)
    )
    updates = parse_updates(await ask(prompt, PERSONA_PROMPT))
    _save_persona(store, updates, selected)
    return selected


async def _category_urls(session: aiohttp.ClientSession) -> list[str]:
    async with session.get(CATEGORY_URL) as response:
        response.raise_for_status()
        page = await response.text()
    soup = BeautifulSoup(page, "lxml")
    container = soup.select_one("#mw-pages") or soup
    urls = []
    for anchor in container.select("a[href]"):
        href = anchor.get("href") or ""
        if not href.startswith("/") or ":" in href or href.startswith("//"):
            continue
        url = urljoin(BASE_URL, href)
        if url not in urls:
            urls.append(url)
        if len(urls) >= MAX_PAGES:
            break
    return urls


async def _page_material(session: aiohttp.ClientSession, url: str) -> str:
    async with session.get(url) as response:
        response.raise_for_status()
        page = await response.text()
    soup = BeautifulSoup(page, "lxml")
    title = soup.select_one("#firstHeading")
    content = soup.select_one(".mw-parser-output")
    if content is None:
        return ""
    for node in content.select(
        "script,style,table,.navbox,.toc,.mw-editsection,.reference,.references"
    ):
        node.decompose()
    # 萌娘百科会把正文标成 TemplateString；BeautifulSoup 默认的
    # get_text 会忽略它，所以这里明确读取所有字符串节点。
    text = re.sub(r"\s+", " ", content.get_text(" ", strip=True, types=None))
    name = (
        title.get_text(" ", strip=True, types=None)
        if title
        else (soup.title.get_text(strip=True, types=None).split(" - 萌娘百科", 1)[0] if soup.title else url)
    )
    return f"角色：{name}\n资料：{text[:MAX_PAGE_CHARS]}"


def _save_persona(store: MemoryStore, updates: list[dict], sources: list[str]) -> int:
    saved = 0
    source_note = "参考萌娘百科姐姐角色样本提炼，不代表对应角色经历"
    for update in updates:
        name = str(update.get("name") or "")
        if name not in {f"姐姐人格-{section}" for section in _SECTIONS}:
            continue
        facts = []
        for value in update.get("facts") or []:
            fact = re.sub(r"\s+", " ", str(value)).strip()
            if (
                fact.startswith("我")
                and len(fact) <= 60
                and not _BAD_TRAIT_RE.search(fact)
            ):
                facts.append(fact)
        if not facts:
            continue
        update["category"] = "persona"
        update["aliases"] = []
        update["facts"] = facts + [source_note]
        if store.apply(update, private=False, allowed={"persona"}):
            saved += len(facts)
    return saved
