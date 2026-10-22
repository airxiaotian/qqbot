"""技术问题才查公开网页。查到链接就给链接，没查到再按公开常识回答。"""

import re
import urllib.parse
import urllib.request

_LINK_RE = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def search_snippets(query: str, timeout: float = 4.0) -> str:
    text = query or ""
    if "【这句】" in text:
        text = text.split("【这句】")[-1]
    text = re.sub(r"^\[[^\]]*\]\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip()[:80]
    if not text:
        return ""
    data = urllib.parse.urlencode({"q": text}).encode()
    request = urllib.request.Request(
        "https://lite.duckduckgo.com/lite/",
        data=data,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            page = response.read().decode("utf-8", "replace")
    except Exception:
        return ""
    lines = []
    for href, label in _LINK_RE.findall(page):
        if "duckduckgo.com" in href or href.startswith("/"):
            continue
        title = _TAG_RE.sub("", label)
        title = re.sub(r"\s+", " ", title).strip()
        if len(title) < 4:
            title = href
        lines.append(f"- {title}" if title == href else f"- {title} {href}")
        if len(lines) >= 3:
            break
    if not lines:
        return ""
    return (
        "【查到的公开资料。回答时先给出其中的链接。"
        "资料没写到的部分可以补充公开常识，并说明那部分不是从链接里看到的】\n"
        + "\n".join(lines)
    )
