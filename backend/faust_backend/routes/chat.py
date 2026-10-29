import json
import asyncio
import time
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from faust_backend.core.message import (
    format_chat_error,
    message_content_to_text,
)
import faust_backend.core.frontend.api as backend2frontend
import faust_backend.core.events as events
import faust_backend.nimble as nimble
import faust_backend.agent.araya.runtime as araya_runtime
import faust_backend.agent.trigger as trigger_manager
import faust_backend.agent.runtime.turn.gate as turn_gate
import faust_backend.admin_runtime as admin_runtime
import faust_backend.core.config as conf
import faust_backend.service_manager as service_manager
import faust_backend.agent.skill as skill_manager
from faust_backend.agent.runtime import state
from faust_backend.agent.subagent import events as subagent_events
from faust_backend.agent.runtime.turn import state as turn_state
from faust_backend.agent.runtime.turn import presence as frontend_presence
from faust_backend.agent.runtime import scheduler as agent_scheduler
from faust_backend.agent.runtime.build import _build_chat_model
from faust_backend.agent.runtime.invoke import stream_chat_agent_events, stream_silent_reply
from faust_backend.agent.runtime.rebuild import rebuild_runtime, schedule_memory_record_sync
from faust_backend.agent.runtime.turn.session_stats import collect_session_stats, get_checkpoint_messages
from faust_backend.mcp_manager import get_mcp_manager
from faust_backend.core.log import get_logger
from faust_backend.agent.runtime.turn.output_store import reset_output_store

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
    text = message_content_to_text(content)
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


def _busy_rebuild_note(info: dict) -> str:
    """配置已保存但运行时没重建成功时的统一说明（不能假装生效了）。"""
    return (
        f"；但主 Agent 正忙（{info.get('error') or '锁被占用'}），"
        "运行时未重建，将在下次重建时生效"
    )


async def _set_thinking_enabled(enabled: bool) -> str:
    # 全局思考由 REASONING_CONFIG 驱动：off 关闭，on 恢复 medium。
    current = str(getattr(conf, "REASONING_CONFIG", "medium") or "medium")
    next_value = "medium" if enabled and current == "off" else current
    if not enabled:
        next_value = "off"
    admin_runtime.save_config({"public": {"REASONING_CONFIG": next_value}})
    info = await rebuild_runtime(reset_dialog=False, no_initial_chat=True)
    note = _busy_rebuild_note(info) if info.get("status") == "busy" else ""
    return f"Thinking 已{'开启' if enabled else '关闭'} (REASONING_CONFIG={next_value}){note}"


async def _set_reasoning_effort(level: str) -> str:
    level = str(level or "").strip().lower()
    if level not in {"off", "low", "medium", "high"}:
        return "用法: /effort off|low|medium|high"
    admin_runtime.save_config({"public": {"REASONING_CONFIG": level}})
    info = await rebuild_runtime(reset_dialog=False, no_initial_chat=True)
    note = _busy_rebuild_note(info) if info.get("status") == "busy" else ""
    return f"Reasoning effort 已设置为 {level}{note}"


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
        f"API 格式: {stats.get('api_format') or 'openai_chat'}",
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
        if stats.get("cache_creation_tokens"):
            lines.append(f"缓存写入(cache_creation)={stats['cache_creation_tokens']}")
        if stats.get("reasoning_tokens"):
            lines.append(f"推理(reasoning)={stats['reasoning_tokens']}")
        lines.append(
            f"上下文长度={stats['context_length']} 触发阈值={stats['threshold_tokens']} "
            f"({stats['compact_threshold_ratio']:.0%}) 当前占用={stats['percent']:.1f}%"
        )
    return "\n".join(lines)


async def _clear_session_locked() -> str:
    """在调度器 exclusive 回合内（锁已持有）清空会话并重建运行时。"""
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


async def _clear_current_session() -> str:
    """清空当前会话。

    整个「清数据 + 重建」在**同一个调度器 exclusive 回合**里完成：先删 checkpoint
    再发现拿不到锁，会得到「会话已清掉、运行时没重建」的半成品状态。拿不到执行位
    由 ``run_exclusive`` 统一报"主 Agent 正忙"。
    """
    return await _clear_session_locked()


