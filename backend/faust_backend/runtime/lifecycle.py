from faust_backend.utils import PerfTimer

_t = PerfTimer()

_t.begin("builtin")
import os
import asyncio
import time
from contextlib import asynccontextmanager, nullcontext, suppress as contextlib_suppress
from typing import Any

_t.end("builtin")

_t.begin("fastapi&&langchain")
from fastapi import FastAPI
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
import aiosqlite
import tqdm

_t.begin("faustbot_backend1")
import faust_backend.config_loader as conf
import faust_backend.llm_tools as llm_tools
import faust_backend.backend2front as backend2frontend
import faust_backend.trigger_manager as trigger_manager
import faust_backend.araya_runtime as araya_runtime
import faust_backend.service_manager as service_manager
import faust_backend.live_api as live_api
import faust_backend.blive_manager as blive_manager

_t.end("faustbot_backend1")

_t.begin("faustbot_backend2")
import faust_backend.minecraft_client as minecraft_client
from faust_backend.processors import get_processor_manager
import faust_backend.speech_runtime as speech_runtime
import faust_backend.nimble as nimble
from faust_backend.plugin_system import PluginManager
from faust_backend.runtime import middleware
from faust_backend.runtime.output_store import get_output_store
from faust_backend.config_loader import args

_t.end("faustbot_backend2")

_t.begin("faustbot_backend3")
from faust_backend.runtime import state
from faust_backend.logger import get_logger
from faust_backend.subagent_manager import SubagentManager
from faust_backend.tools.vfs import get_faustbot_vfs, refresh_runtime_nodes

_t.end("faustbot_backend3")
log = get_logger("faust.lifecycle")
_t.log_pref(log)



def init_plugin_manager():
    if state.plugin_manager is None:
        state.plugin_manager = PluginManager()


def _sync_plugin_trigger_filters():
    pm = state.plugin_manager
    if pm is None:
        return
    trigger_manager.set_append_filters([pm.filter_trigger_on_append])
    trigger_manager.set_fire_filters([pm.filter_trigger_on_fire])


async def _plugin_heartbeat_loop():
    while True:
        try:
            await asyncio.sleep(10.0)
            pm = state.plugin_manager
            if pm:
                summary = await pm.heartbeat_tick()
                if summary.get("errors"):
                    log.error("插件心跳错误: %s", summary.get("errors"))
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error("插件心跳循环错误: %s", e)
            await asyncio.sleep(1.0)


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


def start_services():
    if not args.no_run_other_backend_services:
        log.info("正在启动后端服务...")
        for service in tqdm.tqdm(
            service_manager.get_service_keys(), desc="[main]Starting services"
        ):
            if service == "tts" and not speech_runtime.should_start_local_tts():
                log.info("跳过本地 TTS 服务（TTS_MODE 不是 gpt-sovits）")
                continue
            if service == "asr" and not speech_runtime.should_start_local_asr():
                log.info("跳过本地 ASR 服务（ASR_MODE 不是 whisper/funasr）")
                continue
            try:
                service_manager.start_service(service, wait=False)
            except Exception as e:
                log.error("启动服务 %s 失败: %s", service, e)
            time.sleep(0.5)
        log.info("其他后端服务已启动")


async def acquire_agent_lock(owner: str) -> None:
    """获取主 Agent 锁，带超时兜底。超时说明出现了真死锁（不可重入二次获取、
    持锁路径被意外阻塞等），此时抛错并报告当前持有者，而不是无限等待。

    调度器临界区内（``scheduler.in_scheduled_turn()``）直接返回：锁已由调度器
    worker 为本回合持有，本任务只是它的子孙，二次获取必然自锁。这与"同一任务
    二次获取"不同——后者仍是真 bug，继续报错。

    同一任务二次获取会**立刻**报错：asyncio.Lock 不可重入，真等下去只会卡满
    AGENT_LOCK_TIMEOUT（/clear 曾经就这样把后端锁死 10 分钟：嵌套的 Agent 调用
    必须在调度器的同一个回合里执行，而不是二次取锁）。
    """
    from faust_backend.runtime import scheduler

    if scheduler.in_scheduled_turn():
        return
    if state.agent_lock_owner_task is asyncio.current_task():
        raise RuntimeError(
            f"主 Agent 锁不可重入：当前任务已经持有它（持有者="
            f"[{state.agent_lock_owner or '未知'}]，新的请求方=[{owner}]）。"
            "调用链里嵌套了 Agent 调用，应放在调度器的同一个回合里执行。"
        )
    try:
        await asyncio.wait_for(state.agent_lock.acquire(), timeout=state.AGENT_LOCK_TIMEOUT)
    except asyncio.TimeoutError:
        raise RuntimeError(
            f"等待主 Agent 锁超时（{state.AGENT_LOCK_TIMEOUT:g}s）：当前持有者 "
            f"[{state.agent_lock_owner or '未知'}]。这可能意味着持锁方已死锁或被阻塞。"
        ) from None
    state.mark_agent_lock_acquired(owner)


