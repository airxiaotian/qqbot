"""
本地 OpenAI 兼容代理
直连 GitHub Copilot API，把人设、完整聊天记录和消息时间一起发给模型。
"""

import base64
import os
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging

from dialog import (
    LIGHT_MODEL,
    PERSONA,
    assemble_context,
    choose_model,
    choose_strategy,
    commit_turn,
    in_group,
    load_state,
    needs_search,
    plain_text,
    quality_issues,
    rewrite_note,
    save_state,
    strategy_block,
)
from learn import apply_rule, group_judge_messages, load_rules, parse_group_judgment, propose_rule
from search import search_snippets
from copilot_client import chat_completions_create
from perception import describe_image, transcribe_wav

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

HISTORY_MESSAGES = 30
MAX_TOKENS = 220
TEMPERATURE = 0.9
# 插件整理记忆时使用的模型名，代理收到后不套人设
MEMORY_MODEL = "oneesan-memory"

_REMINDER_RE = re.compile(r"<system_reminder>(.*?)</system_reminder>", re.S)
_DATETIME_RE = re.compile(r"Current datetime:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})")
# 模型偶尔会模仿消息前缀，或把自己的判断写成 [在群里...] 放在回复开头
_ECHOED_META_RE = re.compile(r"^\s*[\[【][^\]】\n]*[\]】]\s*")
# 模型偶尔先写一段分析，再用单独一行 --- 隔开真正的回复
_REASONING_SPLIT_RE = re.compile(r"\n\s*-{3,}\s*\n")
def call_model_sync(messages: list, max_tokens: int = MAX_TOKENS, temperature: float = TEMPERATURE, model: str = LIGHT_MODEL):
    try:
        return chat_completions_create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    except Exception as error:
        logger.warning(f"模型 {model} 调用失败，重试 {LIGHT_MODEL}: {error}")
        if model == LIGHT_MODEL:
            raise
        return chat_completions_create(
            model=LIGHT_MODEL,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )


def flatten_content(content) -> str:
    """把 OpenAI 格式的 content 转成纯文本，时间提醒转成 [YYYY-MM-DD HH:MM] 前缀"""
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif item.get("type") == "image_url":
                    parts.append("[图片]")
            elif isinstance(item, str):
                parts.append(item)
        text = "\n".join(parts)
    else:
        text = content or ""

    meta = []
    for reminder in _REMINDER_RE.findall(text):
        for line in reminder.splitlines():
            line = line.strip()
            match = _DATETIME_RE.search(line)
            if match:
                meta.insert(0, match.group(1))
            elif line:
                meta.append(line)
    text = _REMINDER_RE.sub("", text).strip()
    if not meta:
        return text
    return f"[{' | '.join(meta)}]\n【这句】{text}"


def build_messages(messages: list) -> list:
    """丢弃 AstrBot 自带的 system prompt 和工具调用消息，只保留对话"""
    dialog = []
    for msg in messages:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        text = flatten_content(msg.get("content"))
        if not text:
            continue
        if dialog and dialog[-1]["role"] == role:
            dialog[-1]["content"] += "\n" + text
        else:
            dialog.append({"role": role, "content": text})

    dialog = dialog[-HISTORY_MESSAGES:]
    if dialog and dialog[0]["role"] != "user":
        dialog.insert(0, {"role": "user", "content": "（之前的聊天）"})
    return dialog


def build_raw_messages(messages: list) -> list:
    """记忆整理用：不套人设，原样保留 system 和 user"""
    raw = []
    for msg in messages:
        if msg.get("role") in ("system", "user"):
            text = flatten_content(msg.get("content"))
            if text:
                raw.append({"role": msg["role"], "content": text})
    return raw


def _clean_reply(result) -> str:
    content = _REASONING_SPLIT_RE.split(result.choices[0].message.content or "")[-1]
    return _ECHOED_META_RE.sub("", content).strip()


# 线程池用于运行同步的模型调用
executor = ThreadPoolExecutor(max_workers=4)


@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {"id": "hermes-oneesan", "object": "model", "created": 1700000000, "owned_by": "copilot"},
        ]
    }