async def _compact_session_stream(websocket: WebSocket) -> str:
    """手动 /compact：调用与 auto compact 同一个 middleware，并流式推送卡片。

    与 auto compact 的唯一区别是「无视阈值强制压一次」；压缩逻辑、提示词、
    缓存策略完全共用，避免两套实现漂移。

    本函数只在调度器的 exclusive 回合里被调用（``_run_compact_command``），
    锁由 worker 持有；修复前手动路径完全不加锁，会与进行中的 turn 并发改写
    同一 thread。
    """
    middleware = state.compact_middleware
    if middleware is None:
        raise RuntimeError("会话压缩中间件未就绪（运行时可能未成功重建）")

    async def _notice(text: str) -> None:
        """无需压缩时也要给出一张卡片，避免 /compact 看起来毫无反应。"""
        await _safe_send(websocket, _main_event_payload("compact_start"))
        await _safe_send(websocket, _main_event_payload("compact_delta", content=text))
        await _safe_send(websocket, _main_event_payload("compact_done"))

    async def _on_delta(text: str) -> None:
        # 走 _safe_send：连接中途断开时不能抛异常，否则会顺着 _acompact →
        # force_compact 冒出去，让已经推送的压缩卡片停在无终态的状态。
        await _safe_send(websocket, _main_event_payload("compact_delta", content=text))

    messages = await get_checkpoint_messages()
    if not messages:
        await _notice("当前会话没有可压缩的上下文。")
        return "当前会话没有可压缩的上下文。"

    await _safe_send(websocket, _main_event_payload("compact_start"))

    update = await middleware.force_compact(messages, on_delta=_on_delta)
    if not update:
        await _safe_send(
            websocket,
            _main_event_payload(
                "compact_delta", content="当前会话没有可压缩的历史轮次。"
            ),
        )
        await _safe_send(websocket, _main_event_payload("compact_done"))
        return "当前会话没有可压缩的历史轮次。"

    # 通过 graph 的 aupdate_state 应用更新：它会走 add_messages reducer，
    # 因此 RemoveMessage(REMOVE_ALL_MESSAGES) 与顺序重排都能被正确执行。
    # as_node 必须显式指定：create_agent 编译出的图有 model/tools 两个节点，
    # 不指定时 langgraph 无法判断该写入归属哪个节点（Ambiguous update）。
    config = {"configurable": {"thread_id": str(state.THREAD_ID)}}
    await state.agent.aupdate_state(config, update, as_node="model")
    await _safe_send(websocket, _main_event_payload("compact_done"))
    return "对话压缩完成。"


async def _run_slash_command_stream(websocket: WebSocket, factory) -> None:
    """后台执行一条会占用 Agent / 重建运行时的斜杠命令，并自行推送事件流。

    修复前这些命令在 WS 接收循环里内联 await：一旦要等主 Agent 锁（/clear
    /thinking /effort 都会重建运行时），整个输入循环连 interrupt 一起冻住。
    现在它作为 ``exclusive`` 档回合提交给调度器（与 user 同档、不可被抢占），
    由调度器统一排队并持锁——重建运行时不再是"锁外插队"。
    """
    turn_id = turn_gate.new_turn_id()
    if state.subagent_manager and state.subagent_manager.consume_status_dirty():
        await _safe_send(websocket, _subagents_summary_payload())
    await _safe_send(
        websocket, _main_event_payload("start", turn_id=turn_id, origin="command")
    )
    try:
        reply = await _scheduler().run_exclusive(factory, label="exclusive")
    except agent_scheduler.LockBusyError:
        # 主 Agent 被外部持锁方（管理路由的 rebuild）占着：给出人话而不是错误事件
        log.warning("斜杠命令未取得主 Agent 锁（正忙）")
        reply = "主 Agent 正忙（可能有回合正在执行），已放弃该命令，请稍后重试。"
    except Exception as e:  # noqa: BLE001
        log.error("斜杠命令后台执行失败: %s", e, exc_info=True)
        # 前端在 error 分支即释放 currentChatRequest；此时再补 done 反而会另开一个空会话
        await _safe_send(
            websocket, _main_event_payload("error", error=format_chat_error(e))
        )
        return
    if reply:
        await _safe_send(
            websocket, _main_event_payload("delta", content=reply, turn_id=turn_id)
        )
    await _safe_send(websocket, _main_event_payload("done", turn_id=turn_id))


