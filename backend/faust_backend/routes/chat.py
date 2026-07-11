import json
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.base import empty_checkpoint

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
from faust_backend.mcp_manager import get_mcp_manager
from faust_backend.logger import get_logger

log = get_logger("faust.chat")

router = APIRouter(tags=["chat"])
router.description = "聊天/通信：WebSocket 流式聊天、命令转发、命令反馈，以及遗留的 POST 聊天接口"

COMPACT_SYSTEM_PROMPT = """你是一个对话压缩器。你的任务是把当前 Agent 会话压缩成一段高密度中文摘要，供系统作为后续上下文继续使用。

要求：
1. 保留用户目标、未完成事项、关键约束、重要偏好、当前代码状态、失败尝试与结论。
2. 保留仍然有效的文件路径、命令、配置状态、MCP/Plugin/Skill/服务状态。
3. 图片或多模态内容只能转述其与任务相关的信息，不要保留无关细节。
4. 工具调用必须折叠成“做了什么、结果是什么、后续影响是什么”。
5. 不要写寒暄，不要写面向用户的话，不要写 Markdown 标题。
6. 输出必须是单段或少量短段落纯文本，直接可作为系统上下文拼接。"""


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


async def _get_checkpoint_messages() -> list:
    if state.checkpointer is None:
        return []
    cfg = {"configurable": {"thread_id": state.THREAD_ID}}
    checkpoint_tuple = await state.checkpointer.aget_tuple(cfg)
    if checkpoint_tuple is None:
        return []
    checkpoint = getattr(checkpoint_tuple, "checkpoint", None)
    if not isinstance(checkpoint, dict):
        return []
    values = checkpoint.get("channel_values") or {}
    messages = values.get("messages") or []
    return list(messages) if isinstance(messages, list) else []


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
        f"Thinking: {'on' if conf.THINKING_ENABLED else 'off'}",
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
    admin_runtime.save_config({"public": {"THINKING_ENABLED": bool(enabled)}})
    await rebuild_runtime(reset_dialog=False, no_initial_chat=True)
    return f"Thinking 已{'开启' if enabled else '关闭'}"


async def _session_token_summary() -> str:
    messages = await _get_checkpoint_messages()
    if not messages:
        return "当前会话没有可统计的上下文。"
    chat_model = _build_chat_model(model_name=conf.CHAT_MODEL)
    try:
        total = chat_model.get_num_tokens_from_messages(messages)
        return f"当前会话 messages={len(messages)}，估算 tokens={total}"
    except NotImplementedError:
        joined = "\n\n".join(_message_to_plain_text(message) for message in messages)
        try:
            total = chat_model.get_num_tokens(joined)
            return f"当前会话 messages={len(messages)}，估算 tokens={total}（按纯文本降级估算）"
        except Exception:
            approx = max(1, len(joined.encode("utf-8")) // 4)
            return f"当前会话 messages={len(messages)}，估算 tokens≈{approx}（按字节降级估算）"


async def _clear_current_session() -> str:
    if state.checkpointer is not None and hasattr(state.checkpointer, "adelete_thread"):
        await state.checkpointer.adelete_thread(str(state.THREAD_ID))
    info = await rebuild_runtime(reset_dialog=True, no_initial_chat=False)
    return f"已清空当前会话并重建运行时。ready={info.get('ready')} status={info.get('status')}"


async def _compact_session_stream(websocket: WebSocket) -> str:
    messages = await _get_checkpoint_messages()
    if not messages:
        return "当前会话没有可压缩的上下文。"
    transcript = "\n\n".join(_message_to_plain_text(message) for message in messages)
    llm = _build_chat_model(model_name=conf.CHAT_MODEL)
    chunks: list[str] = []
    payload = [
        SystemMessage(content=COMPACT_SYSTEM_PROMPT),
        HumanMessage(content=transcript),
    ]
    async for chunk in llm.astream(payload):
        delta = state.message_content_to_text(getattr(chunk, "content", ""))
        if not delta:
            continue
        chunks.append(delta)
        await websocket.send_text(json.dumps({"type": "delta", "content": delta}, ensure_ascii=False))
    summary = "".join(chunks).strip()
    if not summary:
        raise RuntimeError("对话压缩结果为空")
    await _replace_session_with_summary(summary)
    return summary


async def _replace_session_with_summary(summary: str) -> None:
    if state.checkpointer is None:
        raise RuntimeError("checkpointer 未初始化，无法压缩会话")
    if hasattr(state.checkpointer, "adelete_thread"):
        await state.checkpointer.adelete_thread(str(state.THREAD_ID))
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"]["messages"] = [
        SystemMessage(content=f"你的压缩后的上下文:\n{summary}")
    ]
    checkpoint["updated_channels"] = ["messages"]
    config = {
        "configurable": {
            "thread_id": str(state.THREAD_ID),
            "checkpoint_ns": "",
        }
    }
    metadata = {
        "source": "compact",
        "step": 0,
        "parents": {},
        "ls_integration": "faust_slash_compact",
    }
    await state.checkpointer.aput(config, checkpoint, metadata, {"messages": 1})


