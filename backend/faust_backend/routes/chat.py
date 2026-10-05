import json
import asyncio
import time
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

import faust_backend.backend2front as backend2frontend
import faust_backend.events as events
import faust_backend.nimble as nimble
import faust_backend.araya_runtime as araya_runtime
import faust_backend.trigger_manager as trigger_manager
import faust_backend.live_mode as live_mode
import faust_backend.admin_runtime as admin_runtime
import faust_backend.config_loader as conf
import faust_backend.service_manager as service_manager
import faust_backend.skill_manager as skill_manager
from faust_backend.runtime import state
from faust_backend.runtime.lifecycle import (
    invoke_agent_locked, stream_chat_agent_events, schedule_memory_record_sync,
    rebuild_runtime, _build_chat_model,
)
from faust_backend.runtime.session_stats import collect_session_stats, get_checkpoint_messages
from faust_backend.mcp_manager import get_mcp_manager
from faust_backend.logger import get_logger
from faust_backend.runtime.output_store import reset_output_store

log = get_logger("faust.chat")

router = APIRouter(tags=["chat"])
router.description = "聊天/通信：WebSocket 流式聊天、命令转发、命令反馈，以及遗留的 POST 聊天接口" # type: ignore


def _is_slash_command(text: str) -> bool:
    return str(text or "").startswith("/")


def _parse_slash_command(text: str) -> tuple[str, str]:
    raw = str(text or "").strip()
    body = raw[1:].strip()
    if not body:
        return "", ""
    parts = body.split(None, 1)
    name = str(parts[0] or "").strip().lower()
    arg = str(parts[1] or "").strip() if len(parts) > 1 else ""
    return name, arg


def _message_to_plain_text(message) -> str:
    msg_type = type(message).__name__
    content = getattr(message, "content", "")
    text = state.message_content_to_text(content)
    if not text and isinstance(content, list):
        text = json.dumps(content, ensure_ascii=False)
    return f"[{msg_type}] {text}".strip()


async def _collect_status_summary() -> str:
    skill_manager._ensure_builtin_skills(agent_name=state.AGENT_NAME)
    skills = [item for item in skill_manager.list_skills(agent_name=state.AGENT_NAME) if item.get("enabled", True)]
    plugins = state.plugin_manager.list_plugins() if state.plugin_manager else []
    enabled_plugins = [item for item in plugins if item.get("enabled")]
    services = service_manager.list_services(include_log=False)
    mcp_items = get_mcp_manager().list_server_statuses(include_log=False)
    lines = [
        f"Agent: {state.AGENT_NAME}",
        f"Reasoning: {getattr(conf, 'REASONING_CONFIG', 'medium')}",
        f"Skills({len(skills)}): " + (", ".join(item.get("slug") or "" for item in skills) if skills else "none"),
        f"Plugins({len(enabled_plugins)}): " + (", ".join(item.get("id") or "" for item in enabled_plugins) if enabled_plugins else "none"),
        "Services:",
    ]
    for item in services:
        lines.append(f"- {item.get('key')}: {'running' if item.get('is_running') else 'stopped'}")
    lines.append("MCP:")
    for item in mcp_items:
        lines.append(f"- {item.get('server_id')}: {item.get('status')} tools={item.get('tool_count')}")
    return "\n".join(lines)


async def _set_thinking_enabled(enabled: bool) -> str:
    # 全局思考由 REASONING_CONFIG 驱动：off 关闭，on 恢复 medium。
    current = str(getattr(conf, "REASONING_CONFIG", "medium") or "medium")
    next_value = "medium" if enabled and current == "off" else current
    if not enabled:
        next_value = "off"
    admin_runtime.save_config({"public": {"REASONING_CONFIG": next_value}})
    await rebuild_runtime(reset_dialog=False, no_initial_chat=True)
    return f"Thinking 已{'开启' if enabled else '关闭'} (REASONING_CONFIG={next_value})"


async def _set_reasoning_effort(level: str) -> str:
    level = str(level or "").strip().lower()
    if level not in {"off", "low", "medium", "high"}:
        return "用法: /effort off|low|medium|high"
    admin_runtime.save_config({"public": {"REASONING_CONFIG": level}})
    await rebuild_runtime(reset_dialog=False, no_initial_chat=True)
    return f"Reasoning effort 已设置为 {level}"


def _usage_field(value) -> str:
    return "-" if value is None else str(value)