async def _run_compact_command(websocket: WebSocket) -> None:
    """后台执行 /compact：压缩卡片自带 compact_start/done，这里只补终结事件。"""
    try:
        await _scheduler().run_exclusive(
            lambda: _compact_session_stream(websocket), label="exclusive:compact"
        )
    except Exception as e:  # noqa: BLE001
        log.error("手动会话压缩失败: %s", e, exc_info=True)
        reason = format_chat_error(e)
        # 把失败原因一并塞进 compact_done：前端据此把卡片标成失败并显示原因，
        # 否则卡片在失败时照样显示"完成"（ok 字段此前完全没人读）。
        await _safe_send(
            websocket, _main_event_payload("compact_done", ok=False, error=reason)
        )
        await _safe_send(websocket, _main_event_payload("error", error=reason))
    else:
        # 压缩卡片已由 compact_done 收尾，这里再补终结事件释放前端请求状态
        await _safe_send(websocket, _main_event_payload("done"))


async def _dispatch_runtime_command(websocket: WebSocket | None, factory) -> tuple[bool, str | None]:
    """会重建运行时的命令：有前端连接时转后台任务，没有连接（POST 接口）时同步返回文本。

    两条路径都在调度器里以 ``exclusive`` 档执行（与 user 同档、不可被抢占）：
    重建运行时改写的正是主 Agent 的同一份状态，不能绕过唯一执行位。
    """
    if websocket is None:
        return True, await _scheduler().run_exclusive(factory, label="exclusive")
    asyncio.create_task(_run_slash_command_stream(websocket, factory))
    return True, None


async def _handle_slash_command(text: str, websocket: WebSocket | None = None) -> tuple[bool, str | None]:
    if not _is_slash_command(text) or text.startswith("/skill:"):
        return False, ""
    name, arg = _parse_slash_command(text)
    if not name:
        return True, "空命令"
    if name == "thinking":
        lowered = arg.lower()
        if lowered not in {"on", "off"}:
            return True, "用法: /thinking on 或 /thinking off"
        return await _dispatch_runtime_command(
            websocket, lambda: _set_thinking_enabled(lowered == "on")
        )
    if name == "effort":
        return await _dispatch_runtime_command(
            websocket, lambda: _set_reasoning_effort(arg)
        )
    if name == "status":
        return True, await _collect_status_summary()
    if name == "session":
        return True, await _session_token_summary()
    if name == "clear":
        return await _dispatch_runtime_command(websocket, _clear_current_session)
    if name == "compact":
        if websocket is None:
            return True, "POST 接口暂不支持 /compact，请使用 WebSocket 聊天接口。"
        # 转后台执行：/compact 要等主 Agent 锁、并且真的改写 checkpoint，内联在接收
        # 循环里会把整个输入（连同 interrupt）一起冻住，且与进行中的 turn 抢同一份状态。
        # 它自行推送 compact_* 卡片流，所以上层不能再发通用 start（会重置前端
        # entries，把刚推送的压缩卡片清掉）；终结事件由 _run_compact_command 保证。
        asyncio.create_task(_run_compact_command(websocket))
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


async def _safe_send(websocket: WebSocket, payload: dict) -> bool:
    """向 websocket 发送一个事件；连接已断开时只记录，不抛异常。

    发送失败若往外抛，会顺着接收循环冒出去而杀死整条连接；而前端此时还在等
    终结事件，输入框就永久卡死了。凡是"必须发出"的通知都走这里。
    """
    try:
        await websocket.send_text(json.dumps(payload, ensure_ascii=False))
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "向 websocket 发送 %s 失败（连接可能已关闭）: %s", payload.get("type"), exc
        )
        return False


# 活跃的 /faust/chat websocket 连接，供前台触发器流式推送复用。
# 用 dict（插入有序）而不是 set：前台触发器推给"最新接上的那条连接"，
# 修复前 next(iter(set)) 在多连接时是随机挑一条。
_active_chat_websockets: dict = {}


