from faust_backend.utils import PerfTimer

_t = PerfTimer()

_t.begin("builtin")
import os
import asyncio
import time
from contextlib import asynccontextmanager
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
import faust_backend.vad_runtime as vad_runtime
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
                summary = pm.heartbeat_tick()
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


async def invoke_agent_locked(target_agent, payload, config=None):
    if config is None:
        config = {
            "configurable": {"thread_id": state.THREAD_ID},
            "recursion_limit": 300,
        }
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        log.debug("等待 Agent 锁")
        async with state.agent_lock:
            log.debug("开始调用 LLM")
            try:
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


async def stream_chat_agent_events(
    target_agent, payload, config=None, *, abort_event: asyncio.Event | None = None
):
    if config is None:
        config = {
            "configurable": {"thread_id": state.THREAD_ID},
            "recursion_limit": 300,
        }
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        log.debug("等待 Agent 锁")
        async with state.agent_lock:
            log.debug("开始调用 LLM")
            try:
                async for event in target_agent.astream_events(
                    payload, config=config, version="v2"
                ):
                    if not isinstance(event, dict):
                        continue
                    if abort_event and abort_event.is_set():
                        log.info("Agent stream aborted by user")
                        raise asyncio.CancelledError("User interrupted")
                    event_name = str(event.get("event") or "").strip().lower()
                    data = event.get("data") or {}
                    if event_name == "on_chat_model_stream":
                        chunk = data.get("chunk")
                        if not chunk or not state.is_ai_message_chunk(chunk):
                            continue
                        # Extract reasoning/thinking delta (OpenAI o1/o3, DeepSeek R1, etc.)
                        additional_kwargs = (
                            getattr(chunk, "additional_kwargs", {}) or {}
                        )
                        reasoning = (
                            additional_kwargs.get("reasoning_content")
                            or additional_kwargs.get("reasoning")
                            or additional_kwargs.get("think")
                        )
                        if reasoning:
                            yield {"type": "reasoning_delta", "content": reasoning}
                        delta_text = state.message_content_to_text(chunk.content)
                        if delta_text:
                            yield {"type": "delta", "content": delta_text}
                        continue
                    if event_name == "on_tool_start":
                        yield {
                            "type": "tool_start",
                            "tool_name": str(
                                event.get("name") or data.get("name") or "tool"
                            ).strip(),
                            "args": state.normalize_tool_args(data.get("input")),
                            "call_id": str(event.get("run_id") or ""),
                        }
                        continue
                    if event_name == "on_tool_end":
                        yield {
                            "type": "tool_result",
                            "tool_name": str(
                                event.get("name") or data.get("name") or "tool"
                            ).strip(),
                            "output": state.tool_value_to_text(data.get("output")),
                            "call_id": str(event.get("run_id") or ""),
                        }
                        continue
                log.debug("LLM 调用结束")
                return
            except Exception as e:
                if state.is_rate_limit_error(e) and attempt < max_attempts:
                    log.warning("429 限流，重试 attempt=%d/%d", attempt, max_attempts)
                else:
                    raise
        await _sleep_backoff(attempt)


def _compose_runtime_extensions():
    from faust_backend.runtime.mm_bridge import MultimodalBridgeMiddleware

    base_tools = list(llm_tools.get_tools_for_agent(state.AGENT_NAME))
    from faust_backend.mcp_manager import get_mcp_manager

    mcp_tools = get_mcp_manager().get_langchain_tools()
    if mcp_tools:
        base_tools.extend(mcp_tools)
    pm = state.plugin_manager
    tools = (
        pm.compose_tools(base_tools=base_tools, agent_name=state.AGENT_NAME)
        if pm
        else base_tools
    )
    middlewares = pm.compose_middlewares(agent_name=state.AGENT_NAME) if pm else []
    # Filter out any stale mm_bridge instances from old plugin state
    middlewares = [
        m for m in middlewares if not isinstance(m, MultimodalBridgeMiddleware)
    ]
    middlewares.append(MultimodalBridgeMiddleware())
    return tools, middlewares


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
    subagent_db_path = os.path.join(state.AGENT_ROOT, "subagents.db")
    if state.subagent_manager is not None:
        try:
            await state.subagent_manager.aclose()
        except Exception as exc:
            log.warning("关闭旧 SubagentManager 失败: %s", exc)
    manager = SubagentManager(checkpointerPath=subagent_db_path)
    manager.setChatModel(await _build_chat_model(model_name=model_name))
    _tools, middlewares = _compose_runtime_extensions()
    manager.setMiddlewares(middlewares)
    for name, toolset in _build_subagent_toolsets().items():
        manager.newToolset(name, toolset)
    manager.restore_persisted_state()
    state.subagent_manager = manager
    get_output_store()
    log.info("SubagentManager 已重建: %s", subagent_db_path)
    return manager


