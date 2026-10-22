"""调用本机 GPT-SoVITS api_v2（默认 9880 /tts）。"""

import json
import os
from pathlib import Path

import aiohttp

_DEFAULT_REF = (
    "/Users/tciuser1/qqbot/astrbot/data/plugin_data/astrbot_plugin_oneesan/sovits/ref.wav"
)
SOVITS_URL = os.environ.get("QQBOT_SOVITS_URL", "http://host.docker.internal:9880/tts").rstrip("/")
REF_AUDIO = os.environ.get("QQBOT_SOVITS_REF_AUDIO", _DEFAULT_REF)
def _load_prompt_text() -> str:
    from_env = os.environ.get("QQBOT_SOVITS_PROMPT_TEXT", "").strip()
    if from_env:
        return from_env
    sidecar = Path(REF_AUDIO).with_suffix(".txt")
    if sidecar.is_file():
        return sidecar.read_text(encoding="utf-8").strip()
    return ""


PROMPT_TEXT = _load_prompt_text()
PROMPT_LANG = os.environ.get("QQBOT_SOVITS_PROMPT_LANG", "zh")
TEXT_LANG = os.environ.get("QQBOT_SOVITS_TEXT_LANG", "zh")
SPEED = float(os.environ.get("QQBOT_SOVITS_SPEED", "1.0"))


def payload(text: str) -> dict:
    body = {
        "text": text.strip()[:500],
        "text_lang": TEXT_LANG,
        "ref_audio_path": REF_AUDIO,
        "prompt_lang": PROMPT_LANG,
        "prompt_text": PROMPT_TEXT,
        "text_split_method": "cut5",
        "batch_size": 1,
        "media_type": "wav",
        "streaming_mode": False,
        "speed_factor": SPEED,
    }
    return body


async def synthesize_to_file(text: str, wav_path: Path, timeout: float = 120) -> str | None:
    """成功返回 wav_path，失败返回 None。"""
    if not text.strip():
        return None
    if not REF_AUDIO:
        return None
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as session:
            async with session.post(SOVITS_URL, json=payload(text)) as response:
                raw = await response.read()
                if response.status != 200:
                    detail = raw.decode("utf-8", "replace")[:300]
                    try:
                        detail = json.loads(raw).get("message") or json.loads(raw).get("detail") or detail
                    except (ValueError, TypeError):
                        pass
                    raise RuntimeError(f"SoVITS {response.status}: {detail}")
    except Exception:
        raise
    if len(raw) < 128:
        raise RuntimeError("SoVITS 返回的音频太短")
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    wav_path.write_bytes(raw)
    return str(wav_path)