def release_agent_lock() -> None:
    """释放主 Agent 锁（**本调用点是否拥有它**）。

    调度器回合内是 no-op：锁归回合所有，由 worker（或流式生产者在 LLM 会话结束时
    提前）统一释放，嵌套的辅助调用（``TimedAgentLock`` 退出、``invoke_agent_locked``
    返回）不得把回合的锁放掉——否则回合中段会出现"锁空窗"，外部持锁方可以插进来。
    """
    from faust_backend.runtime import scheduler

    if scheduler.in_scheduled_turn():
        return
    state.agent_lock.release()
    state.mark_agent_lock_released()


def release_llm_stream_lock() -> None:
    """流式生产者结束时的释放。

    回合内：LLM 会话一结束就提前放掉本回合的锁（交付阶段的 websocket 发送/插件
    hook 阻塞不再拖住全局锁——方案 1 的 P0 语义；worker 收尾时的释放是幂等的）。
    回合外：就是普通释放。
    """
    from faust_backend.runtime import scheduler

    if scheduler.in_scheduled_turn():
        scheduler.release_turn_lock()
        return
    state.agent_lock.release()
    state.mark_agent_lock_released()


class AgentLockBusy(RuntimeError):
    """主 Agent 锁在超时内没有拿到（Agent 正忙，或持锁方僵死）。"""


class TimedAgentLock:
    """`async with` 形式的带超时主 Agent 锁。

    与 `acquire_agent_lock` 同一实现：超时抛 `AgentLockBusy`，而不是无限等待。
    修复前 rebuild 用的是裸 `async with state.agent_lock`，是全工程唯一一处
    无超时获取，把斜杠命令与管理接口都变成了可以永久挂起的调用。
    """

    __slots__ = ("owner",)

    def __init__(self, owner: str) -> None:
        self.owner = owner

    async def __aenter__(self) -> None:
        try:
            await acquire_agent_lock(self.owner)
        except RuntimeError as e:
            raise AgentLockBusy(str(e)) from None

    async def __aexit__(self, exc_type, exc, tb) -> None:
        release_agent_lock()


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
            if state.is_rate_limit_error(e) and attempt < max_attempts:
                log.warning("429 限流，重试 attempt=%d/%d", attempt, max_attempts)
            else:
                raise
        await _sleep_backoff(attempt)
    raise RuntimeError("agent invoke retries exhausted")


def _agent_thread_config() -> dict:
    """主 Agent 的 checkpoint 配置（与 `_invoke_agent_unlocked` 的默认值一致）。"""
    return {
        "configurable": {"thread_id": state.THREAD_ID},
        "recursion_limit": 300,
    }


async def _checkpoint_messages(target_agent) -> list:
    """读取当前 thread 的 checkpoint 消息（as_node 等细节不参与判断）。"""
    snapshot = await target_agent.aget_state(_agent_thread_config())
    values = getattr(snapshot, "values", None) or {}
    return list(values.get("messages") or [])


