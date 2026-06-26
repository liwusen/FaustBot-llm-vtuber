import json
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import faust_backend.backend2front as backend2frontend
import faust_backend.events as events
import faust_backend.nimble as nimble
import faust_backend.araya_runtime as araya_runtime
import faust_backend.trigger_manager as trigger_manager
import faust_backend.live_mode as live_mode
from faust_backend.runtime import state
from faust_backend.runtime.lifecycle import (
    invoke_agent_locked, stream_chat_agent_events, schedule_memory_record_sync,
)
from faust_backend.logger import get_logger

log = get_logger("faust.chat")

router = APIRouter(tags=["chat"])
router.description = "聊天/通信：WebSocket 流式聊天、命令转发、命令反馈，以及遗留的 POST 聊天接口"


@router.post("/faust/chat")
async def chat_post(payload: dict):
    text = None
    if isinstance(payload, dict):
        text = payload.get('text') or payload.get('message')
    if not text:
        return {"error": "no text provided"}
    if not state.RUNTIME_READY or state.agent is None:
        return {"error": state.runtime_not_ready_message(), "runtime": state.runtime_status_payload()}
    try:
        await asyncio.to_thread(araya_runtime.get_araya_runtime(refresh=True).mark_main_agent_activity)
        events.ignore_trigger_event.set()
        resp = await invoke_agent_locked(state.agent, {"messages": [{"role": "user", "content": text}]})
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
    agent_task: asyncio.Task | None = None

    async def _run_agent_stream(text: str):
        reply = ""
        abort_evt = state.reset_abort_event()
        try:
            async for event in stream_chat_agent_events(
                state.agent,
                {"messages": [{"role": "user", "content": text}]},
                abort_event=abort_evt,
            ):
                if not isinstance(event, dict):
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
                        result = nimble.get_nimble_result(callback_id, cleanup=False)
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