async def _session_token_summary() -> str:
    """统计当前会话 token 的文本渲染。

    数字来源与「拿不到 API 计数就不猜」的判定全部在
    `runtime/session_stats.py`（与 GET /faust/session/context 共用同一实现）。
    """
    stats = await collect_session_stats()
    if not stats["messages"]:
        return "当前会话没有可统计的上下文。"
    lines = [
        f"模型: {stats['model']}",
        f"messages={stats['messages']}",
    ]
    if not stats["has_api_usage"]:
        lines.append("tokens: 暂无 API 计数（本会话尚无 LLM 响应上报 usage）")
    else:
        lines.append(f"prompt_tokens={_usage_field(stats['input_tokens'])}")
        lines.append(f"completion_tokens={_usage_field(stats['output_tokens'])}")
        lines.append(f"total_tokens={_usage_field(stats['total_tokens'])}")
        if stats["cache_read_tokens"] is not None:
            lines.append(f"缓存命中(cache_read)={stats['cache_read_tokens']}")
        lines.append(
            f"上下文长度={stats['context_length']} 触发阈值={stats['threshold_tokens']} "
            f"({stats['compact_threshold_ratio']:.0%}) 当前占用={stats['percent']:.1f}%"
        )
    return "\n".join(lines)


async def _clear_current_session() -> str:
    if state.checkpointer is not None and hasattr(state.checkpointer, "adelete_thread"):
        await state.checkpointer.adelete_thread(str(state.THREAD_ID))
    if state.subagent_manager is not None:
        await state.subagent_manager.reset_persistent_state()
    reset_output_store(clear_persisted=True)
    pm = getattr(state, 'plugin_manager', None)
    if pm:
        pm.reset_all_plugin_sessions()
    info = await rebuild_runtime(reset_dialog=True, no_initial_chat=False)
    return f"已清空当前会话并重建运行时。ready={info.get('ready')} status={info.get('status')}"


async def _compact_session_stream(websocket: WebSocket) -> str:
    """手动 /compact：调用与 auto compact 同一个 middleware，并流式推送卡片。

    与 auto compact 的唯一区别是「无视阈值强制压一次」；压缩逻辑、提示词、
    缓存策略完全共用，避免两套实现漂移。
    """
    middleware = state.compact_middleware
    if middleware is None:
        raise RuntimeError("会话压缩中间件未就绪（运行时可能未成功重建）")
    messages = await get_checkpoint_messages()
    if not messages:
        return "当前会话没有可压缩的上下文。"

    await websocket.send_text(
        json.dumps(_main_event_payload("compact_start"), ensure_ascii=False)
    )

    async def _on_delta(text: str) -> None:
        await websocket.send_text(
            json.dumps(
                _main_event_payload("compact_delta", content=text), ensure_ascii=False
            )
        )

    update = await middleware.force_compact(messages, on_delta=_on_delta)
    if not update:
        await websocket.send_text(
            json.dumps(_main_event_payload("compact_done"), ensure_ascii=False)
        )
        return "当前会话没有可压缩的历史轮次。"

    # 通过 graph 的 aupdate_state 应用更新：它会走 add_messages reducer，
    # 因此 RemoveMessage(REMOVE_ALL_MESSAGES) 与顺序重排都能被正确执行。
    # as_node 必须显式指定：create_agent 编译出的图有 model/tools 两个节点，
    # 不指定时 langgraph 无法判断该写入归属哪个节点（Ambiguous update）。
    config = {"configurable": {"thread_id": str(state.THREAD_ID)}}
    await state.agent.aupdate_state(config, update, as_node="model")
    await websocket.send_text(
        json.dumps(_main_event_payload("compact_done"), ensure_ascii=False)
    )
    return "对话压缩完成。"


async def _handle_slash_command(text: str, websocket: WebSocket | None = None) -> tuple[bool, str]:
    if not _is_slash_command(text) or text.startswith("/skill:"):
        return False, ""
    name, arg = _parse_slash_command(text)
    if not name:
        return True, "空命令"
    if name == "thinking":
        lowered = arg.lower()
        if lowered not in {"on", "off"}:
            return True, "用法: /thinking on 或 /thinking off"
        return True, await _set_thinking_enabled(lowered == "on")
    if name == "effort":
        return True, await _set_reasoning_effort(arg)
    if name == "status":
        return True, await _collect_status_summary()
    if name == "session":
        return True, await _session_token_summary()
    if name == "clear":
        return True, await _clear_current_session()
    if name == "compact":
        if websocket is None:
            return True, "POST 接口暂不支持 /compact，请使用 WebSocket 聊天接口。"
        await _compact_session_stream(websocket)
        # 已自行推送 compact_* 完整流。返回 None 让上层跳过通用的
        # start/delta/done —— 否则通用 start 会重置前端 entries，
        # 把刚推送的压缩卡片清掉。
        return True, None
    return True, f"未知命令: /{name}"


