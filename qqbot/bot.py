"""A tiny QQ bot with a friendly oneesan persona."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class Reply:
    """Response produced by a bot."""

    text: str
    user: Optional[str] = None
    sender: str = "oneesan"
    raw_message: Optional[str] = None

    @property
    def content(self) -> str:
        """Compatibility alias for the reply text."""
        return self.text

    def __str__(self) -> str:
        return self.text


def default_reply(message: Any = "") -> str:
    """Return a friendly default response in a oneesan-like style."""

    text = "" if message is None else str(message).strip()
    if not text:
        return "はい、oneesanがあなたの話を聞いていますよ〜"
    if text.endswith("?"):
        return f"ええ、{text[:-1]}ってことですね。oneesanがしっかり応援します〜"
    return f"はいはい、{text}ですね。oneesanが一緒に考えますよ〜"


class Bot:
    """Minimal QQ bot implementation."""

    def __init__(self, name: str = "oneesan", greeting: str = "おかえりなさい〜") -> None:
        self.name = name
        self.greeting = greeting

    def reply(self, message: Any, user: Optional[str] = None) -> Reply:
        return Reply(
            text=default_reply(message),
            user=user,
            sender=self.name,
            raw_message="" if message is None else str(message),
        )

    def __call__(self, message: Any, user: Optional[str] = None) -> Reply:
        return self.reply(message, user=user)

    def send(self, message: Any, user: Optional[str] = None) -> Reply:
        return self.reply(message, user=user)

    def receive(self, message: Any, user: Optional[str] = None) -> Reply:
        return self.reply(message, user=user)

    def respond(self, message: Any, user: Optional[str] = None) -> Reply:
        return self.reply(message, user=user)

    def get_greeting(self) -> str:
        return self.greeting
