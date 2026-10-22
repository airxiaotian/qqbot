"""按"人"、"事"、"从网上学到的内容"和"说法"分类的长期记忆，每一条保存为一个 skill 风格的 SKILL.md。"""

import datetime
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CATEGORIES = {
    "people": "人",
    "events": "事",
    "study": "学到的",
    "voice": "说法",
    "persona": "人格",
}
CHAT_CATEGORIES = {"people", "events"}
PRIVATE_TAG = "（私聊）"
MAX_FACTS = 30
RECALL_SKILLS = 4
RECALL_FACTS = 8

EXTRACT_SYSTEM_PROMPT = """你在帮一个QQ聊天机器人"姐姐"整理长期记忆。她的弟弟是 QQ 273585129，对话里标成"弟弟(他的昵称)"；"你"是姐姐自己。
- 关于弟弟的一律 name 写"弟弟"、qq 写 273585129，别人用昵称叫他也算弟弟，昵称写进 aliases。
从【新对话】里找出值得长期记住的"人"和"事"，只输出一个 JSON 数组，不要输出任何别的文字。
- people：对话里出现或被提到的具体的人，包括弟弟本人、群友、他们提到的同事朋友家人。记身份、和弟弟的关系、性格、喜好、近况。
- events：具体的事情，比如上夜班、体检、旅行计划、考试、生病、搬家、某个游戏活动。记发生了什么、进展、时间。
- 只记录别人明确说过的事实。姐姐（"你"）自己说的话不算事实，不要记。
- 不记寒暄、玩笑、问好、没有信息量的内容。没有值得记的就输出 []。
- 【已有记忆】里标着“学到的”或“说法”的不要改写。已经有同一个人或同一件事时，name 用已有的名字，只写新增的事实，不要重复旧的。
- 每条事实一句话，写清主语，带上时间线索，比如"10月22日晚上又要上夜班"。
格式：[{"category": "people 或 events", "name": "名字", "aliases": ["别名"], "qq": "只有 people 且知道 QQ 时填", "description": "一句话概括", "facts": ["事实"]}]"""


@dataclass
class MemorySkill:
    category: str
    name: str
    description: str = ""
    aliases: list[str] = field(default_factory=list)
    qq: str = ""
    facts: list[str] = field(default_factory=list)
    updated: str = ""
    path: Path | None = None

    def keywords(self) -> list[str]:
        return [word for word in (self.name, *self.aliases) if len(word) >= 2]