async def _handle_slash_command(text: str, websocket: WebSocket | None = None) -> tuple[bool, str]:
    if not _is_slash_command(text):
        return False, ""
    name, arg = _parse_slash_command(text)
    if not name:
        return True, "空命令"
    if name == "thinking":
        lowered = arg.lower()
        if lowered not in {"on", "off"}:
            return True, "用法: /thinking on 或 /thinking off"
        return True, await _set_thinking_enabled(lowered == "on")
    if name == "status":
        return True, await _collect_status_summary()
    if name == "session":
        return True, await _session_token_summary()
    if name == "clear":
        return True, await _clear_current_session()
    if name == "compact":
        if websocket is None:
            return True, "POST 接口暂不支持 /compact，请使用 WebSocket 聊天接口。"
        return True, await _compact_session_stream(websocket)
    return True, f"未知命令: /{name}"


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
        pm = getattr(state, 'plugin_manager', None)
        if pm:
            results = pm._call_pluggy_hook('message_received', msg=text, history=[], ctx=None)
            if results:
                for r in results:
                    if r is not None and isinstance(r, str):
                        text = r
                        break
        resp = await invoke_agent_locked(state.agent, {"messages": [{"role": "user", "content": text}]})
        reply = state.message_content_to_text(resp["messages"][-1].content)
        if pm:
            post_results = pm._call_pluggy_hook('message_sent', msg=text, response=reply, ctx=None)
            if post_results:
                for r in post_results:
                    if r is not None:
                        reply = r
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
    agent_task: asyncio.Task | None = None

    async def _run_agent_stream(text: str):
        reply = ""
        abort_evt = state.reset_abort_event()
        pm = getattr(state, 'plugin_manager', None)
        if pm:
            results = pm._call_pluggy_hook('message_received', msg=text, history=[], ctx=None)
            if results:
                for r in results:
                    if r is not None and isinstance(r, str):
                        text = r
                        break
        try:
            async for event in stream_chat_agent_events(
                state.agent,
                {"messages": [{"role": "user", "content": text}]},
                abort_event=abort_evt,
            ):
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "reasoning_delta":
                    await websocket.send_text(json.dumps({
                        "type": "reasoning_delta",
                        "content": event.get("content", ""),
                    }, ensure_ascii=False))
                    continue
                if event.get("type") == "delta":
                    delta_text = state.message_content_to_text(event.get("content"))
                    if not delta_text:
                        continue
                    reply += delta_text
                    log.debug("聊天增量: %s", delta_text[:80])
                    await websocket.send_text(json.dumps({"type": "delta", "content": delta_text}, ensure_ascii=False))
                    continue
                if event.get("type") in {"tool_start", "tool_result"}:
                    await websocket.send_text(json.dumps(event, ensure_ascii=False))
            schedule_memory_record_sync(text, reply)
            if pm:
                post_results = pm._call_pluggy_hook('message_sent', msg=text, response=reply, ctx=None)
                if post_results:
                    for r in post_results:
                        if r is not None:
                            reply = r
            await websocket.send_text(json.dumps({"type": "done", "reply": reply}, ensure_ascii=False))
            log.debug("聊天流结束")
        except asyncio.CancelledError:
            await websocket.send_text(json.dumps({"type": "interrupted"}, ensure_ascii=False))
            log.info("聊天流被用户中断")
        return reply

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
                await websocket.send_text(json.dumps({"type": "interrupt_ack"}, ensure_ascii=False))
                continue

            text = None
            if isinstance(payload, dict):
                text = payload.get("text") or payload.get("message")
            if not text:
                await websocket.send_text(json.dumps({"type": "error", "error": "no text provided"}, ensure_ascii=False))
                continue
            handled, command_reply = await _handle_slash_command(text, websocket)
            if handled:
                await websocket.send_text(json.dumps({"type": "start"}, ensure_ascii=False))
                await websocket.send_text(json.dumps({"type": "done", "reply": command_reply}, ensure_ascii=False))
                continue
            if not state.RUNTIME_READY or state.agent is None:
                await websocket.send_text(json.dumps({"type": "error", "error": state.runtime_not_ready_message(), "runtime": state.runtime_status_payload()}, ensure_ascii=False))
                continue

            # Cancel any running agent task before starting a new one
            if agent_task is not None and not agent_task.done():
                state.get_abort_event().set()
                agent_task.cancel()
                try:
                    await agent_task
                except asyncio.CancelledError:
                    pass

            try:
                araya_runtime.get_araya_runtime(refresh=True).mark_main_agent_activity()
                events.ignore_trigger_event.set()
                await websocket.send_text(json.dumps({"type": "start"}, ensure_ascii=False))
                log.info("收到聊天消息: %s", text[:100])
                agent_task = asyncio.create_task(_run_agent_stream(text))
                agent_task.add_done_callback(lambda _: events.ignore_trigger_event.clear())
            except Exception as e:
                events.ignore_trigger_event.clear()
                log.error("Chat WebSocket 错误: %s", e)
                await websocket.send_text(json.dumps({"type": "error", "error": state.format_chat_error(e)}, ensure_ascii=False))
    except WebSocketDisconnect:
        log.info("Chat WebSocket 断开")