def _scheduler():
    """主 Agent 调度器（唯一仲裁执行权的组件，S1）。"""
    return agent_scheduler.get_scheduler()


_KIND_INTERRUPT = agent_scheduler.TurnKind.INTERRUPT
_KIND_BATCH = agent_scheduler.TurnKind.BATCH


def _trigger_kind(item) -> "agent_scheduler.TurnKind":
    """触发器 payload → 档位（§1.1：静默由 sink 决定，不再是档位）。"""
    priority = str((item or {}).get("priority") or "normal") if isinstance(item, dict) else "normal"
    if priority == "interrupt":
        return agent_scheduler.TurnKind.INTERRUPT
    if priority == "batched":
        return agent_scheduler.TurnKind.BATCH
    return agent_scheduler.TurnKind.TRIGGER


def _trigger_label(kind) -> str:
    """档位名（快照里的 ``kind``）：trigger / batch / interrupt。"""
    return str(agent_scheduler.TurnKind(kind).name.lower())


def _submit_trigger_request(
    trigger_text: str,
    chat_ws,
    *,
    origin: str,
    label: str,
    kind,
    merge_key: str | None = None,
    gate: str = "playback",
) -> None:
    """把一个触发器请求非阻塞地交给调度器（入口不再 await 执行）。

    ``owner=None``：触发器回合**不绑定任何一个连接**（D19-b）——一旦开跑就跑完，
    不因 chat 连接断开被停；这也正是与用户回合（D21）刻意不对称的地方。
    没有 chat 连接时 sink 降级为 silent（既有行为，只是从"执行前判断"变成标志）。
    """
    if chat_ws is not None:
        async def _run() -> None:
            await _run_trigger_stream_frontend(chat_ws, trigger_text)
    else:
        async def _run() -> None:
            await _run_trigger_silent(trigger_text, origin=origin)

    _scheduler().submit(agent_scheduler.AgentRequest(
        kind,
        _run,
        label=label,
        sink="chat" if chat_ws is not None else "silent",
        gate=gate,
        merge_key=merge_key,
    ))


def _latest_chat_ws():
    """前台触发器流式推送的目标连接（最新接入的那条）。"""
    return next(reversed(_active_chat_websockets), None)


async def _force_interrupt_trigger(websocket) -> bool:
    """用户插话：抢占正在占用执行位的触发器/批量回合（AUTO_FORCE_INTERRUPT）。

    ``AUTO_FORCE_INTERRUPT`` 是既有、已测、用户可见的行为，粒度沿用现状：
    用 ``abort_event`` + ``task.cancel()``，只升级"谁来仲裁"（调度器）。
    抢占标记打在**触发器流的目标连接**上——前台触发器流读的正是它的
    ``force_interrupting``，据此把终态从 ``interrupted`` 换成 ``done(forced)``。
    """
    from faust_backend.agent.runtime import scheduler as agent_scheduler

    scheduler = _scheduler()
    kinds = {agent_scheduler.TurnKind.TRIGGER, agent_scheduler.TurnKind.BATCH}
    if not scheduler.has_running_kinds(kinds):
        return False
    target = _latest_chat_ws() or websocket
    log.info("用户插话：抢占正在占用执行位的触发器回合")
    turn = turn_state.get_turn_state(target)
    turn.force_interrupting = True
    try:
        await scheduler.cancel_running_kinds(kinds, reason="user_interjection")
    finally:
        turn.force_interrupting = False
    return True


async def _apply_user_interjection(text: str, websocket) -> str:
    """AUTO_FORCE_INTERRUPT 开启时，若主 Agent 正被触发器占用：
    强制取消触发器任务，并给用户消息加"(用户插话)"标记。"""
    if not conf.AUTO_FORCE_INTERRUPT:
        return text
    if await _force_interrupt_trigger(websocket):
        return f"(用户插话){text}"
    return text


async def _run_trigger_stream_frontend(chat_ws, trigger_text: str) -> None:
    """前台触发器（sink=chat）流式推送；推送失败时降级为静默执行。"""
    turn_id = turn_gate.new_turn_id()
    try:
        await chat_ws.send_text(json.dumps(
            _main_event_payload("start", turn_id=turn_id, origin="trigger_foreground"),
            ensure_ascii=False))
        await _run_agent_stream(chat_ws, trigger_text, origin='trigger_foreground', turn_id=turn_id)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.error("触发器流式推送失败，降级为静默执行: %s", e)
        await _run_trigger_silent(trigger_text, origin='trigger_background')


