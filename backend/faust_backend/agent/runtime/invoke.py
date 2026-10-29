"""主 Agent 的单次调用与流式事件生产（含 429 退避重试）。

自 ``runtime/lifecycle.py`` 整块挖出（P4 布局拆分）。锁的获取/释放在 ``lock.py``。
"""

from __future__ import annotations

import asyncio

from faust_backend.core.message import (
    is_rate_limit_error,
    message_content_to_text,
    extract_reasoning_text,
    is_ai_message_chunk,
    tool_value_to_text,
    normalize_tool_args,
)
import faust_backend.agent.runtime.state as state
from faust_backend.agent.runtime.lock import (
    acquire_agent_lock,
    release_agent_lock,
    release_llm_stream_lock,
)
from faust_backend.core.log import get_logger

log = get_logger("faust.lifecycle")


async def _sleep_backoff(attempt: int) -> None:
    import random

    base = 0.8 * (2 ** max(0, attempt - 1))
    jitter = random.uniform(0.0, 0.35)
    await asyncio.sleep(min(3.0, base + jitter))


async def _apply_llm_request_pre(payload: dict) -> dict:
    """llm_request_pre hook: plugins may rewrite the messages list before each
    agent LLM invocation. First non-empty list result wins."""
    pm = getattr(state, "plugin_manager", None)
    if pm is None:
        return payload
    messages = list(payload.get("messages") or [])
    if not messages:
        return payload
    try:
        results = await pm._call_pluggy_hook("llm_request_pre", messages=messages, ctx=None)
    except Exception:
        return payload
    for r in results:
        if isinstance(r, list) and r:
            return {**payload, "messages": r}
    return payload


class _StreamState:
    """生产者→消费者的终结标记状态：保证恰好投递一次终结事件。"""

    __slots__ = ("terminal_sent",)

    def __init__(self) -> None:
        self.terminal_sent = False


async def _invoke_agent_unlocked(target_agent, payload, config=None):
    """调用主 Agent（含 429 退避重试）；**调用方必须已经持有主 Agent 锁**。

    这是"锁归谁"的唯一表达方式：需要自己取锁的入口用 ``invoke_agent_locked``，
    已经在临界区内的路径（rebuild 的引导对话）直接用它——不再需要 ``lock_held``
    这类"我自己已经持锁"的布尔参数（asyncio.Lock 不可重入，传错就是自锁）。
    """
    if config is None:
        config = {
            "configurable": {"thread_id": state.THREAD_ID},
            "recursion_limit": 300,
        }
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        log.debug("开始调用 LLM")
        try:
            payload = await _apply_llm_request_pre(payload)
            res = await target_agent.ainvoke(payload, config)
            log.debug("LLM 调用结束")
            return res
        except Exception as e:
            if is_rate_limit_error(e) and attempt < max_attempts:
                log.warning("429 限流，重试 attempt=%d/%d", attempt, max_attempts)
            else:
                raise
        await _sleep_backoff(attempt)
    raise RuntimeError("agent invoke retries exhausted")


async def invoke_agent_locked(target_agent, payload, config=None):
    """调用主 Agent（获取主 Agent 锁后再调用）。

    调度器回合内 ``acquire_agent_lock`` 是 no-op：锁已由 worker 为本回合持有，
    因此同一条链路既能被独立调用，也能安全地跑在调度器里。
    """
    log.debug("等待 Agent 锁")
    await acquire_agent_lock("invoke_agent_locked")
    try:
        return await _invoke_agent_unlocked(target_agent, payload, config)
    finally:
        release_agent_lock()


class _StreamFailed:
    """生产者→消费者队列的异常包装，由消费者重抛以保持原有错误语义。"""

    def __init__(self, error: BaseException):
        self.error = error


_STREAM_DONE = object()  # 队列终结标记