@router.websocket("/faust/command")
async def command_websocket(websocket: WebSocket):
    await websocket.accept()
    backend2frontend.FrontEndSay("Hello World! 你好,世界!")
    nimble.push_persistent_sessions_to_frontend()
    try:
        while True:
            if backend2frontend.hasFrontEndTask():
                task = await backend2frontend.popFrontEndTask()
                log.debug("从 backend2frontend 队列发送前端任务: %s", task[:80] if isinstance(task, str) else str(task)[:80])
                if task:
                    await websocket.send_text(task)
            if trigger_manager.has_queue_task() and not events.ignore_trigger_event.is_set():
                if not state.RUNTIME_READY or state.agent is None:
                    await asyncio.sleep(0.1)
                    continue
                task = trigger_manager.get_next_trigger()
                trigger_text = f"<Trigger>触发器唤醒了你，请根据触发器内容执行相应操作。{str(task)}"
                if isinstance(task, dict):
                    ttype = task.get("type")
                    callback_id = task.get("callback_id")
                    if ttype == "event" and task.get("event_name") == "nimble_result" and callback_id:
                        result = (task.get("payload") or {}).get("result")
                        trigger_text = f"<Trigger>灵动交互窗口收到用户提交。callback_id={callback_id}，用户结果={result}。请继续处理。"
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
                    elif ttype == "nimble-reminder" and callback_id:
                        session = nimble.get_nimble_session(callback_id)
                        if not session:
                            continue
                        trigger_text = f"<Trigger>灵动交互窗口仍在等待用户操作。callback_id={callback_id}，标题={session.get('title')}，提醒说明={task.get('recall_description') or session.get('recall_text')}。请判断是否需要继续引导用户。"
                    elif ttype == "nimble-expire" and callback_id:
                        session = nimble.close_nimble_session(callback_id, reason="expired")
                        if session:
                            trigger_manager.delete_trigger(session["result_trigger_id"])
                            trigger_manager.delete_trigger(session["reminder_trigger_id"])
                            trigger_manager.delete_trigger(session["expire_trigger_id"])
                            backend2frontend.FrontEndCloseNimbleWindow({"callback_id": callback_id, "reason": "expired"})
                        trigger_text = f"<Trigger>灵动交互窗口已过期关闭。callback_id={callback_id}。如有必要，请重新创建更明确的新窗口。"
                log.info('触发器激活，正在调用 Agent: %s', trigger_text[:120])
                resp = await invoke_agent_locked(state.agent, {"messages": [{"role": "user", "content": trigger_text}]})
                reply = resp["messages"][-1].content
                log.debug('触发器激活回复: %s', str(reply)[:120])
                if "<NO_TTS_OUTPUT>" in reply:
                    continue
                await websocket.send_text(f"SAY {reply}")
            try:
                command = await asyncio.wait_for(state.forward_queue.get(), timeout=0.01)
                log.debug("从队列转发命令: %s", command[:80])
                await websocket.send_text(f"{command}")
            except asyncio.TimeoutError:
                pass
            await asyncio.sleep(0.05)
    except WebSocketDisconnect:
        log.info("Command WebSocket 断开")
    except Exception as e:
        log.error("Command WebSocket 错误: %s", e)
        try:
            await websocket.send_text(f"SAY COMMAND LOOP ERROR::{e}")
        except WebSocketDisconnect:
            log.warning("Command WebSocket 报告错误时已断开")
        except RuntimeError as send_error:
            log.warning("Command WebSocket 在错误报告前已关闭: %s", send_error)


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
