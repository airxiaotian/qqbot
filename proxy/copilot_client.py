"""直连 GitHub Copilot API（OpenAI 兼容），不依赖 Hermes 运行时。"""

import logging
import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

logger = logging.getLogger(__name__)

COPILOT_BASE_URL = os.environ.get(
    "QQBOT_COPILOT_BASE_URL", "https://api.githubcopilot.com/v1"
)
_CLASSIC_PAT_PREFIX = "ghp_"
_ENV_VARS = ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")

_REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / ".env")


def _validate_token(token: str) -> bool:
    token = token.strip()
    if not token or token.startswith(_CLASSIC_PAT_PREFIX):
        return False
    return True


def _gh_token() -> str:
    gh = os.environ.get("GH_PATH") or "gh"
    try:
        out = subprocess.run(
            [gh, "auth", "token"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    token = (out.stdout or "").strip()
    return token if _validate_token(token) else ""


def resolve_copilot_token() -> str:
    for name in _ENV_VARS:
        token = os.getenv(name, "").strip()
        if _validate_token(token):
            logger.info("Copilot token 来自 %s", name)
            return token
    token = _gh_token()
    if token:
        logger.info("Copilot token 来自 gh auth token")
        return token
    return ""


def copilot_headers(*, vision: bool = False) -> dict[str, str]:
    headers = {
        "Editor-Version": "vscode/1.104.1",
        "User-Agent": "qqbot-proxy/1.0",
        "Copilot-Integration-Id": "vscode-chat",
        "Openai-Intent": "conversation-edits",
        "x-initiator": "agent",
    }
    if vision:
        headers["Copilot-Vision-Request"] = "true"
    return headers


_clients: dict[bool, OpenAI] = {}


def get_openai_client(*, vision: bool = False) -> OpenAI:
    token = resolve_copilot_token()
    if not token:
        raise RuntimeError(
            "没有 Copilot 令牌。在 qqbot/.env 里设置 GITHUB_TOKEN（gho_ 或 github_pat_），"
            "或 COPILOT_GITHUB_TOKEN / GH_TOKEN，或先 gh auth login。"
        )
    if vision not in _clients:
        _clients[vision] = OpenAI(
            api_key=token,
            base_url=COPILOT_BASE_URL.rstrip("/"),
            default_headers=copilot_headers(vision=vision),
        )
    return _clients[vision]


def _messages_need_vision(messages: list) -> bool:
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                return True
    return False


def chat_completions_create(model: str, messages: list, **kwargs):
    client = get_openai_client(vision=_messages_need_vision(messages))
    return client.chat.completions.create(model=model, messages=messages, **kwargs)