class MemoryStore:
    def __init__(self, root: Path, brother_id: str) -> None:
        self.root = root
        self.brother_id = brother_id
        self.skills: list[MemorySkill] = []
        for category in CATEGORIES:
            for path in sorted((root / category).glob("*/SKILL.md")):
                skill = _parse(path, category)
                if skill:
                    self.skills.append(skill)

    def index(self) -> str:
        lines = []
        for skill in self.skills:
            extra = f"，QQ {skill.qq}" if skill.qq else ""
            aliases = "、".join(skill.aliases) or "无"
            lines.append(
                f"- [{CATEGORIES[skill.category]}] {skill.name}（别名：{aliases}{extra}）：{skill.description}"
            )
        return "\n".join(lines) or "（暂无）"

    def apply(self, update: dict, private: bool, allowed: set[str] | None = None) -> MemorySkill | None:
        category = update.get("category")
        name = str(update.get("name") or "").strip()
        allowed = CHAT_CATEGORIES if allowed is None else allowed
        if category not in allowed or category not in CATEGORIES or not name:
            return None
        aliases = [str(a).strip() for a in update.get("aliases") or [] if str(a).strip()]
        qq = str(update.get("qq") or "").strip() if category == "people" else ""
        facts = [str(f).strip() for f in update.get("facts") or [] if str(f).strip()]
        if category == "people" and (qq == self.brother_id or name == "弟弟"):
            if name != "弟弟":
                aliases.append(name)
            name, qq = "弟弟", self.brother_id

        skill = self._find(category, name, aliases, qq)
        if skill is None:
            skill = MemorySkill(category=category, name=name)
            self.skills.append(skill)
        if update.get("description"):
            skill.description = str(update["description"]).strip()
        for alias in [name, *aliases]:
            if alias != skill.name and alias not in skill.aliases:
                skill.aliases.append(alias)
        if qq:
            skill.qq = qq

        today = datetime.date.today().isoformat()
        tag = PRIVATE_TAG if private else ""
        for fact in facts:
            if any(fact in old for old in skill.facts):
                continue
            skill.facts.append(f"{today} {fact}{tag}")
        skill.facts = skill.facts[-MAX_FACTS:]
        skill.updated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        self._write(skill)
        return skill

    def recall(self, text: str, sender_qq: str) -> list[MemorySkill]:
        hits = [
            skill
            for skill in self.skills
            if skill.category not in {"voice", "persona"}
            and (
                (sender_qq and skill.qq == sender_qq)
                or any(keyword in text for keyword in skill.keywords())
            )
        ]
        hits.sort(key=lambda skill: skill.updated, reverse=True)
        return hits[:RECALL_SKILLS]

    def latest(self, category: str, limit: int) -> list[MemorySkill]:
        items = [skill for skill in self.skills if skill.category == category]
        items.sort(key=lambda skill: skill.updated, reverse=True)
        return items[:limit]

    def render(self, skills: list[MemorySkill], private: bool) -> str:
        blocks = []
        for skill in skills:
            facts = [f for f in skill.facts if private or not f.endswith(PRIVATE_TAG)]
            if not facts and not skill.description:
                continue
            extra = f"（QQ {skill.qq}）" if skill.qq else ""
            lines = [f"【{CATEGORIES[skill.category]}】{skill.name}{extra}：{skill.description}"]
            lines += [f"- {fact}" for fact in facts[-RECALL_FACTS:]]
            blocks.append("\n".join(lines))
        return "\n".join(blocks)

    def _find(self, category: str, name: str, aliases: list[str], qq: str) -> MemorySkill | None:
        for skill in self.skills:
            if skill.category != category:
                continue
            if qq and skill.qq == qq:
                return skill
            names = {skill.name, *skill.aliases}
            if name in names or names.intersection(aliases):
                return skill
        return None

    def _write(self, skill: MemorySkill) -> None:
        if skill.path is None:
            slug = re.sub(r'[\\/:*?"<>|\s]+', "_", skill.name)[:40] or "unnamed"
            skill.path = self.root / skill.category / slug / "SKILL.md"
        skill.path.parent.mkdir(parents=True, exist_ok=True)
        header = {
            "name": skill.name,
            "description": skill.description,
            "category": skill.category,
            "aliases": skill.aliases,
            "qq": skill.qq,
            "updated": skill.updated,
        }
        body = "\n".join(f"- {fact}" for fact in skill.facts)
        skill.path.write_text(
            "---\n"
            + yaml.safe_dump(header, allow_unicode=True, sort_keys=False)
            + f"---\n# {skill.name}\n\n{skill.description}\n\n## 记得的事\n{body}\n",
            encoding="utf-8",
        )


def parse_updates(text: str) -> list[dict]:
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return []
    return [item for item in data if isinstance(item, dict)]


def _parse(path: Path, category: str) -> MemorySkill | None:
    content = path.read_text(encoding="utf-8")
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None
    header = yaml.safe_load(parts[1]) or {}
    facts = []
    if "## 记得的事" in parts[2]:
        facts = [
            line[2:].strip()
            for line in parts[2].split("## 记得的事", 1)[1].splitlines()
            if line.startswith("- ")
        ]
    return MemorySkill(
        category=category,
        name=str(header.get("name") or path.parent.name),
        description=str(header.get("description") or ""),
        aliases=[str(a) for a in header.get("aliases") or []],
        qq=str(header.get("qq") or ""),
        facts=facts,
        updated=str(header.get("updated") or ""),
        path=path,
    )