def _subagents_summary_payload() -> dict:
    manager = state.subagent_manager
    if manager is None:
        return {"agent_id": "subagents", "type": "subagents_summary", "items": []}
    return {"agent_id": "subagents", "type": "subagents_summary",
            "items": manager.list_statuses_light()}


def _main_event_payload(event_type: str, **kwargs) -> dict:
    payload = {"agent_id": "main", "type": event_type}
    payload.update(kwargs)
    return payload


# 活跃的 /faust/chat websocket 连接，供前台触发器流式推送复用
_active_chat_websockets: set = set()

# 当前占用主 Agent 的触发器调用任务（前台流 / 后台 invoke），供用户插话强制取消
_active_trigger_task: asyncio.Task | None = None
# 正在执行用户插话强制打断（前台触发器被取消时改发 done(forced) 而非 interrupted）
_force_interrupting: bool = False


def _register_trigger_task(task: asyncio.Task) -> None:
    global _active_trigger_task
    _active_trigger_task = task


def _clear_trigger_task(task: asyncio.Task) -> None:
    global _active_trigger_task
    if _active_trigger_task is task:
        _active_trigger_task = None


async def _force_interrupt_trigger() -> bool:
    """用户插话：强制取消当前触发器任务并等待其退出（确保主 Agent 锁释放）。

    返回是否实际发生了打断。"""
    global _force_interrupting
    task = _active_trigger_task
    if task is None or task.done():
        return False
    log.info("用户插话：强制打断触发器任务")
    _force_interrupting = True
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.warning("强制打断触发器任务时出错: %s", e)
    finally:
        _force_interrupting = False
        _clear_trigger_task(task)
    return True


async def _apply_user_interjection(text: str) -> str:
    """AUTO_FORCE_INTERRUPT 开启时，若主 Agent 正被触发器占用：
    强制取消触发器任务，并给用户消息加"(用户插话)"标记。"""
    if not conf.AUTO_FORCE_INTERRUPT:
        return text
    if await _force_interrupt_trigger():
        return f"(用户插话){text}"
    return text


async def _run_trigger_stream_frontend(chat_ws, trigger_text: str) -> None:
    """前台触发器流式推送；失败时降级为后台执行。"""
    try:
        await chat_ws.send_text(json.dumps(_main_event_payload("start"), ensure_ascii=False))
        await _run_agent_stream(chat_ws, trigger_text, origin='trigger_foreground')
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.error("触发器流式推送失败，降级为后台执行: %s", e)
        fallback_text = await _apply_plugin_message_hooks(trigger_text, origin='trigger_background')
        if fallback_text == "__IGNORED__":
            log.info("触发器消息已被插件拦截，跳过降级执行")
            return
        await invoke_agent_locked(state.agent, {"messages": [{"role": "user", "content": fallback_text}]})


async def _apply_plugin_message_hooks(text: str, origin: str = 'user') -> str:
    """把用户消息/触发器文本按洋葱模型过一遍插件的 message_received。

    所有把文本送进 Agent 的入口都必须走这里，否则插件（记忆注入、情绪向量等）
    会在该路径上被静默绕过。返回值可能为 "__IGNORED__"（消息被插件拦截）。

    origin 告诉插件这条文本是谁送进来的（'user' / 'trigger_foreground' / 'trigger_background'），
    插件据此决定要不要把"随行附加"这类只对用户可见的东西挂上去。
    """
    pm = state.plugin_manager
    if pm is None:
        return text
    return await pm.apply_message_received(text, history=[], ctx=None, origin=origin)