async def _run_trigger_silent(trigger_text: str, origin: str = 'trigger_background') -> str:
    """silent sink（后台触发器 / batched 批量）：只执行，不推前端。

    插件钩子（记忆检索/情绪向量）与 Agent 调用都可能失败（如 LLM 传输层异常），
    由调度器统一落成该回合的 error 终态，不再炸掉命令 WS 循环。
    """
    text = await _apply_plugin_message_hooks(trigger_text, origin=origin)
    if text == "__IGNORED__":
        log.info('触发器消息已被插件拦截，跳过执行: %s', trigger_text[:80])
        return ""
    return await stream_silent_reply({"messages": [{"role": "user", "content": text}]})


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


async def _run_agent_stream(websocket: WebSocket, text: str, agent=None, origin: str = 'user',
                            turn_id: str | None = None) -> str:
    reply = ""
    # 本连接这一轮的中断信号：是连接级状态，别的连接的 interrupt 打不到这里
    turn = turn_state.get_turn_state(websocket)
    abort_evt = turn.begin_turn()
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
                    delta_text = message_content_to_text(event.get("content"))
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
        # 登记播放闸门: 必须在 done 发出之前 arm, 否则前端可能在登记之前就把 ack 送回来
        if turn_id:
            turn_gate.arm(turn_id, len(reply or ""), websocket)
        done_payload = _main_event_payload("done", turn_id=turn_id)
        if pm:
            await pm._call_pluggy_hook('agent_event_sent', event=done_payload, current_history=current_history, ctx=None)
        await websocket.send_text(json.dumps(done_payload, ensure_ascii=False))
        log.debug("聊天流结束")
    except asyncio.CancelledError:
        # 生产者由 stream_chat_agent_events 自己的 finally 取消并等待（主 Agent 锁
        # 在那里释放）。这里不再全进程扫描同名 producer —— 修复前它会把其它连接
        # 正在跑的流一并取消，被取消者若停在等锁处就再没有终结事件，那条流永久转圈。
        if turn.force_interrupting:
            # 用户插话强制打断前台触发器：补发 done(forced)，前端据此清理触发器会话
            await websocket.send_text(json.dumps(_main_event_payload("done", forced=True), ensure_ascii=False))
        else:
            await websocket.send_text(json.dumps(_main_event_payload("interrupted"), ensure_ascii=False))
            log.info("聊天流被用户中断")
    except Exception as e:
        log.error("Chat agent stream 错误: %s", e, exc_info=True)
        try:
            await websocket.send_text(json.dumps(_main_event_payload("error", error=format_chat_error(e)), ensure_ascii=False))
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
        from faust_backend.agent.runtime import scheduler as agent_scheduler

        handle = _scheduler().submit(agent_scheduler.AgentRequest(
            agent_scheduler.TurnKind.USER,
            lambda: stream_silent_reply({"messages": [{"role": "user", "content": text}]}),
            label="user",
            sink="silent",          # 没有连接可推：POST 只回文本
            gate="exempt",
            on_terminal=lambda _h: events.ignore_trigger_event.clear(),
        ))
        try:
            reply = await handle.wait()
        except RuntimeError as e:
            if "等待主 Agent 锁超时" in str(e):
                log.error("Chat POST 获取主 Agent 锁超时: %s", e)
                return JSONResponse(
                    status_code=503,
                    content={"error": str(e), "busy": True},
                )
            raise
        if not reply:
            raise RuntimeError()
        schedule_memory_record_sync(text, reply)
        log.info('Chat POST 回复完成')
        return {"reply": reply, "warning": "使用websocket /faust/chat接口以获得更好的前端流式体验和更低的延迟。"}
    except Exception as e:
        events.ignore_trigger_event.clear()
        log.error("Chat POST 错误: %s", e)
        return {"error": format_chat_error(e), "warning": "使用websocket /faust/chat接口以获得更好的前端流式体验和更低的延迟。"}


