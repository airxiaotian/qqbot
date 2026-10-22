"""识图与语音转文字，供 AstrBot 插件调用。"""

import os
from io import BytesIO

VISION_MODEL = os.environ.get("QQBOT_VISION_MODEL", os.environ.get("QQBOT_MODEL_HEAVY", "claude-sonnet-4.6"))
VISION_PROMPT = (
    "用中文一两句说明图片内容和文字（若有）。这是 QQ 聊天里对方发来的图，"
    "只输出客观描述，不要寒暄，不要 markdown。"
)
WHISPER_MODEL = os.environ.get("QQBOT_WHISPER_MODEL", "whisper-1")


def describe_image(b64: str, get_client) -> str:
    raw = (b64 or "").strip()
    if raw.startswith("base64://"):
        raw = raw[9:]
    if not raw:
        return "（空的图片）"
    url = f"data:image/jpeg;base64,{raw}"
    client = get_client(VISION_MODEL)
    result = client.chat.completions.create(
        model=VISION_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_PROMPT},
                    {"type": "image_url", "image_url": {"url": url}},
                ],
            }
        ],
        max_tokens=300,
        temperature=0.2,
    )
    text = (result.choices[0].message.content or "").strip()
    return text or "（没能读出图里有什么）"


def transcribe_wav(wav_bytes: bytes, get_client) -> str:
    if not wav_bytes:
        return "（空的语音）"
    client = get_client(os.environ.get("QQBOT_MODEL", "claude-haiku-4.5"))
    if not hasattr(client, "audio") or not hasattr(client.audio, "transcriptions"):
        return "（语音转写未配置，需要支持 Whisper 的 API）"
    bio = BytesIO(wav_bytes)
    bio.name = "voice.wav"
    try:
        result = client.audio.transcriptions.create(model=WHISPER_MODEL, file=bio)
        text = getattr(result, "text", None) or str(result)
        return text.strip() or "（语音里没有听清文字）"
    except Exception as error:
        return f"（语音转写失败：{error}）"
