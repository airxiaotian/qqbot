"""姐姐的情绪。对着她说的话才加减分，分数大约四十分钟掉一半。"""

import re
import time

HALF_LIFE_SECONDS = 40 * 60
SHOW_AT = 3.0
_PRIORITY = ("抑郁", "伤心", "生气", "开心")

_RULES = (
    ("抑郁", 4, re.compile(r"抑郁|没意思|就这几句|改不了|永远都一样|没人要|提不起")),
    ("生气", 3, re.compile(r"嘲讽|死板|装傻|恶心|烦死|抖S|发烂|假的|机器人|滚")),
    ("伤心", 3, re.compile(r"难过|伤心|委屈|别理我|不想理你|冷落|丢下")),
    ("开心", 3, re.compile(r"喜欢你|爱你|想你|谢谢|最好了|真好|夸奖|开心|高兴")),
)
_SOOTHE = re.compile(r"道歉|对不起|抱歉|开过头|不该|心疼|别难过|我在呢")
_SPEAK = {
    "平静": "按平时的语气说话。",
    "开心": "轻快一点，可以轻轻打趣。不要变成一串感叹。",
    "生气": "句子短，冷淡，不陪笑，不骂人。对弟弟可以带一点委屈的气。对群友就拉开距离。",
    "伤心": "说得轻、说得少，少问。不要装成没事。",
    "抑郁": "提不起劲。回复更短，不接热闹，不讲道理。别人继续挤兑，就更沉，不要突然开朗。",
}


def concerns_her(text: str) -> bool:
    return "姐姐" in text or "拉普斯拉妖" in text


def _scores(state: dict) -> dict[str, float]:
    raw = state.get("mood_scores")
    scores = {name: 0.0 for name, _, _ in _RULES}
    if isinstance(raw, dict):
        for name in scores:
            try:
                scores[name] = float(raw.get(name) or 0)
            except (TypeError, ValueError):
                scores[name] = 0.0
    return scores


def update_mood(state: dict, text: str, *, personal: bool, now: float | None = None) -> str:
    """按这句话调整情绪。心情名字变了就返回「旧→新：原因」，否则返回空字符串。"""
    now = time.time() if now is None else now
    scores = _scores(state)
    last = float(state.get("mood_at") or now)
    factor = 0.5 ** (max(0.0, now - last) / HALF_LIFE_SECONDS)
    for name in scores:
        scores[name] *= factor
    before = state.get("mood") or "平静"
    reason = state.get("mood_reason") or ""
    if personal and text:
        hit = False
        for name, points, pattern in _RULES:
            if pattern.search(text):
                scores[name] += points
                reason = text.strip().replace("\n", " ")[:40]
                hit = True
        if _SOOTHE.search(text):
            scores["生气"] *= 0.35
            scores["抑郁"] *= 0.55
            scores["伤心"] *= 0.7
            if not hit:
                reason = text.strip().replace("\n", " ")[:40]
    # 几种情绪同时过线时，沉的那种盖过轻的：抑郁优先于伤心、生气、开心。
    mood = "平静"
    for name in _PRIORITY:
        if scores[name] >= SHOW_AT:
            mood = name
            break
    state["mood_scores"] = {name: round(value, 2) for name, value in scores.items()}
    state["mood"] = mood
    state["mood_reason"] = reason
    state["mood_at"] = now
    if mood == before:
        return ""
    return f"{before}→{mood}：{reason}"


def mood_note(state: dict) -> str:
    mood = state.get("mood") or "平静"
    scores = _scores(state)
    reason = state.get("mood_reason") or ""
    why = f"刚碰到的话：{reason}。" if reason and mood != "平静" else ""
    level = ""
    if mood != "平静":
        value = scores.get(mood, 0)
        if value >= 8:
            level = "，很明显"
        elif value >= 5:
            level = "，能看出来"
        else:
            level = "，有一点"
    return (
        f"你现在的情绪是{mood}{level}。{why}"
        "这是你自己的心情。爱弟弟这件事不变，但口气跟着情绪走。"
        "不要每句都报情绪的名字；对方问心情时再说。"
        + _SPEAK.get(mood, _SPEAK["平静"])
    )
