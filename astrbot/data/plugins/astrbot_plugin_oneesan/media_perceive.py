"""向本机 proxy 请求识图 / 识语音。"""

import base64
import os

import aiohttp

PERCEIVE_URL = os.environ.get("QQBOT_PERCEIVE_URL", "http://host.docker.internal:8765/v1/perceive")


async def perceive(kind: str, data: bytes, timeout: float = 45) -> str:
    payload = {
        "kind": kind,
        "data": base64.b64encode(data).decode("ascii"),
    }
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as session:
            async with session.post(PERCEIVE_URL, json=payload) as response:
                body = await response.json(content_type=None)
    except Exception as error:
        return f"（{kind}识别失败：{error}）"
    if not isinstance(body, dict) or not body.get("ok"):
        return str((body or {}).get("error") or f"（{kind}识别失败）")
    return str(body.get("text") or "").strip() or f"（{kind}没有结果）"