@router.websocket("/faust/chat")
async def chat_websocket(websocket: WebSocket):
    await websocket.accept()
    _active_chat_websockets[websocket] = None

    # ── WS 连接级别的 Subagent 事件转发 ──
    # 队列取自进程级广播器，而不是 state.subagent_manager：manager 会在每次
    # rebuild_runtime() 时被整体替换（lifecycle._rebuild_subagent_manager），
    # 若订阅 manager 实例内部队列，重建后本任务会永远监听一个孤儿队列，
    # 前端 Subagent Panel 的实时事件就此断流（而摘要栏仍正常，故表现为"有时行有时不行"）。
    _subagent_fwd_stop = asyncio.Event()
    _subagent_fwd_task: asyncio.Task | None = None
    _subagent_queue = subagent_events.subscribe()

    async def _push_subagents_summary_if_dirty() -> None:
        """Subagent 状态变化实时刷新摘要栏。

        原先只在主 Agent 流式响应过程中顺带推送，主 Agent 空闲时后台 Subagent
        的创建/运行/结束不会反映到摘要栏。
        """
        manager = state.subagent_manager
        if manager is not None and manager.consume_status_dirty():
            await websocket.send_text(
                json.dumps(_subagents_summary_payload(), ensure_ascii=False)
            )

    async def _forward_subagent_events():
        while not _subagent_fwd_stop.is_set():
            try:
                event = await asyncio.wait_for(_subagent_queue.get(), timeout=0.5)
                await websocket.send_text(json.dumps(event, ensure_ascii=False))
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break
            try:
                await _push_subagents_summary_if_dirty()
            except asyncio.CancelledError:
                break

    _subagent_fwd_task = asyncio.create_task(_forward_subagent_events())

    async def _stop_forward_task():
        nonlocal _subagent_fwd_task
        subagent_events.unsubscribe(_subagent_queue)
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
        except Exception as exc:
            # 连接已断时 send_text 会失败；这里只记录，不让 finally 再抛异常
            log.warning("Subagent 事件转发任务异常退出: %s", exc)
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
                # 只中止本连接这一轮：abort event 与当前任务都挂在本连接的 TurnState 上
                turn_state.get_turn_state(websocket).interrupt()
                await websocket.send_text(json.dumps(_main_event_payload("interrupt_ack"), ensure_ascii=False))
                continue

            # 播放闸门回报：前端在这一轮音频播完后 ack，触发器不必等满安全网。
            # 必须走 chat WS —— 前台触发器是在 command WS 的循环里 await 的，
            # ack 若走 command WS 会与消费循环互锁。
            if isinstance(payload, dict) and payload.get("type") == "turn_playback_done":
                ack_turn_id = str(payload.get("turn_id") or "")
                if turn_gate.note_playback_done(ack_turn_id):
                    log.info(
                        "回合播放结束 ack: turn_id=%s, 触发器闸门剩余 %.2fs",
                        ack_turn_id, turn_gate.stats().get("remaining_sec", 0.0),
                    )
                    # 立即唤醒 command WS 消费循环，不必等它的轮询间隔
                    events.backend2frontendQueue_event.set()
                continue

            text = None
            if isinstance(payload, dict):
                text = payload.get("text") or payload.get("message")
            if not text:
                await websocket.send_text(json.dumps(_main_event_payload("error", error="no text provided"), ensure_ascii=False))
                continue
            trigger_manager.note_user_interaction()
            try:
                handled, command_reply = await _handle_slash_command(text, websocket)
            except Exception as e:  # noqa: BLE001
                # 斜杠命令抛异常绝不能杀死接收循环：循环一退出 websocket 就断开，
                # 而前端仍在等终结事件，输入框会永久卡在"发送中"。
                log.error("斜杠命令处理失败: %s", e, exc_info=True)
                await _safe_send(
                    websocket,
                    _main_event_payload("error", error=format_chat_error(e)),
                )
                continue
            if handled:
                if command_reply is None:
                    # 命令已自行推送完整事件流（/compact 的压缩卡片），
                    # 不能再发通用 start，否则会重置前端 entries。
                    log.info("Slash Command 自行推送事件流（无通用回复）")
                    continue
                # Send subagent summary before start/done per requirement
                if state.subagent_manager and state.subagent_manager.consume_status_dirty():
                    await websocket.send_text(json.dumps(_subagents_summary_payload(), ensure_ascii=False))
                command_turn_id = turn_gate.new_turn_id()
                await websocket.send_text(json.dumps(
                    _main_event_payload("start", turn_id=command_turn_id, origin="command"),
                    ensure_ascii=False))
                await websocket.send_text(json.dumps(_main_event_payload("delta", content=command_reply, turn_id=command_turn_id), ensure_ascii=False))
                await websocket.send_text(json.dumps(_main_event_payload("done", turn_id=command_turn_id), ensure_ascii=False))
                log.info(f"Slash Command Result:{command_reply}")
                continue
            if not state.RUNTIME_READY or state.agent is None:
                await websocket.send_text(json.dumps(_main_event_payload("error", error=state.runtime_not_ready_message(), runtime=state.runtime_status_payload()), ensure_ascii=False))
                continue

            # 用户插话: 触发器占用主 Agent 时强制打断并标记消息 (AUTO_FORCE_INTERRUPT)。
            # 放在 ignore_trigger_event 置位之后，确保插话瞬间调度器不再放行触发器
            # 抢在用户消息前执行。
            try:
                from faust_backend.agent.runtime import scheduler as agent_scheduler

                araya_runtime.get_araya_runtime(refresh=True).mark_main_agent_activity()
                events.ignore_trigger_event.set()
                turn = turn_state.get_turn_state(websocket)
                text = await _apply_user_interjection(text, websocket)

                # 新用户消息抢占本连接上一条消息（既有语义：abort_event + 撤单/停跑）
                turn.abort_event.set()
                await _scheduler().cancel_owner(
                    websocket, reason="superseded_by_new_user_message"
                )

                user_turn_id = turn_gate.new_turn_id()
                await websocket.send_text(json.dumps(
                    _main_event_payload("start", turn_id=user_turn_id, origin="user"),
                    ensure_ascii=False))
                log.info("收到聊天消息: %s", text[:100])
                if not _scheduler().is_idle():
                    # 主 Agent 忙/排队中：立即向前端反馈，避免无提示转圈
                    await _safe_send(
                        websocket, _main_event_payload("pending", reason="waiting_lock")
                    )
                turn.turn_handle = _scheduler().submit(agent_scheduler.AgentRequest(
                    agent_scheduler.TurnKind.USER,
                    lambda: _run_agent_stream(websocket, text, turn_id=user_turn_id),
                    label="user",
                    sink="chat",
                    gate="exempt",          # 用户消息不适用播放闸门
                    preemptible=True,
                    owner=websocket,        # D21：用户回合随连接生死
                    on_terminal=lambda _h: events.ignore_trigger_event.clear(),
                ))
            except Exception as e:
                events.ignore_trigger_event.clear()
                log.error("Chat WebSocket 错误: %s", e)
                await websocket.send_text(json.dumps(_main_event_payload("error", error=format_chat_error(e)), ensure_ascii=False))
    except WebSocketDisconnect:
        log.info("Chat WebSocket 断开")
    finally:
        _active_chat_websockets.pop(websocket, None)
        # 连接断开 → 这一轮的 ack 永远不会来，立即释放闸门，别让触发器队列等到超时
        if turn_gate.release_for_websocket(websocket):
            log.info("Chat WebSocket 断开，触发器闸门已释放")
            events.backend2frontendQueue_event.set()
        # D21：用户回合随连接生死——撤掉本连接未开始的请求 + 停掉它在跑的回合。
        # 刻意不对称：触发器回合不绑定任何连接（D19-b），不在这里被停。
        await _scheduler().cancel_owner(websocket, reason="chat_ws_disconnect")
        turn_state.drop_turn_state(websocket)
        await _stop_forward_task()