async def _seed_system_prompt(target_agent) -> bool:
    """把角色 Prompt 写进 checkpoint 的 System 消息；**不发起任何模型调用**。

    为什么要单独做这一步：主 Agent 的系统提示词只存在于 state 的 SystemMessage
    里（``create_agent`` 没有接收 ``system_prompt``），而 ``reset_dialog`` 的语义
    就是「清空对话后重新植入角色设定」。

    历史实现用「只发一条 system 消息」的模型调用来完成这件事，这对 OpenAI
    兼容格式恰好能被容忍，但对其它格式是非法请求：

    - Gemini：system 被收进 ``system_instruction`` 后 ``contents=[]`` →
      ``ValueError: contents are required.``
    - Anthropic：``messages=[]``（system 走顶层参数）→ 422
      ``messages: at least one message is required``

    改成纯状态写入后与 provider 格式无关，也不会为一次「植入」白烧一次模型调用，
    更不会在全新会话里留下一条模型寒暄。

    写入方式与 ``CompactMiddleware._build_replacement`` 一致：先
    ``REMOVE_ALL_MESSAGES`` 再按目标顺序重排，因此 System 恒在最前面，同时保留
    非 System 的历史消息（``/faust/admin/runtime/reload-all`` 这类不删 thread 的
    入口不会丢上下文、也不会累积重复 System）。

    Returns:
        True 表示写入了新的 System 消息，False 表示已经是目标状态（跳过）。
    """
    from langchain_core.messages import RemoveMessage, SystemMessage
    from langgraph.graph.message import REMOVE_ALL_MESSAGES

    prompt = str(state.PROMPT or "")
    if not prompt.strip():
        log.warning("角色 Prompt 为空，跳过 System 消息植入（AGENT.md 可能未加载）")
        return False

    config = _agent_thread_config()
    existing = await _checkpoint_messages(target_agent)

    system_messages = [m for m in existing if isinstance(m, SystemMessage)]
    kept = [m for m in existing if not isinstance(m, SystemMessage)]
    if (
        len(system_messages) == 1
        and existing
        and existing[0] is system_messages[0]
        and system_messages[0].content == prompt
    ):
        log.debug("角色 Prompt 已是最新，跳过重复植入")
        return False

    await target_agent.aupdate_state(
        config,
        {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                SystemMessage(content=prompt),
                *kept,
            ]
        },
        # 已有消息时 LangGraph 无法推断该更新属于哪个节点，会抛
        # "Ambiguous update, specify as_node"。用模型节点（与 /compact 的写回一致），
        # 语义也正确：这批消息是"模型将要看到的历史"。
        as_node="model",
    )
    log.info(
        "已植入角色 Prompt（%d 字符，保留 %d 条历史消息），未发起模型调用",
        len(prompt),
        len(kept),
    )
    return True


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
    from faust_backend.runtime import scheduler
    from faust_backend.runtime.compact import COMPACT_TAG

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
                    if not chunk or not state.is_ai_message_chunk(chunk):
                        continue
                    if is_compact_call:
                        # 压缩调用：内容只走 compact_* 通道，绝不进入正文 delta
                        if not compact_active:
                            compact_active = True
                            await queue.put({"type": "compact_start"})
                        compact_text = state.message_content_to_text(chunk.content)
                        if compact_text:
                            await queue.put(
                                {"type": "compact_delta", "content": compact_text}
                            )
                        continue
                    # 主模型开始输出 => 压缩阶段已结束（成功或失败降级都走这里）
                    await _close_compact()
                    # 推理增量：四种格式落点不同（additional_kwargs / content 块），
                    # 统一交给 state.extract_reasoning_text 提取。
                    reasoning = state.extract_reasoning_text(chunk)
                    if reasoning:
                        await queue.put({"type": "reasoning_delta", "content": reasoning})
                    delta_text = state.message_content_to_text(chunk.content)
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
                        "args": state.normalize_tool_args(data.get("input")),
                        "call_id": str(event.get("run_id") or ""),
                    })
                    continue
                if event_name == "on_tool_end":
                    await queue.put({
                        "type": "tool_result",
                        "tool_name": str(
                            event.get("name") or data.get("name") or "tool"
                        ).strip(),
                        "output": state.tool_value_to_text(data.get("output")),
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
            if state.is_rate_limit_error(e) and attempt < max_attempts:
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
            reply += state.message_content_to_text(event.get("content"))
    return reply


def _collect_runtime_tools() -> list:
    """收集主 Agent 的完整工具列表（内置 + MCP + 插件注入）。"""
    from faust_backend.mcp_manager import get_mcp_manager

    base_tools = list(llm_tools.get_tools_for_agent(state.AGENT_NAME))
    mcp_tools = get_mcp_manager().get_langchain_tools()
    if mcp_tools:
        base_tools.extend(mcp_tools)
    pm = state.plugin_manager
    if pm:
        return pm.compose_tools(base_tools=base_tools, agent_name=state.AGENT_NAME)
    return base_tools


def _compose_runtime_middlewares(*, compact_middleware=None, server_tool_middleware=None) -> list:
    """组装中间件栈。

    compact_middleware 会插在 ModelRetryMiddleware 之前（重试始终是最内层，
    只重放模型请求本身），并且始终排在最外层之后，保证判定基于未修复/未桥接
    的原始消息。server_tool_middleware 只负责往请求里追加 provider 托管工具规格。
    """
    from faust_backend.runtime.mm_bridge import MultimodalBridgeMiddleware
    from faust_backend.runtime.model_retry import with_model_retry
    from faust_backend.runtime.server_tools import ServerToolMiddleware
    from faust_backend.runtime.tool_call_repair import ToolCallRepairMiddleware

    pm = state.plugin_manager
    middlewares = pm.compose_middlewares(agent_name=state.AGENT_NAME) if pm else []
    # Filter out any stale mm_bridge instances from old plugin state
    middlewares = [
        m for m in middlewares if not isinstance(m, MultimodalBridgeMiddleware)
    ]
    middlewares.append(MultimodalBridgeMiddleware())
    # Always-on repair: never allow dangling assistant tool_calls to reach the model
    middlewares = [
        m for m in middlewares if not isinstance(m, ToolCallRepairMiddleware)
    ]
    middlewares.append(ToolCallRepairMiddleware())
    # 服务端（provider 托管）工具：仅在模型显式启用时存在
    middlewares = [
        m for m in middlewares if not isinstance(m, ServerToolMiddleware)
    ]
    if server_tool_middleware is not None:
        middlewares.append(server_tool_middleware)
    # Always-on 会话压缩：保命机制，不依赖插件开关
    if compact_middleware is not None:
        from faust_backend.runtime.compact import CompactMiddleware

        middlewares = [
            m for m in middlewares if not isinstance(m, CompactMiddleware)
        ]
        middlewares.append(compact_middleware)
    # Always-on: 瞬时模型故障统一退避重试（最内层，只重放模型请求本身）
    return with_model_retry(middlewares)


def _compose_runtime_extensions(*, compact_middleware=None, server_tool_middleware=None):
    return (
        _collect_runtime_tools(),
        _compose_runtime_middlewares(
            compact_middleware=compact_middleware,
            server_tool_middleware=server_tool_middleware,
        ),
    )


def _find_tool_by_name(name: str):
    target = str(name or "").strip()
    for tool_item in llm_tools.get_tools_for_agent(state.AGENT_NAME):
        tool_name = getattr(tool_item, "name", None) or getattr(
            tool_item, "__name__", ""
        )
        if str(tool_name).strip() == target:
            return tool_item
    raise KeyError(f"Tool not found: {target}")


def _build_subagent_toolsets() -> dict[str, list]:
    from faust_backend.mcp_manager import get_mcp_manager

    toolsets = {
        "BASESET": [
            _find_tool_by_name("read"),
            _find_tool_by_name("search"),
            _find_tool_by_name("find"),
        ],
        "WRITESET": [
            _find_tool_by_name("write"),
            _find_tool_by_name("edit"),
        ],
        "EXECUTESET": [
            _find_tool_by_name("execute"),
        ],
        "SKILLSET": [
            _find_tool_by_name("listSkills"),
        ],
    }
    mcp_manager = get_mcp_manager()
    for item in mcp_manager.list_server_statuses(include_log=False):
        server_id = str(item.get("server_id") or "").strip()
        if not server_id or not item.get("running"):
            continue
        prefix = f"{server_id}_"
        matching = []
        for tool_item in mcp_manager.get_langchain_tools():
            tool_name = getattr(tool_item, "name", "")
            if str(tool_name).startswith(prefix):
                matching.append(tool_item)
        if matching:
            toolsets[f"MCP_{server_id.upper()}_SET"] = matching
    return toolsets


async def _rebuild_subagent_manager(*, model_name: str) -> SubagentManager:
    from faust_backend.runtime.server_tools import build_server_tool_middleware

    subagent_db_path = os.path.join(state.AGENT_ROOT, "subagents.db")
    if state.subagent_manager is not None:
        try:
            await state.subagent_manager.aclose()
        except Exception as exc:
            log.warning("关闭旧 SubagentManager 失败: %s", exc)
    manager = SubagentManager(checkpointerPath=subagent_db_path)
    manager.setChatModel(await _build_chat_model(model_name=model_name), spec=model_name)
    _tools, middlewares = _compose_runtime_extensions(
        server_tool_middleware=build_server_tool_middleware(
            state.get_model_providers(), model_name
        )
    )
    manager.setMiddlewares(middlewares)
    for name, toolset in _build_subagent_toolsets().items():
        # Subagent 与主 Agent 共用 _registry 里的工具对象；不包装的话
        # 工具输出（含图片 base64）会原样进入消息文本
        manager.newToolset(name, middleware.wrap_tools(toolset))
    manager.restore_persisted_state()
    state.subagent_manager = manager
    get_output_store()
    log.info("SubagentManager 已重建: %s", subagent_db_path)
    return manager


async def _build_chat_model(*, model_name: str):
    """Build the main agent's chat model from the ModelProviders.

    model_name 是 'provider::model' spec。返回的模型格式由该 spec 的路由决定
    （OpenAI Chat / OpenAI Responses / Anthropic / Gemini），所以只能依赖
    BaseChatModel 的公共接口——不要假设 ChatOpenAI 专有属性（如 `model_name`）。
    thinking 参数由全局 REASONING_CONFIG 驱动（off/low/medium/high）：
    REASONING_CONFIG == "off" 或 provider 的 thinking_type == "none" 时不传思考参数。
    """
    from faust_backend.provider import build_ReasoningChatOpenAI_from_spec
    from faust_backend.runtime import state as runtime_state
    import faust_backend.config_loader as conf
    providers = runtime_state.get_model_providers()
    spec = model_name or providers.main_model
    if not spec:
        raise RuntimeError("main_model is not configured (provider.private.json)")
    # 找到 spec 对应的 provider，取其 thinking_type 决定是否启用思考
    provider_name, _ = spec.split("::", 1)
    provider = next((p for p in providers.providers if p.name == provider_name), None)
    reasoning = getattr(conf, "REASONING_CONFIG", "medium")
    thinking = (
        reasoning
        if reasoning != "off" and provider and provider.thinking_type != "none"
        else None
    )
    return await build_ReasoningChatOpenAI_from_spec(
        providers, spec=spec, intensity=thinking
    )


async def _create_agent_with_extensions(*, model_name: str, checkpointer):
    from faust_backend.runtime.compact import build_compact_middleware
    from faust_backend.runtime.server_tools import build_server_tool_middleware

    tools = _collect_runtime_tools()
    chat_model = await _build_chat_model(model_name=model_name)
    # 压缩 middleware 需要「与主请求逐字节相同的工具 schema」来命中前缀缓存，
    # 而 wrap_tools 是就地包装（不改变 schema），因此在包装前构建即可。
    compact_middleware = await build_compact_middleware(
        model=chat_model, tools=tools, model_spec=model_name
    )
    server_tool_middleware = build_server_tool_middleware(
        state.get_model_providers(), model_name
    )
    tools = middleware.wrap_tools(tools)
    mgmt_middlewares = _compose_runtime_middlewares(
        compact_middleware=compact_middleware,
        server_tool_middleware=server_tool_middleware,
    )
    state.compact_middleware = compact_middleware
    kwargs = {
        "model": chat_model,
        "checkpointer": checkpointer,
        "tools": tools,
    }
    if mgmt_middlewares:
        kwargs["middleware"] = mgmt_middlewares
    return create_agent(**kwargs)


async def rebuild_runtime(*, reset_dialog: bool = False, no_initial_chat: bool = False):
    """重建运行时；拿不到主 Agent 锁时返回 busy 结果，绝不无限期等待。

    修复前这里是裸 `async with state.agent_lock`（全工程唯一一处无超时获取）：
    斜杠命令 /clear /thinking /effort 在 WS 接收循环里 await 它，Agent 一忙就把
    整个输入循环（连同 interrupt 控制消息）一起冻住，管理接口同样挂起且无诊断。
    锁忙不等于运行时损坏：这里只报告状态，不清空 state.agent。

    调用位置决定锁的归属：斜杠命令/管理路由把它放在调度器的 exclusive 回合里
    （锁已由 worker 持有，取锁是 no-op），启动期与独立重建则在回合外自己取锁。
    """
    try:
        return await _rebuild_runtime_locked(
            reset_dialog=reset_dialog,
            no_initial_chat=no_initial_chat,
        )
    except AgentLockBusy as e:
        log.error("重建运行时未取得主 Agent 锁: %s", e)
        return {
            "agent_name": state.AGENT_NAME,
            "agent_root": state.AGENT_ROOT,
            "initial_chat_skipped": True,
            "ready": False,
            "status": "busy",
            "error": str(e),
        }


async def _rebuild_runtime_locked(
    *, reset_dialog: bool = False, no_initial_chat: bool = False
):  # TODO: 让逻辑更加清晰
    log.info(
        "正在重建运行时，reset_dialog=%s, no_initial_chat=%s",
        reset_dialog,
        no_initial_chat,
    )
    _needs_initial_chat = False
    async with TimedAgentLock("rebuild_runtime"):
        try:
            conf.reload_configs()
            from faust_backend.runtime import state as runtime_state
            from faust_backend.provider import (
                get_main_credentials,
                try_main_openai_credentials,
            )

            # models.dev 目录：后台刷新 + 重新路由，不阻塞启动。
            # 失败只记日志（继续用陈旧缓存），绝不阻断 Agent 启动。
            asyncio.create_task(
                refresh_models_dev_catalog(), name="models_dev_refresh"
            )

            _model_name, _api_key, _api_base = get_main_credentials(
                runtime_state.get_model_providers()
            )
            os.environ["DEEPSEEK_API_KEY"] = _api_key
            os.environ["SEARCHAPI_API_KEY"] = conf.SEARCH_API_KEY
            # OPENAI_* 环境变量只服务于「仍假定 OpenAI 协议」的第三方库。
            # 主模型是 Anthropic/Gemini 格式时不能把它的 base_url 冒充成 OpenAI
            # 端点（会让这些库打到必然 404 的路径），此时宁可不设置并要求调用方
            # 显式使用 get_main_openai_credentials()。
            _openai_creds = try_main_openai_credentials(
                runtime_state.get_model_providers()
            )
            if _openai_creds is not None:
                _oa_model, _oa_key, _oa_base = _openai_creds
                os.environ["OPENAI_API_KEY"] = _oa_key
                os.environ["OPENAI_BASE_URL"] = _oa_base
            else:
                log.info(
                    "主模型不是 OpenAI Chat 兼容格式，未设置 OPENAI_BASE_URL"
                    "（依赖 OpenAI 协议的内部功能会明确报错）"
                )
            state.AGENT_NAME = conf.AGENT_NAME
            state.AGENT_ROOT = os.path.join(
                conf.CONFIG_ROOT, "agents", f"{state.AGENT_NAME}"
            )
            log.info("重建目标 Agent: %s", state.AGENT_NAME)
            # MD_BLOCK_ENABLED 在此判断并生效：后续 _create_agent_with_extensions
            # 会经 get_tools_for_agent 按该开关决定是否注册 RenderMarkdownBlock
            log.info(
                "Markdown 内容块工具 (RenderMarkdownBlock): %s",
                "启用" if conf.MD_BLOCK_ENABLED else "禁用",
            )

            # ---Templates Makeup---
            import faust_backend.admin_runtime as admin_runtime

            sync_result = admin_runtime.sync_template_files(state.AGENT_NAME)
            if any(sync_result.values()):
                updated = [k for k, v in sync_result.items() if v]
                log.info("模板文件已同步: %s", ", ".join(updated))
            state.makeup_init_prompt()
            llm_tools.refresh_runtime_paths()
            await refresh_runtime_nodes(await get_faustbot_vfs(refresh=True))

            # ---mcp---
            from faust_backend.mcp_manager import get_mcp_manager

            mcp_manager = get_mcp_manager()
            mcp_manager.load_config(getattr(conf, "MCP_SERVERS", {}) or {})
            await mcp_manager.sync_servers()

            # ---Araya Runtime---
            araya_runtime.get_araya_runtime(refresh=True).refresh_target_agent()

            # ---Plugins---
            pm = state.plugin_manager
            if (
                pm and pm.needs_reload()
            ):  # 不在每次重建时都重载插件，而是仅在插件配置或文件变更时才重载
                log.info("插件配置或文件变更，正在重载插件...")
                plugin_reload = await pm.reload(force=True)
                log.info("插件重载摘要: %s", plugin_reload)
                _sync_plugin_trigger_filters()
                backend2frontend.FrontEndReloadPluginAssets()

            # ---langchain Agent---
            if not args.save_in_memory:
                try:
                    if state.conn is not None:
                        await state.conn.commit()
                        await state.conn.close()
                except Exception:
                    pass
                os.makedirs(state.AGENT_ROOT, exist_ok=True)
                state.conn = await aiosqlite.connect(
                    os.path.join(state.AGENT_ROOT, "faust_checkpoint.db")
                )
                state.checkpointer = AsyncSqliteSaver(conn=state.conn)
                log.info("已初始化 SQLite Checkpoint")
            else:
                state.checkpointer = InMemorySaver()
            log.info("Checkpoint 已为重建就绪")
            from faust_backend.runtime import state as runtime_state
            from faust_backend.provider import get_default_subagent_model
            _providers = runtime_state.get_model_providers()
            state.agent = await _create_agent_with_extensions(
                model_name=_providers.main_model,
                checkpointer=state.checkpointer,
            )
            try:
                _sub_model = get_default_subagent_model(_providers)
            except ValueError:
                _sub_model = _providers.main_model
            await _rebuild_subagent_manager(model_name=_sub_model)
            log.debug("Agent 已为重建重新创建")
            checkpoint_exists = (not args.save_in_memory) and state.has_checkpoint_db(
                state.AGENT_ROOT
            )
            if no_initial_chat and checkpoint_exists:
                log.info("运行时重建跳过初始对话")
                state.set_runtime_state(ready=True, status="ready")
                return {
                    "agent_name": state.AGENT_NAME,
                    "agent_root": state.AGENT_ROOT,
                    "initial_chat_skipped": True,
                    "ready": True,
                    "status": state.RUNTIME_STATUS,
                    "error": "",
                }
            _needs_initial_chat = True
        except Exception as e:
            state.agent = None
            state.set_runtime_state(
                ready=False, status="waiting_for_config", error=str(e)
            )
            log.warning("运行时重建降级: %s", e)
            return {
                "agent_name": state.AGENT_NAME,
                "agent_root": state.AGENT_ROOT,
                "initial_chat_skipped": True,
                "ready": False,
                "status": state.RUNTIME_STATUS,
                "error": str(e),
            }

    if _needs_initial_chat:
        try:
            if reset_dialog:
                # 只写状态、不调模型：system-only 的模型请求对 Gemini/Anthropic
                # 都是非法请求（contents are required / messages 不能为空）。
                await _seed_system_prompt(state.agent)
            else:
                # 全新 checkpoint（首次安装、换角色后的新库）里没有任何 System
                # 消息，角色 Prompt 就永远不会进上下文；先补齐再发 bootstrap。
                if not await _checkpoint_messages(state.agent):
                    await _seed_system_prompt(state.agent)
                await _invoke_agent_unlocked(
                    state.agent,
                    {
                        "messages": [
                            {
                                "role": "user",
                                "content": (
                                    f"请执行启动流程(Bootstrap 流程)[如有]"
                                    f"你可以读取agents/{state.AGENT_NAME}/AGENT.md、ROLE.md、COREMEMORY.md、TASK.md等文件"
                                    f"来获取最新的设定内容。\n 这一条对话无需写入日记"
                                ),
                            }
                        ]
                    },
                )
            log.info("运行时重建完成")
            state.set_runtime_state(ready=True, status="ready")
            return {
                "agent_name": state.AGENT_NAME,
                "agent_root": state.AGENT_ROOT,
                "initial_chat_skipped": False,
                "ready": True,
                "status": state.RUNTIME_STATUS,
                "error": "",
            }
        except Exception as e:
            state.agent = None
            state.set_runtime_state(
                ready=False, status="waiting_for_config", error=str(e)
            )
            log.warning("运行时重建降级: %s", e)
            return {
                "agent_name": state.AGENT_NAME,
                "agent_root": state.AGENT_ROOT,
                "initial_chat_skipped": True,
                "ready": False,
                "status": state.RUNTIME_STATUS,
                "error": str(e),
            }

    return {
        "agent_name": state.AGENT_NAME,
        "agent_root": state.AGENT_ROOT,
        "initial_chat_skipped": True,
        "ready": True,
        "status": state.RUNTIME_STATUS,
        "error": "",
    }


async def refresh_models_dev_catalog(*, force: bool = False) -> dict:
    """后台刷新 models.dev 目录并把结果应用到所有 Provider（不阻塞启动）。

    只有配置真的变化时才写回 `provider.private.json`，避免无谓的磁盘写入。
    任何异常都只记日志：目录缺失时格式会回退到通用启发式，Agent 照常工作。
    """
    import faust_backend.models_dev as models_dev

    providers = state.get_model_providers()
    snapshot = None
    try:
        snapshot = providers.model_dump()
    except Exception:  # noqa: BLE001 - 测试替身/受限对象没有 model_dump
        snapshot = None
    try:
        catalog = (
            await models_dev.refresh_catalog()
            if force
            else await models_dev.refresh_if_stale()
        )
    except Exception as exc:  # noqa: BLE001 - 后台任务不能把异常抛给事件循环
        log.warning("刷新 models.dev 目录失败（继续使用已有缓存）: %s", exc)
        return {"status": "error", "error": str(exc)}
    if not catalog:
        log.info("models.dev 目录不可用，API 格式将回退到通用启发式")
        return {"status": "empty", "providers": 0}
    if not getattr(providers, "providers", None):
        return {"status": "ok", "providers": 0, "matched": 0, "results": []}
    results = models_dev.enrich_all(providers, catalog)
    after = providers.model_dump()
    if after != snapshot:
        try:
            conf.save_model_providers()
        except Exception as exc:  # noqa: BLE001
            log.warning("保存 models.dev 路由结果失败: %s", exc)
    matched = [item for item in results if item.get("provider_id")]
    log.info(
        "models.dev 目录已应用: %d 个 Provider 完成匹配（共 %d 个），路由模型 %d 个",
        len(matched),
        len(results),
        sum(int(item.get("routed") or 0) for item in results),
    )
    return {
        "status": "ok",
        "providers": len(results),
        "matched": len(matched),
        "results": results,
    }


def schedule_memory_record_sync(user_text: str, assistant_text: str) -> None:
    if not conf.KB_ENABLED:
        return
    if not str(user_text).strip() or not str(assistant_text).strip():
        return
    log.debug("调度记忆记录同步")

    async def _job():
        try:
            llm_tools.refresh_runtime_paths()
            from faust_backend.memory import get_memory

            result = await get_memory().add_chat_record(user_text, assistant_text)
            log.info("聊天记录已同步到记忆库: %s", result.get("path"))
        except Exception as exc:
            log.error("聊天记录同步到记忆库失败: %s", exc)

    asyncio.create_task(_job())


async def _graceful_shutdown_task():
    log.info("正在优雅关闭...")
    await asyncio.sleep(0.1)
    if state.uvicorn_server:
        state.uvicorn_server.should_exit = True
        log.info("Uvicorn 关闭标志已设置")


async def _start_araya_runtime() -> bool:
    """启动 Araya 运行时；失败只降级 Araya 自己。

    Araya 是辅助 Agent（空闲时整理记忆）。它的模型构建 / 模板同步 / 状态读写
    出错，不应该连带跳过主 Agent 与插件的初始化：插件加载在 rebuild_runtime
    里，一旦被跳过，插件工具、hook、中间件、前端资源会整体缺席，界面上看起来
    就是"所有插件都没被加载"。
    """
    try:
        await araya_runtime.get_araya_runtime(refresh=True).startup()
        return True
    except Exception as e:
        log.warning("Araya 运行时启动失败（主 Agent 与插件继续启动）: %s", e)
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_plugin_manager()
    from faust_backend.mcp_manager import get_mcp_manager

    mcp_manager = get_mcp_manager()

    # ── startup ──
    backend2frontend.set_main_loop(asyncio.get_running_loop())
    # Processor 的 worker 控制通道绑在主循环上：登记后插件/Agile 模块可从自己的循环调用
    get_processor_manager().bind_loop()
    araya_runtime.get_araya_runtime(refresh=True)
    # Araya 是辅助 Agent：它失败只降级自身，不能把下面的主运行时（含插件加载）带走
    await _start_araya_runtime()
    try:
        from faust_backend.memory import get_memory

        get_memory(refresh=True)
        startup_info = await rebuild_runtime(
            reset_dialog=False, no_initial_chat=bool(args.no_startup_chat)
        )
        log.info("启动运行时摘要: %s", startup_info)
    except Exception as e:
        state.agent = None
        state.set_runtime_state(ready=False, status="waiting_for_config", error=str(e))
        log.warning("启动运行时降级: %s", e)
    log.info("触发器看门狗线程正在启动...")
    trigger_manager.start_trigger_watchdog_thread()
    try:
        await nimble.restore_persistent_sessions()
    except Exception as e:
        log.warning("恢复持久化 Nimble 窗口失败: %s", e)
    try:
        await minecraft_client.ensure_started()
    except Exception as e:
        log.warning("Minecraft 桥启动时未连接: %s", e)

    if state.plugin_heartbeat_task is None:
        state.plugin_heartbeat_task = asyncio.create_task(_plugin_heartbeat_loop())
    if state.processor_prune_task is None:
        state.processor_prune_task = asyncio.create_task(get_processor_manager().prune_loop())
    live_api.set_rebuild_callback(
        lambda: rebuild_runtime(reset_dialog=False, no_initial_chat=True)
    )
    try:
        blm = blive_manager.get_blive_manager(refresh=True)
        await blm.start()
    except Exception as e:
        log.warning("B站直播客户端启动失败: %s", e)
    log.info("FAUST 后端主服务已启动")

    yield

    # ── shutdown ──
    log.info("开始关闭 Agent...")
    from faust_backend.runtime import scheduler as scheduler_module

    # 调度器 worker：cancel + 有界等待，不 drain（未执行的回合直接终结）
    with contextlib_suppress(Exception):
        await scheduler_module.get_scheduler().shutdown()
    if state.subagent_manager is not None:
        try:
            await state.subagent_manager.aclose()
        except Exception as exc:
            log.warning("关闭 SubagentManager 失败: %s", exc)
        state.subagent_manager = None
    if not args.save_in_memory:
        if state.conn:
            await state.conn.commit()
            await state.conn.close()
    trigger_manager.stop_trigger_watchdog_thread()
    if state.plugin_heartbeat_task is not None:
        state.plugin_heartbeat_task.cancel()
        try:
            await state.plugin_heartbeat_task
        except Exception:
            pass
        state.plugin_heartbeat_task = None
    if state.processor_prune_task is not None:
        state.processor_prune_task.cancel()
        try:
            await state.processor_prune_task
        except Exception:
            pass
        state.processor_prune_task = None
    await get_processor_manager().shutdown()
    await araya_runtime.get_araya_runtime(refresh=True).shutdown()
    await mcp_manager.stop_all()
    trigger_manager.exitflag = True
    for service in service_manager.get_service_keys():
        try:
            log.info("正在停止服务: %s", service)
            service_manager.stop_service(service)
        except Exception as e:
            log.error("停止服务 %s 失败: %s", service, e)
    log.info("正在关闭 FAUST 后端主服务...")