async def _build_chat_model(*, model_name: str):
    """Build a ChatOpenAI instance for the main agent from the ModelProviders.

    model_name 是 'provider::model' spec。thinking 参数由 provider 的
    thinking_type 驱动：thinking_type == "none" 时不思考（返回 ChatOpenAI），
    否则按 medium 强度（R5：与旧 THINKING_ENABLED 开关语义对齐）。
    """
    from faust_backend.provider import build_ReasoningChatOpenAI_from_spec
    from faust_backend.runtime import state as runtime_state
    providers = runtime_state.get_model_providers()
    spec = model_name or providers.main_model
    if not spec:
        raise RuntimeError("main_model is not configured (provider.private.json)")
    # 找到 spec 对应的 provider，取其 thinking_type 决定是否启用思考
    provider_name, _ = spec.split("::", 1)
    provider = next((p for p in providers.providers if p.name == provider_name), None)
    thinking = "medium" if provider and provider.thinking_type != "none" else None
    return await build_ReasoningChatOpenAI_from_spec(
        providers, spec=spec, intensity=thinking
    )


async def _create_agent_with_extensions(*, model_name: str, checkpointer):
    tools, mgmt_middlewares = _compose_runtime_extensions()
    tools = middleware.wrap_tools(tools)
    chat_model = await _build_chat_model(model_name=model_name)
    kwargs = {
        "model": chat_model,
        "checkpointer": checkpointer,
        "tools": tools,
    }
    if mgmt_middlewares:
        kwargs["middleware"] = mgmt_middlewares
        return create_agent(**kwargs)
    return create_agent(**kwargs)


async def rebuild_runtime(
    *, reset_dialog: bool = False, no_initial_chat: bool = False
):  # TODO: 让逻辑更加清晰
    log.info(
        "正在重建运行时，reset_dialog=%s, no_initial_chat=%s",
        reset_dialog,
        no_initial_chat,
    )
    _needs_initial_chat = False
    async with state.agent_lock:
        try:
            conf.reload_configs()
            os.environ["DEEPSEEK_API_KEY"] = conf.CHAT_API_KEY
            os.environ["SEARCHAPI_API_KEY"] = conf.SEARCH_API_KEY
            os.environ["OPENAI_API_KEY"] = conf.CHAT_API_KEY
            os.environ["OPENAI_BASE_URL"] = conf.CHAT_API_BASE
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
            await refresh_runtime_nodes(get_faustbot_vfs(refresh=True))

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
                plugin_reload = pm.reload(force=True)
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
                await invoke_agent_locked(
                    state.agent,
                    {"messages": [{"role": "system", "content": state.PROMPT}]},
                )
            else:
                await invoke_agent_locked(
                    state.agent,
                    {
                        "messages": [
                            {
                                "role": "user",
                                "content": (
                                    f"请继续按当前角色设定工作。\n 如果你需要重新了解你的角色设定，"
                                    f"请读取agents/{state.AGENT_NAME}/AGENT.md、ROLE.md、COREMEMORY.md、TASK.md等文件"
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_plugin_manager()
    from faust_backend.mcp_manager import get_mcp_manager

    mcp_manager = get_mcp_manager()

    # ── startup ──
    backend2frontend.set_main_loop(asyncio.get_running_loop())
    araya_runtime.get_araya_runtime(refresh=True)
    try:
        from faust_backend.memory import get_memory

        get_memory(refresh=True)
        await araya_runtime.get_araya_runtime(refresh=True).startup()
        startup_info = await rebuild_runtime(
            reset_dialog=False, no_initial_chat=bool(args.no_startup_chat)
        )
        log.info("启动运行时摘要: %s", startup_info)
    except Exception as e:
        state.agent = None
        state.set_runtime_state(ready=False, status="waiting_for_config", error=str(e))
        log.warning("启动运行时降级: %s", e)
    try:
        await vad_runtime.vad_runtime.startup()
        log.info("VAD 运行时已加载到 CPU")
    except Exception as e:
        log.warning("启动 VAD 初始化失败: %s", e)
    log.info("触发器看门狗线程正在启动...")
    trigger_manager.start_trigger_watchdog_thread()
    try:
        await nimble.restore_persistent_sessions()
    except Exception as e:
        log.warning("恢复持久化 Nimble 窗口失败: %s", e)
    if conf.config.get("MC_BRIDGE_ENABLED", False):
        try:
            await minecraft_client.ensure_started()
        except Exception as e:
            log.warning("Minecraft 桥启动时未连接: %s", e)
    else:
        log.info("Minecraft 桥未启用 (MC_BRIDGE_ENABLED=false)")

    if state.plugin_heartbeat_task is None:
        state.plugin_heartbeat_task = asyncio.create_task(_plugin_heartbeat_loop())
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
    await araya_runtime.get_araya_runtime(refresh=True).shutdown()
    await mcp_manager.stop_all()
    trigger_manager.exitflag = True
    await vad_runtime.vad_runtime.shutdown()
    for service in service_manager.get_service_keys():
        try:
            log.info("正在停止服务: %s", service)
            service_manager.stop_service(service)
        except Exception as e:
            log.error("停止服务 %s 失败: %s", service, e)
    log.info("正在关闭 FAUST 后端主服务...")