async def _run_agent_stream(websocket: WebSocket, text: str, agent=None, origin: str = 'user') -> str:
    reply = ""
    abort_evt = state.reset_abort_event()
    pm = state.plugin_manager
    text = await _apply_plugin_message_hooks(text, origin)
    if text == "__IGNORED__":
        log.info("消息已被插件拦截 (message_received -> __IGNORED__)")
        done_payload = _main_event_payload("done")
        if pm:
            await pm._call_pluggy_hook('agent_event_sent', event=done_payload, current_history=[], ctx=None)
        await websocket.send_text(json.dumps(done_payload, ensure_ascii=False))
        return ""
    try:
        current_history: list[dict] = []
        agen = stream_chat_agent_events(
            agent if agent is not None else state.agent,
            {"messages": [{"role": "user", "content": text}]},
            abort_event=abort_evt,
        )
        try:
            async for event in agen:
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "waiting_lock":
                    # 主 Agent 忙/排队中：立即向前端反馈，避免无提示转圈
                    await websocket.send_text(json.dumps(_main_event_payload("pending", reason="waiting_lock"), ensure_ascii=False))
                    continue
                if event.get("type") == "lock_acquired":
                    # 已获取主 Agent 锁：清除前端"排队等待中"状态
                    await websocket.send_text(json.dumps(_main_event_payload("streaming"), ensure_ascii=False))
                    continue
                if event.get("type") == "reasoning_delta":
                    payload = _main_event_payload("reasoning_delta", content=event.get("content", ""))
                    if pm:
                        results = await pm._call_pluggy_hook('agent_event_sent', event=payload, current_history=current_history, ctx=None)
                        if results:
                            for r in results:
                                if r == "__IGNORED__" or r == "__REMOVED__":
                                    log.debug("Agent event hook returned empty string, discarding payload")
                                    payload = None
                                    break
                                if isinstance(r, dict):
                                    payload = r
                    if payload:
                        current_history.append(payload)
                        await websocket.send_text(json.dumps(payload, ensure_ascii=False))
                    if state.subagent_manager and state.subagent_manager.consume_status_dirty():
                        await websocket.send_text(json.dumps(_subagents_summary_payload(), ensure_ascii=False))
                    continue
                if event.get("type") == "delta":
                    delta_text = state.message_content_to_text(event.get("content"))
                    if not delta_text:
                        continue
                    reply += delta_text
                    log.debug("聊天增量: %s", delta_text[:80])
                    payload = _main_event_payload("delta", content=delta_text)
                    if pm:
                        results = await pm._call_pluggy_hook('agent_event_sent', event=payload, current_history=current_history, ctx=None)
                        if results:
                            for r in results:
                                if r is None:
                                    payload = None
                                    break
                                if isinstance(r, dict):
                                    payload = r
                    if payload:
                        current_history.append(payload)
                        await websocket.send_text(json.dumps(payload, ensure_ascii=False))
                    if state.subagent_manager and state.subagent_manager.consume_status_dirty():
                        await websocket.send_text(json.dumps(_subagents_summary_payload(), ensure_ascii=False))
                    continue
                if event.get("type") in {"compact_start", "compact_delta", "compact_done"}:
                    # 会话压缩卡片：只进前端折叠卡片，绝不并入 reply（因此不进 TTS）
                    payload = dict(event)
                    payload["agent_id"] = "main"
                    if pm:
                        results = await pm._call_pluggy_hook('agent_event_sent', event=payload, current_history=current_history, ctx=None)
                        if results:
                            for r in results:
                                if r is None:
                                    payload = None
                                    break
                                if isinstance(r, dict):
                                    payload = r
                    if payload:
                        current_history.append(payload)
                        await websocket.send_text(json.dumps(payload, ensure_ascii=False))
                    continue
                if event.get("type") in {"tool_start", "tool_result"}:
                    payload = dict(event)
                    payload["agent_id"] = "main"
                    if pm:
                        results = await pm._call_pluggy_hook('agent_event_sent', event=payload, current_history=current_history, ctx=None)
                        if results:
                            for r in results:
                                if r is None:
                                    payload = None
                                    break
                                if isinstance(r, dict):
                                    payload = r
                    if payload:
                        current_history.append(payload)
                        await websocket.send_text(json.dumps(payload, ensure_ascii=False))
                    if state.subagent_manager and state.subagent_manager.consume_status_dirty():
                        await websocket.send_text(json.dumps(_subagents_summary_payload(), ensure_ascii=False))
        finally:
            await agen.aclose()
        schedule_memory_record_sync(text, reply)
        done_payload = _main_event_payload("done")
        if pm:
            await pm._call_pluggy_hook('agent_event_sent', event=done_payload, current_history=current_history, ctx=None)
        await websocket.send_text(json.dumps(done_payload, ensure_ascii=False))
        log.debug("聊天流结束")
    except asyncio.CancelledError:
        # 消费者在自身 await 处被打断时生成器停在 yield，需显式取消生产者任务以释放锁
        for t in asyncio.all_tasks():
            if t.get_name() == "stream_agent_producer" and not t.done():
                t.cancel()
        if _force_interrupting:
            # 用户插话强制打断前台触发器：补发 done(forced)，前端据此清理触发器会话
            await websocket.send_text(json.dumps(_main_event_payload("done", forced=True), ensure_ascii=False))
        else:
            await websocket.send_text(json.dumps(_main_event_payload("interrupted"), ensure_ascii=False))
            log.info("聊天流被用户中断")
    except Exception as e:
        log.error("Chat agent stream 错误: %s", e, exc_info=True)
        try:
            await websocket.send_text(json.dumps(_main_event_payload("error", error=state.format_chat_error(e)), ensure_ascii=False))
        except Exception:
            log.error("向 websocket 发送错误事件失败（连接可能已关闭）")
    return reply


