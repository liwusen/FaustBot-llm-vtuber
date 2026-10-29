"""消息与异常归一化的纯函数（自 ``agent/runtime/state.py`` 抽出）。

这些函数不碰任何模块级状态，也不 import 任何业务包，因此住在 core/：
routes / agent / tools 都可以直接用，而不必为了一个字符串格式化去 import
整个 Agent 运行时状态模块。
"""

from __future__ import annotations

import json


def is_rate_limit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        ("429" in text)
        or ("rate" in text and "limit" in text)
        or ("bad_response_status_code" in text)
    )


def format_chat_error(exc: Exception) -> str:
    if is_rate_limit_error(exc):
        return "上游模型网关触发限流(429)，请稍后重试。若正在发送图片，请降低 MAX_PIXELS 或减少并发请求。"
    return str(exc)


def message_content_to_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
                continue
            if not isinstance(block, dict):
                continue
            if str(block.get("type") or "").strip().lower() == "text":
                text_val = block.get("text")
                if text_val is not None:
                    parts.append(str(text_val))
        return "".join(parts)
    return str(content)


#: 可能承载「推理文本」的 content block 类型（四种格式各不同）
_REASONING_BLOCK_TYPES = {"reasoning", "thinking", "reasoning_content", "thought"}


def extract_reasoning_text(chunk) -> str:
    """从模型分片/消息里提取推理（thinking）文本，兼容四种 API 格式。

    各格式的落点完全不同，必须同时看两处：

    - `additional_kwargs`：OpenAI Chat 兼容网关（DeepSeek R1、Qwen、
      `reasoning_content`）以及部分自定义实现；
    - `content` 块：OpenAI Responses（`{"type": "reasoning", "summary": [...]}`）、
      Anthropic（`{"type": "thinking", "thinking": ...}`）、
      Gemini（`{"type": "thinking", "thinking": ...}`）。

    找不到时返回空串（不猜、不伪造）。
    """
    if chunk is None:
        return ""
    additional = getattr(chunk, "additional_kwargs", None)
    if isinstance(additional, dict):
        for key in ("reasoning_content", "reasoning", "think"):
            value = additional.get(key)
            if isinstance(value, str) and value:
                return value

    content = getattr(chunk, "content", None)
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "").strip().lower()
        if block_type not in _REASONING_BLOCK_TYPES:
            continue
        for key in ("reasoning", "thinking", "text"):
            value = block.get(key)
            if isinstance(value, str) and value:
                parts.append(value)
                break
        else:
            summary = block.get("summary")
            if isinstance(summary, list):
                for item in summary:
                    if isinstance(item, dict) and item.get("text"):
                        parts.append(str(item["text"]))
                    elif isinstance(item, str):
                        parts.append(item)
    return "".join(parts)



def is_ai_message_chunk(message_chunk) -> bool:
    msg_type = str(message_chunk.type).strip().lower()
    if msg_type == "ai":
        return True
    cls_name = message_chunk.__class__.__name__.lower()
    return "aimessage" in cls_name


def tool_value_to_text(value) -> str | dict:
    if value is None:
        return ""
    if isinstance(value, dict):
        return value  # 让外层的 json.dumps 只序列化一次
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except Exception:
        return str(value)


def normalize_tool_args(payload) -> dict:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return {}
        try:
            decoded = json.loads(text)
        except Exception:
            return {"input": text}
        return decoded if isinstance(decoded, dict) else {"input": decoded}
    if payload is None:
        return {}
    return {"input": payload}