@router.websocket("/faust/command")
async def command_websocket(websocket: WebSocket):
    await websocket.accept()
    # 在线信号由传输层写、由触发器域读（D19）：本连接是触发器唯一的消费通道，
    # 因此"前端在线"== 本连接在线，两者不可能漂移。
    frontend_presence.mark_online(websocket)
    backend2frontend.FrontEndSay("Hello World! 你好,世界!")
    nimble.push_persistent_sessions_to_frontend()

    # 断线探测：本循环是**只发不收**的（前端在 command WS 上从不发消息），因此
    # Starlette 永远不会从 receive 侧发现对端消失——进程被杀/网络断掉时连接会一直
    # 挂着，presence 停在"在线"，于是离线禁触（D19）失效、唤醒静默丢失。
    # 这里单独跑一个读任务，只为把断开变成可观测事件。
    disconnected = asyncio.Event()

    async def _watch_disconnect() -> None:
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # noqa: BLE001 - 断开/异常都按断开处理
            log.debug("command WS 读端结束: %s", exc)
        finally:
            disconnected.set()

    watcher = asyncio.create_task(_watch_disconnect(), name="command_ws_disconnect_watch")

    try:
        batch_buffer: list[tuple[float, dict]] = []  # (first_ts, item)
        while not disconnected.is_set():
            if backend2frontend.hasFrontEndTask():
                task = await backend2frontend.popFrontEndTask()
                log.debug("从 backend2frontend 队列发送前端任务: %s", task[:80] if isinstance(task, str) else str(task)[:80])
                if task:
                    await websocket.send_text(task)
            # 消费侧 batched 聚合：忙时积压，窗口到期或调度器空闲时打包提交
            for it in trigger_manager.drain_batched():
                batch_buffer.append((time.time(), it))
            if batch_buffer:
                first_ts = min(ts for ts, _ in batch_buffer)
                window_due = time.time() - first_ts >= trigger_manager.BATCH_WINDOW_SEC
                idle_now = (
                    not backend2frontend.hasFrontEndTask()
                    and _scheduler().is_idle()
                )
                if (
                    (window_due or idle_now)
                    and state.RUNTIME_READY and state.agent is not None
                ):
                    items = [it for _, it in batch_buffer]
                    batch_buffer = []
                    trigger_text = trigger_manager.format_batch_injection(items, first_ts)
                    log.info('批量触发器注入 %d 条: %s', len(items), trigger_text[:120])
                    _submit_trigger_request(trigger_text, None, origin='trigger_background',
                                            label=_trigger_label(_KIND_BATCH), kind=_KIND_BATCH)
            while state.RUNTIME_READY and state.agent is not None:
                # 非阻塞取用：准入（播放闸门 / ignore_trigger_event / 优先级）全部交给
                # 调度器，本循环只负责"把 inbox 里的 payload 变成一条请求"。
                item = trigger_manager.pop_next_trigger()
                if item is None:
                    break
                trigger_text = await trigger_manager.render_trigger_text(item)
                if trigger_text is None:
                    continue    # TTS 黑名单等"判定为不注入"
                is_dict = isinstance(item, dict)
                run_background = bool(item.get("run_background")) if is_dict else False
                kind = _trigger_kind(item)
                chat_ws = None if run_background else _latest_chat_ws()
                _submit_trigger_request(
                    trigger_text,
                    chat_ws,
                    origin='trigger_foreground' if chat_ws is not None else 'trigger_background',
                    label=_trigger_label(kind),
                    kind=kind,
                    merge_key=str(item.get("id")) if is_dict and item.get("id") else None,
                    gate="exempt" if kind == _KIND_INTERRUPT else "playback",
                )
            if not state.forward_queue.empty():
                command = await state.forward_queue.get()
                log.debug("从队列转发命令: %s", command[:80])
                await websocket.send_text(f"{command}")
                continue
            # inbox 已在上面被取空：这里等唤醒即可（触发器到达会 set 这个事件；
            # 超时 0.5s 也让"没有事件通知"的路径（定时触发器）照样被轮询到）。
            if not backend2frontend.hasFrontEndTask():
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
    finally:
        watcher.cancel()
        try:
            await watcher
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        frontend_presence.mark_offline(websocket)
        # D19-c：断开瞬间 inbox 里尚未消费的唤醒逐条记为 refused 后清空，
        # 否则回连时会冒出"断开前残留的几条"。
        trigger_manager.drain_inbox_as_refused()
        events.backend2frontendQueue_event.set()


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