@router.post("/faust/chat")
async def chat_post(payload: dict):
    text = None
    if isinstance(payload, dict):
        text = payload.get('text') or payload.get('message')
    if not text:
        return {"error": "no text provided"}
    handled, command_reply = await _handle_slash_command(text)
    if handled:
        return {"reply": command_reply}
    if not state.RUNTIME_READY or state.agent is None:
        return {"error": state.runtime_not_ready_message(), "runtime": state.runtime_status_payload()}
    try:
        await asyncio.to_thread(araya_runtime.get_araya_runtime(refresh=True).mark_main_agent_activity)
        events.ignore_trigger_event.set()
        text = await _apply_plugin_message_hooks(text)
        if text == "__IGNORED__":
            events.ignore_trigger_event.clear()
            log.info("消息已被插件拦截 (message_received -> __IGNORED__)")
            return {"reply": "", "suppressed": True}
        try:
            resp = await invoke_agent_locked(state.agent, {"messages": [{"role": "user", "content": text}]})
        except RuntimeError as e:
            if "等待主 Agent 锁超时" in str(e):
                log.error("Chat POST 获取主 Agent 锁超时: %s", e)
                return JSONResponse(
                    status_code=503,
                    content={"error": str(e), "busy": True},
                )
            raise
        if not resp:
            raise RuntimeError()
        reply = state.message_content_to_text(resp["messages"][-1].content)
        schedule_memory_record_sync(text, reply)
        log.info('Chat POST 回复完成')
        events.ignore_trigger_event.clear()
        return {"reply": reply, "warning": "使用websocket /faust/chat接口以获得更好的前端流式体验和更低的延迟。"}
    except Exception as e:
        log.error("Chat POST 错误: %s", e)
        return {"error": state.format_chat_error(e), "warning": "使用websocket /faust/chat接口以获得更好的前端流式体验和更低的延迟。"}