@app.post("/v1/perceive")
async def perceive_media(request: Request):
    """插件识图 / 识语音。body: {kind: image|audio, data: base64}"""
    try:
        body = await request.json()
        kind = str(body.get("kind") or "").strip().lower()
        raw = body.get("data") or ""
        if not isinstance(raw, str) or not raw.strip():
            return JSONResponse({"ok": False, "error": "data 无效"}, status_code=400)
        blob = base64.b64decode(raw)
        loop = asyncio.get_event_loop()
        if kind == "image":
            text = await loop.run_in_executor(executor, lambda: describe_image(raw))
        elif kind == "audio":
            text = await loop.run_in_executor(executor, lambda: transcribe_wav(blob))
        else:
            return JSONResponse({"ok": False, "error": "kind 必须是 image 或 audio"}, status_code=400)
        return {"ok": True, "text": text}
    except Exception as error:
        logger.exception("perceive 失败")
        return JSONResponse({"ok": False, "error": str(error)}, status_code=500)


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    try:
        body = await request.json()
        loop = asyncio.get_event_loop()

        if body.get("model") == MEMORY_MODEL:
            messages = build_raw_messages(body.get("messages", []))
            logger.info(f"记忆整理请求（{len(messages)} 条）")
            result = await loop.run_in_executor(executor, lambda: call_model_sync(messages, 1500, 0.2, LIGHT_MODEL))
            response_text = (result.choices[0].message.content or "").strip()
        else:
            dialog = build_messages(body.get("messages", []))
            if not dialog:
                logger.error("没有找到用户消息")
                return JSONResponse({"error": "没有找到用户消息"}, status_code=400)

            state = load_state()
            strategy = choose_strategy(dialog[-1]["content"], state)
            model = choose_model(dialog[-1]["content"], strategy)
            found = ""
            if needs_search(dialog[-1]["content"]):
                found = await loop.run_in_executor(
                    executor, search_snippets, plain_text(dialog[-1]["content"])
                )
            rule = propose_rule(dialog[-1]["content"])
            learned_note = ""
            if rule:
                learned_note = apply_rule(rule, "私聊")
                if learned_note:
                    logger.info(f"已备份并写入私聊规则: {rule}")
            system = PERSONA + "\n" + load_rules() + "\n" + strategy_block(strategy, state)
            if learned_note:
                system += "\n" + learned_note
            if found:
                system += "\n" + found
            prompt_messages = [{"role": "system", "content": system}, *assemble_context(dialog)]
            logger.info(f"策略 {strategy} 模型 {model} 用户消息: {dialog[-1]['content'][:80]}")

            result = await loop.run_in_executor(
                executor, lambda: call_model_sync(prompt_messages, MAX_TOKENS, TEMPERATURE, model)
            )
            response_text = _clean_reply(result)
            issues = quality_issues(response_text, dialog[-1]["content"], dialog, state, strategy)
            if issues:
                logger.info(f"回复未通过检查，重写一次: {issues} 草稿: {response_text}")
                retry_messages = [
                    {"role": "system", "content": system + "\n" + rewrite_note(issues, response_text)},
                    *assemble_context(dialog),
                ]
                result = await loop.run_in_executor(
                    executor, lambda: call_model_sync(retry_messages, MAX_TOKENS, TEMPERATURE, model)
                )
                response_text = _clean_reply(result)
            if in_group(dialog[-1]["content"]):
                try:
                    judge_messages = group_judge_messages(dialog, response_text)
                    judged = await loop.run_in_executor(
                        executor, lambda: call_model_sync(judge_messages, 60, 0.2, LIGHT_MODEL)
                    )
                    own_rule = parse_group_judgment(
                        _clean_reply(judged), plain_text(dialog[-1]["content"])
                    )
                    if own_rule and apply_rule(own_rule, "自己"):
                        logger.info(f"群里自己决定改一条: {own_rule}")
                except Exception as judge_error:
                    logger.warning(f"群里自我判断失败: {judge_error}")
            save_state(commit_turn(state, dialog[-1]["content"], strategy, response_text))

        logger.info(f"AI 响应: {response_text}")

        usage = result.usage
        return {
            "id": "chatcmpl-hermes",
            "object": "chat.completion",
            "created": 1700000000,
            "model": "hermes-oneesan",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": response_text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                "completion_tokens": getattr(usage, "completion_tokens", 0),
                "total_tokens": getattr(usage, "total_tokens", 0),
            },
        }
    except Exception as e:
        logger.error(f"处理请求时出错: {str(e)}", exc_info=True)
        return JSONResponse({"error": f"内部错误: {str(e)}"}, status_code=500)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8766)