async def _stream_agent_producer(
    target_agent, payload, config, abort_event, queue, stream_state: _StreamState
):
    """持有主 Agent 锁的流式生产任务：只负责推进 astream_events 并把解析后的
    事件推入队列。锁的持有时间只覆盖 LLM 会话本身，与消费者（websocket 发送）
    的节拍无关——修复前端停止读取或插件 hook 阻塞时全局锁被无限期持有的问题。"""
    from faust_backend.agent.runtime import scheduler
    from faust_backend.agent.runtime.compact import COMPACT_TAG

    max_attempts = 3
    # 压缩调用（before_model 内部发起、带 COMPACT_TAG 的模型调用）的事件必须与
    # 主模型输出分流，转成 compact_* 事件，避免摘要文本泄进正文气泡。
    compact_active = False

    async def _finish(item) -> None:
        """投递终结标记，且只投一次。

        所有退出路径都必须经过这里：等锁被外部 cancel、异常逃逸等情况下若没有
        终结标记，消费者会永久停在 ``queue.get()`` 上（前端表现为永远转圈）。
        """
        if stream_state.terminal_sent:
            return
        stream_state.terminal_sent = True
        await queue.put(item)

    async def _close_compact(*, ok: bool = True) -> None:
        nonlocal compact_active
        if compact_active:
            compact_active = False
            await queue.put({"type": "compact_done", "ok": ok})

    for attempt in range(1, max_attempts + 1):
        log.debug("等待 Agent 锁")
        try:
            await acquire_agent_lock("stream_chat_agent_events")
        except RuntimeError as e:
            # 锁超时等获取失败：必须通知消费者，否则消费者在 queue.get() 上永久等待
            await _close_compact(ok=False)
            await _finish(_StreamFailed(e))
            return
        log.debug("开始调用 LLM")
        # 通知消费者已获取锁（前端据此清除"排队等待中"状态）
        await queue.put({"type": "lock_acquired"})
        try:
            payload = await _apply_llm_request_pre(payload)
            async for event in target_agent.astream_events(
                payload, config=config, version="v2"
            ):
                if not isinstance(event, dict):
                    continue
                # 执行位"有产出"的心跳（惰性僵死判定用，D7）
                scheduler.touch()
                if abort_event and abort_event.is_set():
                    log.info("Agent stream aborted by user")
                    raise asyncio.CancelledError("User interrupted")
                event_name = str(event.get("event") or "").strip().lower()
                data = event.get("data") or {}
                is_compact_call = COMPACT_TAG in (event.get("tags") or [])
                if event_name == "on_chat_model_stream":
                    chunk = data.get("chunk")
                    if not chunk or not is_ai_message_chunk(chunk):
                        continue
                    if is_compact_call:
                        # 压缩调用：内容只走 compact_* 通道，绝不进入正文 delta
                        if not compact_active:
                            compact_active = True
                            await queue.put({"type": "compact_start"})
                        compact_text = message_content_to_text(chunk.content)
                        if compact_text:
                            await queue.put(
                                {"type": "compact_delta", "content": compact_text}
                            )
                        continue
                    # 主模型开始输出 => 压缩阶段已结束（成功或失败降级都走这里）
                    await _close_compact()
                    # 推理增量：四种格式落点不同（additional_kwargs / content 块），
                    # 统一交给 extract_reasoning_text 提取。
                    reasoning = extract_reasoning_text(chunk)
                    if reasoning:
                        await queue.put({"type": "reasoning_delta", "content": reasoning})
                    delta_text = message_content_to_text(chunk.content)
                    if delta_text:
                        await queue.put({"type": "delta", "content": delta_text})
                    continue
                if event_name == "on_chat_model_end":
                    if is_compact_call:
                        await _close_compact()
                    continue
                if event_name == "on_tool_start":
                    await queue.put({
                        "type": "tool_start",
                        "tool_name": str(
                            event.get("name") or data.get("name") or "tool"
                        ).strip(),
                        "args": normalize_tool_args(data.get("input")),
                        "call_id": str(event.get("run_id") or ""),
                    })
                    continue
                if event_name == "on_tool_end":
                    await queue.put({
                        "type": "tool_result",
                        "tool_name": str(
                            event.get("name") or data.get("name") or "tool"
                        ).strip(),
                        "output": tool_value_to_text(data.get("output")),
                        "call_id": str(event.get("run_id") or ""),
                    })
                    continue
            log.debug("LLM 调用结束")
            await _close_compact()
            await _finish(_STREAM_DONE)
            return
        except asyncio.CancelledError:
            # 中断/消费者关闭：通知消费者（若还在等待），锁在 finally 中释放
            await _close_compact(ok=False)
            await _finish(_StreamFailed(asyncio.CancelledError("User interrupted")))
            raise
        except Exception as e:
            await _close_compact(ok=False)
            if is_rate_limit_error(e) and attempt < max_attempts:
                log.warning("429 限流，重试 attempt=%d/%d", attempt, max_attempts)
            else:
                await _finish(_StreamFailed(e))
                return
        finally:
            release_llm_stream_lock()
        await _sleep_backoff(attempt)
    await _finish(_StreamFailed(RuntimeError("agent stream retries exhausted")))


async def stream_chat_agent_events(
    target_agent, payload, config=None, *, abort_event: asyncio.Event | None = None
):
    """流式产出主 Agent 的事件（归一化后的 dict）。

    由调度器回合内的消费者驱动（``chat._run_agent_stream``）；生产者在
    "锁已由调度器持有"时不会二次获取锁。等待主 Agent 锁的排队反馈不再由这里
    发出——排队发生在调度器队列里，入口在提交时自行告知前端。
    """
    if config is None:
        config = {
            "configurable": {"thread_id": state.THREAD_ID},
            "recursion_limit": 300,
        }
    queue: asyncio.Queue = asyncio.Queue()
    stream_state = _StreamState()
    producer = asyncio.create_task(
        _stream_agent_producer(target_agent, payload, config, abort_event, queue, stream_state),
        name="stream_agent_producer",
    )

    def _on_producer_done(_task: asyncio.Task) -> None:
        """兜底：生产者没投终结标记就结束（等锁时被外部取消、尚未启动即被取消、
        或异常逃逸）时补一条，消费者绝不能停在 queue.get() 上。"""
        if stream_state.terminal_sent:
            return
        stream_state.terminal_sent = True
        queue.put_nowait(
            _StreamFailed(
                RuntimeError("agent stream producer ended without a terminal event")
            )
        )

    producer.add_done_callback(_on_producer_done)
    try:
        while True:
            item = await queue.get()
            if item is _STREAM_DONE:
                return
            if isinstance(item, _StreamFailed):
                raise item.error
            yield item
    finally:
        if not producer.done():
            producer.cancel()
        try:
            await producer
        except asyncio.CancelledError:
            pass


async def stream_silent_reply(payload: dict, config=None) -> str:
    """silent sink：驱动一次 Agent 调用并只收文本（不推前端、不写记忆）。

    与 ``invoke_agent_locked`` 的区别是它走 ``astream_events``：事件流让执行位
    持续有产出（惰性僵死判定 D7 依赖它），也顺带统一了错误语义（锁超时/429
    重试都在生产者的重试循环里）。调用方是调度器 worker 的回合，锁已持有。
    """
    reply = ""
    async for event in stream_chat_agent_events(state.agent, payload, config=config):
        if not isinstance(event, dict):
            continue
        if event.get("type") == "delta":
            reply += message_content_to_text(event.get("content"))
    return reply