@router.websocket("/faust/chat")
async def chat_websocket(websocket: WebSocket):
    await websocket.accept()
    _active_chat_websockets.add(websocket)
    agent_task: asyncio.Task | None = None

    # ── WS 连接级别的 Subagent 事件转发 ──
    _subagent_fwd_stop = asyncio.Event()
    _subagent_fwd_task: asyncio.Task | None = None
    _subagent_queue = None
    if state.subagent_manager is not None:
        _subagent_queue = state.subagent_manager.get_event_queue()
        # 清空遗留事件
        while _subagent_queue and not _subagent_queue.empty():
            try:
                _subagent_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        async def _forward_subagent_events():
            while not _subagent_fwd_stop.is_set():
                try:
                    event = await asyncio.wait_for(_subagent_queue.get(), timeout=0.5)
                    assert event["agent_id"] != "main", "Subagent 事件队列中不应包含主 Agent 事件"
                    await websocket.send_text(json.dumps(event, ensure_ascii=False))
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
            # 排空
            while _subagent_queue and not _subagent_queue.empty():
                try:
                    ev = _subagent_queue.get_nowait()
                    await websocket.send_text(json.dumps(ev, ensure_ascii=False))
                except asyncio.QueueEmpty:
                    break

        _subagent_fwd_task = asyncio.create_task(_forward_subagent_events())

    async def _stop_forward_task():
        nonlocal _subagent_fwd_task
        if _subagent_fwd_task is None:
            return
        _subagent_fwd_stop.set()
        try:
            await asyncio.wait_for(_subagent_fwd_task, timeout=2.0)
        except asyncio.TimeoutError:
            _subagent_fwd_task.cancel()
            try:
                await _subagent_fwd_task
            except Exception:
                pass
        _subagent_fwd_task = None

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {"text": raw}

            # Handle interrupt message
            if isinstance(payload, dict) and payload.get("type") == "interrupt":
                state.get_abort_event().set()
                if agent_task is not None and not agent_task.done():
                    agent_task.cancel()
                await websocket.send_text(json.dumps(_main_event_payload("interrupt_ack"), ensure_ascii=False))
                continue

            text = None
            if isinstance(payload, dict):
                text = payload.get("text") or payload.get("message")
            if not text:
                await websocket.send_text(json.dumps(_main_event_payload("error", error="no text provided"), ensure_ascii=False))
                continue
            trigger_manager.note_user_interaction()
            handled, command_reply = await _handle_slash_command(text, websocket)
            if handled:
                if command_reply is None:
                    # 命令已自行推送完整事件流（/compact 的压缩卡片），
                    # 不能再发通用 start，否则会重置前端 entries。
                    log.info("Slash Command 自行推送事件流（无通用回复）")
                    continue
                # Send subagent summary before start/done per requirement
                if state.subagent_manager and state.subagent_manager.consume_status_dirty():
                    await websocket.send_text(json.dumps(_subagents_summary_payload(), ensure_ascii=False))
                await websocket.send_text(json.dumps(_main_event_payload("start"), ensure_ascii=False))
                await websocket.send_text(json.dumps(_main_event_payload("delta", content=command_reply), ensure_ascii=False))
                await websocket.send_text(json.dumps(_main_event_payload("done"), ensure_ascii=False))
                log.info(f"Slash Command Result:{command_reply}")
                continue
            if not state.RUNTIME_READY or state.agent is None:
                await websocket.send_text(json.dumps(_main_event_payload("error", error=state.runtime_not_ready_message(), runtime=state.runtime_status_payload()), ensure_ascii=False))
                continue

            # 用户插话: 触发器占用主 Agent 时强制打断并标记消息 (AUTO_FORCE_INTERRUPT)。
            # 放在 ignore_trigger_event 置位之后，确保插话瞬间消费循环不再捡起
            # 队列中的下一个触发器抢在用户消息前获取锁。
            try:
                araya_runtime.get_araya_runtime(refresh=True).mark_main_agent_activity()
                events.ignore_trigger_event.set()
                text = await _apply_user_interjection(text)

                # Cancel any running agent task before starting a new one
                if agent_task is not None and not agent_task.done():
                    state.get_abort_event().set()
                    agent_task.cancel()
                    try:
                        await agent_task
                    except asyncio.CancelledError:
                        pass

                await websocket.send_text(json.dumps(_main_event_payload("start"), ensure_ascii=False))
                log.info("收到聊天消息: %s", text[:100])
                agent_task = asyncio.create_task(_run_agent_stream(websocket, text))

                def _on_agent_task_done(t: asyncio.Task):
                    events.ignore_trigger_event.clear()
                    if not t.cancelled() and t.exception() is not None:
                        log.error("agent_task 未处理异常: %s", t.exception(), exc_info=t.exception())

                agent_task.add_done_callback(_on_agent_task_done)
            except Exception as e:
                events.ignore_trigger_event.clear()
                log.error("Chat WebSocket 错误: %s", e)
                await websocket.send_text(json.dumps(_main_event_payload("error", error=state.format_chat_error(e)), ensure_ascii=False))
    except WebSocketDisconnect:
        log.info("Chat WebSocket 断开")
    finally:
        _active_chat_websockets.discard(websocket)
        if agent_task is not None and not agent_task.done():
            agent_task.cancel()
            try:
                await agent_task
            except Exception:
                pass
        await _stop_forward_task()

@router.websocket("/faust/command")
async def command_websocket(websocket: WebSocket):
    await websocket.accept()
    backend2frontend.FrontEndSay("Hello World! 你好,世界!")
    nimble.push_persistent_sessions_to_frontend()
    try:
        batch_buffer: list[tuple[float, dict]] = []  # (first_ts, item)
        while True:
            if backend2frontend.hasFrontEndTask():
                task = await backend2frontend.popFrontEndTask()
                log.debug("从 backend2frontend 队列发送前端任务: %s", task[:80] if isinstance(task, str) else str(task)[:80])
                if task:
                    await websocket.send_text(task)
            # 消费侧 batched 聚合：忙时积压，窗口到期或空闲时打包注入
            for it in trigger_manager.drain_batched():
                batch_buffer.append((time.time(), it))
            if batch_buffer:
                first_ts = min(ts for ts, _ in batch_buffer)
                window_due = time.time() - first_ts >= trigger_manager.BATCH_WINDOW_SEC
                idle_now = (
                    not backend2frontend.hasFrontEndTask()
                    and not trigger_manager.has_queue_task()
                    and not events.ignore_trigger_event.is_set()
                )
                if (
                    (window_due or idle_now)
                    and not events.ignore_trigger_event.is_set()
                    and state.RUNTIME_READY and state.agent is not None
                ):
                    items = [it for _, it in batch_buffer]
                    batch_buffer = []
                    trigger_text = trigger_manager.format_batch_injection(items, first_ts)
                    log.info('批量触发器注入 %d 条: %s', len(items), trigger_text[:120])
                    # 插件钩子（记忆检索/情绪向量）与 Agent 调用都可能失败（如 LLM
                    # 传输层异常），任一失败都不得炸掉整个命令 WS 循环——与后台/前台
                    # 触发器分支保持一致的容错语义。
                    batch_task: asyncio.Task | None = None
                    try:
                        batch_text = await _apply_plugin_message_hooks(trigger_text, origin='trigger_background')
                        if batch_text == "__IGNORED__":
                            log.info('批量触发器消息已被插件拦截，跳过本次注入')
                        else:
                            batch_task = asyncio.create_task(
                                invoke_agent_locked(state.agent, {"messages": [{"role": "user", "content": batch_text}]})
                            )
                            _register_trigger_task(batch_task)
                            await batch_task
                    except asyncio.CancelledError:
                        # 批量任务被用户插话取消属正常流程；自身被取消则继续向上抛
                        if batch_task is None or not batch_task.cancelled():
                            raise
                        log.info('批量触发器被用户插话打断')
                    except Exception as e:
                        log.error('批量触发器 Agent 调用失败: %s', e)
                    finally:
                        if batch_task is not None:
                            _clear_trigger_task(batch_task)
            if trigger_manager.has_queue_task() and not events.ignore_trigger_event.is_set():
                if not state.RUNTIME_READY or state.agent is None:
                    await asyncio.sleep(0.1)
                    continue
                task = trigger_manager.get_next_trigger()
                trigger_text = f"<Trigger>触发器唤醒了你，请根据触发器内容执行相应操作。{str(task)}"
                if isinstance(task, dict):
                    ttype = task.get("type")
                    callback_id = task.get("callback_id")
                    if ttype == "event" and task.get("event_name") == "nimble_message" and callback_id:
                        msg_payload = (task.get("payload") or {}).get("payload")
                        if isinstance(msg_payload, dict) and msg_payload.get("type") == "window-closed":
                            # 关闭时 console 节点已随会话销毁，不能再让 Agent 去读
                            trigger_text = (
                                f"<Trigger>灵动交互窗口 {callback_id} 已被用户关闭"
                                f"（reason={msg_payload.get('reason')}）。窗口与 console 节点已销毁，"
                                f"不要再读写 faustbot://nimble/{callback_id}/console；如需继续互动请重新创建窗口。"
                            )
                        else:
                            trigger_text = (
                                f"<Trigger>灵动交互窗口消息。callback_id={callback_id}，"
                                f"payload={json.dumps(msg_payload, ensure_ascii=False)}。"
                                f"完整对话记录在 faustbot://nimble/{callback_id}/console，"
                                f"如需回复请用 write 工具向该 console 路径写入消息。"
                            )
                    elif ttype == "event" and task.get("event_name") == "blive_danmaku":
                        payload = task.get("payload") or {}
                        uname = payload.get("uname", "匿名")
                        msg = payload.get("msg", "")
                        if live_mode.is_tts_blacklisted(msg):
                            continue
                        trigger_text = f"<Trigger>直播间弹幕: {uname}: {msg}"
                    elif ttype == "event" and task.get("event_name") == "mc_event":
                        payload = task.get("payload") or {}
                        trigger_text = (
                            "<Trigger>Minecraft事件唤醒了你。"
                            f"事件类型={payload.get('mc_event_type')}，"
                            f"事件详情={json.dumps(payload, ensure_ascii=False)}。"
                            "请结合当前游戏状态，决定是否调用 Minecraft 工具继续操作。"
                        )
                    elif ttype == "nimble-expire" and callback_id:
                        await nimble.finalize_close(callback_id, reason="expired")
                        trigger_text = f"<Trigger>灵动交互窗口已过期关闭。callback_id={callback_id}。如有必要，请重新创建更明确的新窗口。"
                log.info('触发器激活，正在调用 Agent: %s', trigger_text[:120])
                run_background = bool(task.get("run_background")) if isinstance(task, dict) else False
                chat_ws = next(iter(_active_chat_websockets), None)
                if run_background or chat_ws is None:
                    # 后台触发器（或无前端连接时降级）：仅执行，不推送前端
                    # 前台流式路径在 _run_agent_stream 内部做插件处理，这里不能重复处理
                    bg_task: asyncio.Task | None = None
                    try:
                        bg_text = await _apply_plugin_message_hooks(trigger_text, origin='trigger_background')
                        if bg_text == "__IGNORED__":
                            log.info('触发器消息已被插件拦截，跳过后台执行: %s', trigger_text[:80])
                            continue
                        bg_task = asyncio.create_task(
                            invoke_agent_locked(state.agent, {"messages": [{"role": "user", "content": bg_text}]})
                        )
                        _register_trigger_task(bg_task)
                        await bg_task
                        log.debug('后台触发器执行完成: %s', trigger_text[:80])
                    except asyncio.CancelledError:
                        # 后台任务被用户插话取消属正常流程；自身被取消则继续向上抛
                        if bg_task is None or not bg_task.cancelled():
                            raise
                        log.info('后台触发器被用户插话打断: %s', trigger_text[:80])
                    except Exception as e:
                        # 插件钩子/Agent 调用失败（如 LLM 连接错误）不应炸掉整个命令 WS 循环
                        log.error('后台触发器执行失败: %s', e)
                    finally:
                        if bg_task is not None:
                            _clear_trigger_task(bg_task)
                else:
                    # 前台触发器：通过 chat websocket 流式推送
                    fg_task = asyncio.create_task(_run_trigger_stream_frontend(chat_ws, trigger_text))
                    _register_trigger_task(fg_task)
                    try:
                        await fg_task
                    except asyncio.CancelledError:
                        if not fg_task.cancelled():
                            raise
                        log.info('前台触发器被用户插话打断: %s', trigger_text[:80])
                    except Exception as e:
                        # Agent 流式调用失败（如 LLM 连接错误）不应炸掉整个命令 WS 循环
                        log.error('前台触发器流式调用失败: %s', e)
                    finally:
                        _clear_trigger_task(fg_task)
            if not state.forward_queue.empty():
                command = await state.forward_queue.get()
                log.debug("从队列转发命令: %s", command[:80])
                await websocket.send_text(f"{command}")
                continue
            if not backend2frontend.hasFrontEndTask() and (events.ignore_trigger_event.is_set() or not trigger_manager.has_queue_task()):
                try:
                    await asyncio.wait_for(events.backend2frontendQueue_event.wait(), timeout=0.5)
                    events.backend2frontendQueue_event.clear()
                except asyncio.TimeoutError:
                    pass
    except WebSocketDisconnect:
        log.info("Command WebSocket 断开")
    except Exception:
        # 不再把致命错误塞进 SAY 通道：前端会把 SAY 内容渲染成气泡并用 TTS 当作
        # Faust 的台词朗读（frontend/app.js 的 SAY 分支）。这里只记录完整栈，
        # 连接关闭后 Electron 主进程会自动重连。
        log.exception("Command WebSocket 循环致命错误，连接即将关闭")


@router.post("/faust/command/forward")
async def command_forward_post(payload: dict):
    command = None
    if isinstance(payload, dict):
        command = payload.get('command')
    if not command:
        return {"error": "no command provided"}
    await state.forward_queue.put(command)
    events.backend2frontendQueue_event.set()
    return {"status": "command forwarded"}


@router.post("/faust/command/feedback")
async def command_feedback_post(payload: dict):
    command_id = None
    feedback = None
    if isinstance(payload, dict):
        command_id = payload.get("command_id")
        feedback = payload.get("feedback")
    if not command_id:
        return {"error": "no command_id provided"}
    log.info("收到命令反馈 %s: %s", command_id, feedback)
    if feedback_event := events.feedback_event_pool.get(command_id):
        feedback_event.set()
    return {"status": "feedback received", "command_id": command_id}
