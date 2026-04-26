from faust_backend.logger import get_logger

log = get_logger("faust.main")

from fastapi import FastAPI,WebSocket, WebSocketDisconnect, HTTPException, Query, UploadFile, File
import json
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
import uvicorn
import numpy as np
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
import faust_backend.config_loader as conf
import faust_backend.backend2front as backend2frontend
import os
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
import aiosqlite
import asyncio
import queue
import random
os.environ["DEEPSEEK_API_KEY"]=conf.CHAT_API_KEY
os.environ["SEARCHAPI_API_KEY"]=conf.SEARCH_API_KEY
os.environ["OPENAI_API_KEY"]=conf.CHAT_API_KEY
os.environ["OPENAI_BASE_URL"]=conf.CHAT_API_BASE
import faust_backend.llm_tools as llm_tools
from langchain.agents.middleware import HumanInTheLoopMiddleware,SummarizationMiddleware,TodoListMiddleware
from langgraph.store.sqlite import AsyncSqliteStore
from langgraph.store.memory import InMemoryStore
import faust_backend.trigger_manager as trigger_manager
import faust_backend.events as events
import faust_backend.nimble as nimble
import faust_backend.minecraft_client as minecraft_client
import faust_backend.admin_runtime as admin_runtime
import faust_backend.service_manager as service_manager
import faust_backend.kb_manager as kb_manager
import faust_backend.kb_api as kb_api
import faust_backend.araya_api as araya_api
import faust_backend.araya_runtime as araya_runtime
import faust_backend.plugin_market as plugin_market
import faust_backend.skill_manager as skill_manager
import faust_backend.speech_runtime as speech_runtime
import faust_backend.vad_runtime as vad_runtime
from faust_backend.plugin_system import PluginManager
import tqdm
from os.path import join as pjoin
from faust_backend.config_loader import args

# SQLite 持久化对象 — 模块级初始化，避免用 globals() 检查存在性
conn = None
checkpointer = None
conn_for_store = None
storer = None
import time
log.info("所有库加载完成")
#Shared Events
app = FastAPI()
uvicorn_server = None
kb_api.register_kb_routes(app)
araya_api.register_araya_routes(app)
import faust_backend.edge_tts_api as edge_tts_api
edge_tts_api.register_edge_tts_routes(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
PORT = 13900
os.chdir(os.path.dirname(os.path.abspath(__file__)))
forward_queue=asyncio.Queue()
agent=None
agent_lock = asyncio.Lock()
plugin_manager = PluginManager()
plugin_heartbeat_task = None
RUNTIME_READY = False
RUNTIME_STATUS = "starting"
RUNTIME_ERROR = ""
AGENT_NAME=conf.AGENT_NAME
PROMPT = ""
AGENT_ROOT=os.path.join("agents",f"{AGENT_NAME}")


def _set_runtime_state(*, ready: bool, status: str, error: str = ""):
    global RUNTIME_READY, RUNTIME_STATUS, RUNTIME_ERROR
    RUNTIME_READY = bool(ready)
    RUNTIME_STATUS = str(status or ("ready" if ready else "waiting_for_config"))
    RUNTIME_ERROR = str(error or "")
    llm_tools.STARTED = RUNTIME_READY


def _runtime_not_ready_message() -> str:
    base = "后端已启动，但 Agent 尚未就绪。请先在配置器中填写私密配置或修正 Agent 配置，然后执行重载。"
    detail = str(RUNTIME_ERROR or "").strip()
    if detail:
        return f"{base} 当前原因: {detail}"
    return base


def _runtime_status_payload() -> dict:
    return {
        "ready": RUNTIME_READY,
        "status": RUNTIME_STATUS,
        "error": RUNTIME_ERROR,
        "agent_name": AGENT_NAME,
        "agent_root": AGENT_ROOT,
        "private_config_missing": bool(conf.PRIVATE_CONFIG_WAS_MISSING),
        "private_config_auto_created": bool(conf.PRIVATE_CONFIG_AUTO_CREATED),
    }


def _ensure_agent_runtime_ready() -> None:
    if agent is None or not RUNTIME_READY:
        raise RuntimeError(_runtime_not_ready_message())


def makeup_init_prompt():
    global PROMPT, AGENT_ROOT, AGENT_NAME
    AGENT_NAME = conf.AGENT_NAME
    AGENT_ROOT=os.path.join("agents",f"{AGENT_NAME}")
    if not os.path.exists(AGENT_ROOT):
        PROMPT = ""
        raise FileNotFoundError(f"Agent file for '{AGENT_NAME}' not found. Please make sure 'agents/{AGENT_NAME}' exists.")
    with open(os.path.join(AGENT_ROOT,"AGENT.md"),"r",encoding="utf-8") as f:
        PROMPT=f.read()
    with open(os.path.join(AGENT_ROOT,"ROLE.md"),"r",encoding="utf-8") as f:
        PROMPT+=f.read()
    with open(os.path.join(AGENT_ROOT,"COREMEMORY.md"),"r",encoding="utf-8") as f:
        PROMPT+=f.read()
    with open(os.path.join(AGENT_ROOT,"TASK.md"),"r",encoding="utf-8") as f:
        PROMPT+=f.read()
try:
    makeup_init_prompt()
except Exception as e:
    log.warning("初始 Prompt 加载跳过: %s", e)
    _set_runtime_state(ready=False, status="waiting_for_config", error=str(e))

THREAD_ID=84
def startServices():
    if not args.no_run_other_backend_services:
        log.info("正在启动后端服务...")
        for service in tqdm.tqdm(service_manager.get_service_keys(), desc="[main]Starting services"):
            if service == "tts" and not speech_runtime.should_start_local_tts():
                log.info("跳过本地 TTS 服务（TTS_MODE 不是 local）")
                continue
            if service == "asr" and not speech_runtime.should_start_local_asr():
                log.info("跳过本地 ASR 服务（ASR_MODE 不是 local）")
                continue
            try:
                service_manager.start_service(service, wait=False)
            except Exception as e:
                log.error("启动服务 %s 失败: %s", service, e)
            time.sleep(0.5)
        log.info("其他后端服务已启动")


def schedule_kb_record_sync(user_text: str, assistant_text: str) -> None:
    if not conf.KB_ENABLED:
        return
    if not str(user_text).strip() or not str(assistant_text).strip():
        return
    log.debug("调度 KB 记录同步")
    async def _job():
        try:
            llm_tools.refresh_runtime_paths()
            manager = kb_manager.get_kb_manager(refresh=True)
            result = await manager.add_chat_record(user_text, assistant_text, {"agent": conf.AGENT_NAME})
            log.info("聊天记录已同步到知识库[异步索引模式]: %s", result.get("path"))
        except Exception as exc:
            log.error("聊天记录同步到知识库失败: %s", exc)

    asyncio.create_task(_job())


def _is_rate_limit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return ("429" in text) or ("rate" in text and "limit" in text) or ("bad_response_status_code" in text)


def _format_chat_error(exc: Exception) -> str:
    if _is_rate_limit_error(exc):
        return (
            "上游模型网关触发限流(429)，请稍后重试。"
            "若正在发送图片，请降低 MAX_PIXELS 或减少并发请求。"
        )
    return str(exc)


async def _sleep_backoff(attempt: int) -> None:
    # 轻量退避，避免在网关限流窗口内连续重试。
    base = 0.8 * (2 ** max(0, attempt - 1))
    jitter = random.uniform(0.0, 0.35)
    await asyncio.sleep(min(3.0, base + jitter))


async def invoke_agent_locked(target_agent, payload, config=None):
    if config is None:
        config = {"configurable": {"thread_id": THREAD_ID}}
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        log.debug("等待 Agent 锁")
        async with agent_lock:
            log.debug("开始调用 LLM")
            try:
                res = await target_agent.ainvoke(payload, config)
                log.debug("LLM 调用结束")
                return res
            except Exception as e:
                if _is_rate_limit_error(e) and attempt < max_attempts:
                    log.warning("429 限流，重试 attempt=%d/%d", attempt, max_attempts)
                else:
                    raise
        await _sleep_backoff(attempt)


async def stream_agent_locked(target_agent, payload, config=None):
    if config is None:
        config = {"configurable": {"thread_id": THREAD_ID}}
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        log.debug("等待 Agent 锁")
        async with agent_lock:
            log.debug("开始流式调用 LLM")
            try:
                async for message_chunk, metadata in target_agent.astream(payload, config, stream_mode="messages"):
                    if message_chunk.content and metadata.get("langgraph_node")!="tools" and _is_ai_message_chunk(message_chunk):
                        yield message_chunk, metadata
                log.debug("流式 LLM 调用结束")
                return
            except Exception as e:
                if _is_rate_limit_error(e) and attempt < max_attempts:
                    log.warning("429 限流，重试 attempt=%d/%d", attempt, max_attempts)
                else:
                    raise
        await _sleep_backoff(attempt)
startServices()


def _compose_runtime_extensions():
    base_tools = list(llm_tools.get_tools_for_agent(AGENT_NAME))
    tools = plugin_manager.compose_tools(base_tools=base_tools, agent_name=AGENT_NAME)
    middlewares = plugin_manager.compose_middlewares(agent_name=AGENT_NAME)
    return tools, middlewares


def _sync_plugin_trigger_filters():
    trigger_manager.set_append_filters([plugin_manager.filter_trigger_on_append])
    trigger_manager.set_fire_filters([plugin_manager.filter_trigger_on_fire])


async def _plugin_heartbeat_loop():
    while True:
        try:
            await asyncio.sleep(10.0)
            summary = plugin_manager.heartbeat_tick()
            if summary.get("errors"):
                log.error("插件心跳错误: %s", summary.get("errors"))
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error("插件心跳循环错误: %s", e)
            await asyncio.sleep(1.0)


def _build_chat_model(*, model_name: str):
    # 统一走 OpenAI 兼容接口，模型名与base_url由配置控制。
    return ChatOpenAI(
        model=model_name,
        api_key=conf.CHAT_API_KEY,
        base_url=conf.CHAT_API_BASE,
        request_timeout=60,
        max_retries=1,
    )


def _create_agent_with_extensions(*, model_name: str, checkpointer, store):
    tools, middlewares = _compose_runtime_extensions()
    chat_model = _build_chat_model(model_name=model_name)
    kwargs = {
        "model": chat_model,
        "checkpointer": checkpointer,
        "tools": tools,
        "store": store,
    }

    if middlewares:
        try:
            kwargs["middlewares"] = middlewares
            create_agent(**kwargs)
        except TypeError:
            pass
        try:
            kwargs.pop("middlewares", None)
            kwargs["middleware"] = middlewares
            create_agent(**kwargs)
        except TypeError:
            log.warning("create_agent 不支持 middleware 参数，已跳过插件 middlewares 注入")
            kwargs.pop("middleware", None)

    return create_agent(**kwargs)


def _has_checkpoint_db(agent_root: str) -> bool:
    return os.path.exists(pjoin(agent_root, "faust_checkpoint.db"))


def _message_content_to_text(content) -> str:
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
            btype = str(block.get("type") or "").strip().lower()
            if btype == "text":
                text_val = block.get("text")
                if text_val is not None:
                    parts.append(str(text_val))
        return "".join(parts)
    return str(content)


def _is_ai_message_chunk(message_chunk) -> bool:
    msg_type = str(message_chunk.type).strip().lower()
    if msg_type == "ai":
        return True
    cls_name = message_chunk.__class__.__name__.lower()
    return "aimessage" in cls_name


def _tool_value_to_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except Exception:
        return str(value)


def _normalize_tool_args(payload) -> dict:
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


async def stream_chat_agent_events(target_agent, payload, config=None):
    if config is None:
        config = {"configurable": {"thread_id": THREAD_ID}}
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        log.debug("等待 Agent 锁")
        async with agent_lock:
            log.debug("开始调用 LLM")
            try:
                async for event in target_agent.astream_events(payload, config=config, version="v2"):
                    if not isinstance(event, dict):
                        continue
                    event_name = str(event.get("event") or "").strip().lower()
                    data = event.get("data") or {}
                    if event_name == "on_chat_model_stream":
                        chunk = data.get("chunk")
                        if not chunk or not _is_ai_message_chunk(chunk):
                            continue
                        delta_text = _message_content_to_text(chunk.content)
                        if delta_text:
                            yield {"type": "delta", "content": delta_text}
                        continue
                    if event_name == "on_tool_start":
                        yield {
                            "type": "tool_start",
                            "tool_name": str(event.get("name") or data.get("name") or "tool").strip(),
                            "args": _normalize_tool_args(data.get("input")),
                            "call_id": str(event.get("run_id") or ""),
                        }
                        continue
                    if event_name == "on_tool_end":
                        yield {
                            "type": "tool_result",
                            "tool_name": str(event.get("name") or data.get("name") or "tool").strip(),
                            "output": _tool_value_to_text(data.get("output")),
                            "call_id": str(event.get("run_id") or ""),
                        }
                        continue
                log.debug("LLM 调用结束")
                return
            except Exception as e:
                if _is_rate_limit_error(e) and attempt < max_attempts:
                    log.warning("429 限流，重试 attempt=%d/%d", attempt, max_attempts)
                else:
                    raise
        await _sleep_backoff(attempt)


async def rebuild_runtime(*, reset_dialog: bool = False, no_initial_chat: bool = False):
    log.info("正在重建运行时，reset_dialog=%s, no_initial_chat=%s", reset_dialog, no_initial_chat)
    global agent, checkpointer, conn, storer, conn_for_store, AGENT_NAME, AGENT_ROOT
    # 在修改全局 Agent 状态前获取锁，防止并发调用破坏状态一致性
    _needs_initial_chat = False
    _reset_dialog = reset_dialog
    async with agent_lock:
        try:
            conf.reload_configs()
            os.environ["DEEPSEEK_API_KEY"] = conf.CHAT_API_KEY
            os.environ["SEARCHAPI_API_KEY"] = conf.SEARCH_API_KEY
            os.environ["OPENAI_API_KEY"] = conf.CHAT_API_KEY
            os.environ["OPENAI_BASE_URL"] = conf.CHAT_API_BASE
            AGENT_NAME = conf.AGENT_NAME
            AGENT_ROOT = os.path.join("agents", f"{AGENT_NAME}")
            log.info("重建目标 Agent: %s", AGENT_NAME)

            makeup_init_prompt()
            llm_tools.refresh_runtime_paths()
            araya_runtime.get_araya_runtime(refresh=True).refresh_target_agent()
            plugin_reload = plugin_manager.reload()
            log.info("插件重载摘要: %s", plugin_reload)
            _sync_plugin_trigger_filters()
            if not args.save_in_memory:
                try:
                    if conn is not None:
                        await conn.commit()
                        await conn.close()
                except Exception:
                    pass
                try:
                    if conn_for_store is not None:
                        await conn_for_store.commit()
                        await conn_for_store.close()
                except Exception:
                    pass
                os.makedirs(AGENT_ROOT, exist_ok=True)
                conn = await aiosqlite.connect(pjoin(AGENT_ROOT,'faust_checkpoint.db'))
                checkpointer=AsyncSqliteSaver(conn=conn)
                conn_for_store = await aiosqlite.connect(pjoin(AGENT_ROOT,'faust_store.db'))
                storer=AsyncSqliteStore(conn=conn_for_store)
                log.info("已初始化 SQLite Checkpoint + Store，checkpoint=%s , store=%s",
                         pjoin(AGENT_ROOT, 'faust_checkpoint.db'),
                         pjoin(AGENT_ROOT, 'faust_store.db'))
            else:
                checkpointer=InMemorySaver()
                storer=InMemoryStore()
            log.info("Checkpoint 和 Store 已为重建就绪")
            agent = _create_agent_with_extensions(model_name=conf.CHAT_MODEL, checkpointer=checkpointer, store=storer)
            log.debug("Agent 已为重建重新创建")
            checkpoint_exists = (not args.save_in_memory) and _has_checkpoint_db(AGENT_ROOT)
            if no_initial_chat and checkpoint_exists:
                log.info("运行时重建跳过初始对话（checkpoint 存在且 no_initial_chat=True）")
                _set_runtime_state(ready=True, status="ready")
                return {
                    "agent_name": AGENT_NAME,
                    "agent_root": AGENT_ROOT,
                    "initial_chat_skipped": True,
                    "ready": True,
                    "status": RUNTIME_STATUS,
                    "error": "",
                }
            _needs_initial_chat = True
        except Exception as e:
            agent = None
            _set_runtime_state(ready=False, status="waiting_for_config", error=str(e))
            log.warning("运行时重建降级: %s", e)
            return {
                "agent_name": AGENT_NAME,
                "agent_root": AGENT_ROOT,
                "initial_chat_skipped": True,
                "ready": False,
                "status": RUNTIME_STATUS,
                "error": str(e),
            }

    if _needs_initial_chat:
        try:
            if _reset_dialog:
                await invoke_agent_locked(agent, {"messages": [{"role": "system", "content": PROMPT}]})
            else:
                await invoke_agent_locked(agent, {"messages": [{"role": "user", "content": f"请继续按当前角色设定工作。\n 如果你需要重新了解你的角色设定，请读取agents/{AGENT_NAME}/AGENT.md、ROLE.md、COREMEMORY.md、TASK.md等文件来获取最新的设定内容。\n 这一条对话无需写入日记"}]})
            log.info("运行时重建完成")
            _set_runtime_state(ready=True, status="ready")
            return {
                "agent_name": AGENT_NAME,
                "agent_root": AGENT_ROOT,
                "initial_chat_skipped": False,
                "ready": True,
                "status": RUNTIME_STATUS,
                "error": "",
            }
        except Exception as e:
            agent = None
            _set_runtime_state(ready=False, status="waiting_for_config", error=str(e))
            log.warning("运行时重建降级: %s", e)
            return {
                "agent_name": AGENT_NAME,
                "agent_root": AGENT_ROOT,
                "initial_chat_skipped": True,
                "ready": False,
                "status": RUNTIME_STATUS,
                "error": str(e),
            }

    return {
        "agent_name": AGENT_NAME,
        "agent_root": AGENT_ROOT,
        "initial_chat_skipped": True,
        "ready": True,
        "status": RUNTIME_STATUS,
        "error": "",
    }

@app.on_event("startup")
async def startup_event():
    global agent,checkpointer,conn,storer,conn_for_store,plugin_heartbeat_task
    backend2frontend.set_main_loop(asyncio.get_running_loop())  # 注册主事件循环，供同步线程推送命令
    araya_runtime.get_araya_runtime(refresh=True)  # 提前加载 ArayaRuntime，确保其事件循环在主线程中
    try:
        kb_runtime = kb_manager.get_kb_manager(refresh=True)
        kb_runtime.ensure_vdb_initialized()
        await kb_runtime.ensure_worker_started()
        await araya_runtime.get_araya_runtime(refresh=True).startup()
        startup_info = await rebuild_runtime(reset_dialog=False, no_initial_chat=bool(conf.args.no_startup_chat))
        log.info("启动运行时摘要: %s", startup_info)
    except Exception as e:
        agent = None
        _set_runtime_state(ready=False, status="waiting_for_config", error=str(e))
        log.warning("启动运行时降级: %s", e)
    try:
        await vad_runtime.vad_runtime.startup()
        log.info("VAD 运行时已加载到 CPU")
    except Exception as e:
        log.warning("启动 VAD 初始化失败: %s", e)
    #--- Start the trigger watchdog thread to monitor and activate triggers.
    log.info("触发器看门狗线程正在启动...")
    trigger_manager.start_trigger_watchdog_thread()
    try:
        await minecraft_client.ensure_started()
    except Exception as e:
        log.warning("Minecraft 桥启动时未连接: %s", e)
    if plugin_heartbeat_task is None:
        plugin_heartbeat_task = asyncio.create_task(_plugin_heartbeat_loop())
    # try:
    #     log.info("正在启动 ArayaRuntime...")
    #     async for event in araya_runtime.get_araya_runtime().stream_once_async(reason="startup_events"):
    #          event_name = str(event.get("event") or "").strip().lower()
    #          event_payload = event.get("data") or {}
    #          log.debug("ArayaRuntime 事件: %s, 数据: %s", event_name, event_payload)
    # except Exception as e:
    #     log.warning("启动 ArayaRuntime 事件流失败: %s", e)
    log.info("FAUST 后端主服务已启动")


@app.get("/faust/admin/config")
async def admin_get_config():
    return admin_runtime.get_config_view()


@app.post("/faust/admin/config")
async def admin_save_config(payload: dict):
    return admin_runtime.save_config(payload or {})


@app.post("/faust/admin/config/reload")
async def admin_reload_config(payload: dict | None = None):
    info = await rebuild_runtime(
        reset_dialog=bool((payload or {}).get("reset_dialog", False)),
        no_initial_chat=bool((payload or {}).get("no_initial_chat", True)),
    )
    return {
        "status": "ok",
        "runtime": info,
        "summary": admin_runtime.runtime_summary(),
        "callback": {
            "type": "runtime_reloaded",
            "scope": "config",
            "agent_name": info.get("agent_name"),
            "reset_dialog": bool((payload or {}).get("reset_dialog", False)),
            "no_initial_chat": bool((payload or {}).get("no_initial_chat", True)),
        }
    }


@app.get("/faust/admin/runtime")
async def admin_runtime_summary_api():
    return {"status": "ok", "runtime": {**admin_runtime.runtime_summary(), **_runtime_status_payload()}}


@app.post("/faust/admin/live2d/apply")
async def admin_apply_live2d(payload: dict | None = None):
    return admin_runtime.apply_live2d_to_frontend(payload or {})


@app.get("/faust/admin/services")
async def admin_list_services(include_log: bool = False):
    return {"status": "ok", "items": service_manager.list_services(include_log=include_log)}


@app.get("/faust/admin/services/{service_key}")
async def admin_get_service(service_key: str, include_log: bool = True):
    return {"status": "ok", "item": service_manager.service_status(service_key, include_log=include_log)}


@app.post("/faust/admin/services/{service_key}/start")
async def admin_start_service(service_key: str):
    item = service_manager.start_service(service_key)
    return {"status": "ok", "item": item, "callback": {"type": "service_action", "action": "start", "service_key": service_key}}


@app.post("/faust/admin/services/{service_key}/stop")
async def admin_stop_service(service_key: str):
    item = service_manager.stop_service(service_key)
    return {"status": "ok", "item": item, "callback": {"type": "service_action", "action": "stop", "service_key": service_key}}


@app.post("/faust/admin/services/{service_key}/restart")
async def admin_restart_service(service_key: str):
    item = service_manager.restart_service(service_key)
    return {"status": "ok", "item": item, "callback": {"type": "service_action", "action": "restart", "service_key": service_key}}


@app.get("/faust/admin/log/recent-errors")
async def admin_log_recent_errors():
    """从日志环状缓冲区返回最近 N 条 ERROR 级别日志。"""
    from faust_backend.logger import get_recent_errors

    errors = get_recent_errors(count=5)
    return {"status": "ok", "errors": errors}


@app.post("/faust/admin/runtime/reload-agent")
async def admin_reload_agent():
    info = await rebuild_runtime(reset_dialog=False, no_initial_chat=True)
    return {
        "status": "ok",
        "runtime": info,
        "callback": {
            "type": "runtime_reloaded",
            "scope": "agent",
            "agent_name": info.get("agent_name"),
            "reset_dialog": False,
            "no_initial_chat": True,
        }
    }


@app.post("/faust/admin/runtime/reload-all")
async def admin_reload_all():
    info = await rebuild_runtime(reset_dialog=True, no_initial_chat=False)
    return {
        "status": "ok",
        "runtime": info,
        "callback": {
            "type": "runtime_reloaded",
            "scope": "all",
            "agent_name": info.get("agent_name"),
            "reset_dialog": True,
            "no_initial_chat": False,
        }
    }


@app.get("/faust/admin/agents")
async def admin_list_agents():
    return {"items": admin_runtime.list_agents()}


@app.post("/faust/admin/agents")
async def admin_create_agent(payload: dict):
    agent_name = (payload or {}).get("agent_name")
    template_agent = (payload or {}).get("template_agent")
    detail = admin_runtime.create_agent(agent_name, template_agent=template_agent)
    return {"status": "ok", "detail": detail}


@app.get("/faust/admin/agents/{agent_name}")
async def admin_get_agent(agent_name: str):
    return {"status": "ok", "detail": admin_runtime.get_agent_detail(agent_name)}


@app.put("/faust/admin/agents/{agent_name}/files")
async def admin_save_agent_files(agent_name: str, payload: dict):
    files = (payload or {}).get("files") or {}
    updated = admin_runtime.save_agent_files(agent_name, files)
    return {"status": "ok", "files": updated}


@app.delete("/faust/admin/agents/{agent_name}")
async def admin_delete_agent(agent_name: str):
    admin_runtime.delete_agent(agent_name)
    return {"status": "ok", "deleted": agent_name}


@app.post("/faust/admin/agents/switch")
async def admin_switch_agent(payload: dict):
    agent_name = (payload or {}).get("agent_name")
    result = await admin_runtime.switch_agent(agent_name)
    info = await rebuild_runtime(reset_dialog=True, no_initial_chat=False)
    return {
        "status": "ok",
        "switch": result,
        "runtime": info,
        "callback": {
            "type": "runtime_reloaded",
            "scope": "agent_switch",
            "agent_name": info.get("agent_name"),
            "reset_dialog": True,
            "no_initial_chat": False,
        }
    }


@app.get("/faust/admin/live2d/models")
async def admin_list_live2d_models():
    return {"items": admin_runtime.list_available_models()}


@app.get("/faust/admin/skills")
async def admin_list_skills(agent_name: str | None = None):
    try:
        items = skill_manager.list_skills(agent_name=agent_name)
        return {"status": "ok", "agent": agent_name or AGENT_NAME, "items": items}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Skill 列表读取失败: {e}")


@app.get("/faust/admin/skills/{slug}")
async def admin_get_skill_detail(slug: str, agent_name: str | None = None):
    try:
        detail = skill_manager.get_skill_detail(slug, agent_name=agent_name)
        return {"status": "ok", "agent": agent_name or AGENT_NAME, "detail": detail}
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Skill 详情读取失败: {e}")


@app.put("/faust/admin/skills/{slug}/skill-md")
async def admin_update_skill_md(slug: str, payload: dict | None = None):
    body = payload or {}
    agent_name = body.get("agent_name")
    content = str(body.get("content") or "")
    try:
        detail = skill_manager.get_skill_detail(slug, agent_name=agent_name)
        skill_path = str(detail.get("path") or "").strip()
        if not skill_path:
            raise RuntimeError("Skill 路径为空")
        md_path = os.path.join(skill_path, "SKILL.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(content)
        refreshed = skill_manager.get_skill_detail(slug, agent_name=agent_name)
        return {"status": "ok", "detail": refreshed}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"SKILL.md 保存失败: {e}")


@app.post("/faust/admin/skills/install")
async def admin_install_skill(payload: dict | None = None):
    body = payload or {}
    slug = str(body.get("slug") or "").strip()
    agent_name = body.get("agent_name")
    overwrite = bool(body.get("overwrite", False))
    if not slug:
        raise HTTPException(status_code=400, detail="缺少 slug")
    try:
        item = skill_manager.install_skill(slug, agent_name=agent_name, overwrite=overwrite)
        return {"status": "ok", "item": item}
    except skill_manager.SkillAlreadyInstalledError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Skill 安装失败: {e}")


@app.post("/faust/admin/skills/install-zip")
async def admin_install_skill_from_zip(payload: dict | None = None):
    body = payload or {}
    zip_path = str(body.get("zip_path") or "").strip()
    agent_name = body.get("agent_name")
    overwrite = bool(body.get("overwrite", False))
    if not zip_path:
        raise HTTPException(status_code=400, detail="缺少 zip_path")
    try:
        item = skill_manager.install_skill_from_zip(zip_path, agent_name=agent_name, overwrite=overwrite)
        return {"status": "ok", "item": item}
    except skill_manager.SkillAlreadyInstalledError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Skill ZIP 安装失败: {e}")


@app.delete("/faust/admin/skills/{slug}")
async def admin_delete_skill(slug: str, agent_name: str | None = None):
    try:
        result = skill_manager.remove_skill(slug, agent_name=agent_name)
        return {"status": "ok", "deleted": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Skill 删除失败: {e}")


@app.post("/faust/admin/skills/{slug}/enable")
async def admin_enable_skill(slug: str, payload: dict | None = None):
    agent_name = (payload or {}).get("agent_name")
    try:
        result = skill_manager.set_skill_enabled(slug, True, agent_name=agent_name)
        return {"status": "ok", "item": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Skill 启用失败: {e}")


@app.post("/faust/admin/skills/{slug}/disable")
async def admin_disable_skill(slug: str, payload: dict | None = None):
    agent_name = (payload or {}).get("agent_name")
    try:
        result = skill_manager.set_skill_enabled(slug, False, agent_name=agent_name)
        return {"status": "ok", "item": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Skill 禁用失败: {e}")


@app.get("/faust/admin/triggers")
async def admin_list_triggers():
    items = await asyncio.to_thread(trigger_manager.list_triggers)
    return {"status": "ok", "items": items}


@app.get("/faust/admin/triggers/{trigger_id}")
async def admin_get_trigger(trigger_id: str):
    item = await asyncio.to_thread(trigger_manager.get_trigger, trigger_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Trigger not found: {trigger_id}")
    return {"status": "ok", "item": item}


@app.post("/faust/admin/triggers")
async def admin_create_or_upsert_trigger(payload: dict | None = None):
    body = payload or {}
    try:
        await asyncio.to_thread(trigger_manager.append_trigger, body)
        tid = str(body.get("id") or "")
        item = await asyncio.to_thread(trigger_manager.get_trigger, tid) if tid else body
        return {"status": "ok", "item": item}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Trigger 保存失败: {e}")


@app.put("/faust/admin/triggers/{trigger_id}")
async def admin_update_trigger(trigger_id: str, payload: dict | None = None):
    body = payload or {}
    try:
        await asyncio.to_thread(trigger_manager.update_trigger, trigger_id, body)
        item = await asyncio.to_thread(trigger_manager.get_trigger, trigger_id)
        return {"status": "ok", "item": item}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Trigger 更新失败: {e}")


@app.delete("/faust/admin/triggers/{trigger_id}")
async def admin_delete_trigger(trigger_id: str):
    existed = await asyncio.to_thread(trigger_manager.get_trigger, trigger_id)
    existed = existed is not None
    await asyncio.to_thread(trigger_manager.delete_trigger, trigger_id)
    if not existed:
        raise HTTPException(status_code=404, detail=f"Trigger not found: {trigger_id}")
    return {"status": "ok", "deleted": trigger_id}


@app.get("/faust/admin/plugins")
async def admin_list_plugins():
    return {
        "status": "ok",
        "items": plugin_manager.list_plugins(),
        "manual_reload_only": True,
    }


@app.post("/faust/admin/plugins/reload")
async def admin_reload_plugins(payload: dict | None = None):
    summary = plugin_manager.reload()
    _sync_plugin_trigger_filters()
    apply_runtime = bool((payload or {}).get("apply_runtime", True))
    reset_dialog = bool((payload or {}).get("reset_dialog", True))
    no_initial_chat = bool((payload or {}).get("no_initial_chat", True))
    runtime_info = None
    if apply_runtime:
        runtime_info = await rebuild_runtime(reset_dialog=reset_dialog, no_initial_chat=no_initial_chat)
    return {
        "status": "ok",
        "reload": summary,
        "runtime": runtime_info,
        "items": plugin_manager.list_plugins(),
        "manual_reload_only": True,
    }


@app.get("/faust/admin/plugins/hot-reload")
async def admin_plugins_hot_reload_status():
    return {"status": "ok", "manual_reload_only": True, "enabled": False}


@app.post("/faust/admin/plugins/heartbeat")
async def admin_plugins_heartbeat_once():
    return {"status": "ok", "result": plugin_manager.heartbeat_tick()}


@app.post("/faust/admin/plugins/hot-reload/start")
async def admin_plugins_hot_reload_start(payload: dict | None = None):
    return {
        "status": "ok",
        "manual_reload_only": True,
        "detail": "已禁用自动轮询热重载，请使用手动重载接口 /faust/admin/plugins/reload",
    }


@app.post("/faust/admin/plugins/hot-reload/stop")
async def admin_plugins_hot_reload_stop():
    return {
        "status": "ok",
        "manual_reload_only": True,
        "detail": "当前仅支持手动重载",
    }


@app.post("/faust/admin/plugins/{plugin_id}/enable")
async def admin_enable_plugin(plugin_id: str, payload: dict | None = None):
    plugin_manager.set_plugin_enabled(plugin_id, True)
    apply_runtime = bool((payload or {}).get("apply_runtime", True))
    reset_dialog = bool((payload or {}).get("reset_dialog", True))
    no_initial_chat = bool((payload or {}).get("no_initial_chat", True))
    runtime_info = None
    if apply_runtime:
        runtime_info = await rebuild_runtime(reset_dialog=reset_dialog, no_initial_chat=no_initial_chat)
    return {"status": "ok", "plugin_id": plugin_id, "enabled": True, "runtime": runtime_info}


@app.post("/faust/admin/plugins/{plugin_id}/disable")
async def admin_disable_plugin(plugin_id: str, payload: dict | None = None):
    plugin_manager.set_plugin_enabled(plugin_id, False)
    apply_runtime = bool((payload or {}).get("apply_runtime", True))
    reset_dialog = bool((payload or {}).get("reset_dialog", True))
    no_initial_chat = bool((payload or {}).get("no_initial_chat", True))
    runtime_info = None
    if apply_runtime:
        runtime_info = await rebuild_runtime(reset_dialog=reset_dialog, no_initial_chat=no_initial_chat)
    return {"status": "ok", "plugin_id": plugin_id, "enabled": False, "runtime": runtime_info}


@app.get("/faust/admin/plugins/{plugin_id}/config")
async def admin_get_plugin_config(plugin_id: str):
    return {
        "status": "ok",
        "plugin_id": plugin_id,
        "config": plugin_manager.get_plugin_config_snapshot(plugin_id),
    }


@app.post("/faust/admin/plugins/{plugin_id}/config")
async def admin_set_plugin_config(plugin_id: str, payload: dict | None = None):
    body = payload or {}
    values = body.get("values") or {}
    apply_runtime = bool(body.get("apply_runtime", True))
    reset_dialog = bool(body.get("reset_dialog", False))
    no_initial_chat = bool(body.get("no_initial_chat", True))
    config_snapshot = plugin_manager.set_plugin_config_values(plugin_id, values)
    reload_summary = plugin_manager.reload()
    _sync_plugin_trigger_filters()
    runtime_info = None
    if apply_runtime:
        runtime_info = await rebuild_runtime(reset_dialog=reset_dialog, no_initial_chat=no_initial_chat)
    return {
        "status": "ok",
        "plugin_id": plugin_id,
        "config": config_snapshot,
        "reload": reload_summary,
        "runtime": runtime_info,
    }


@app.get("/faust/admin/plugin-market/catalog")
async def admin_plugin_market_catalog(index_url: str | None = Query(default=None)):
    try:
        data = plugin_market.fetch_catalog(index_url=index_url)
        return {"status": "ok", **data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"插件市场读取失败: {e}")


@app.post("/faust/admin/plugin-market/install")
async def admin_plugin_market_install(payload: dict | None = None):
    body = payload or {}
    plugin_id = str(body.get("plugin_id") or body.get("id") or "").strip()
    index_url = body.get("index_url") or body.get("market_url")
    overwrite = bool(body.get("overwrite", False))
    apply_runtime = bool(body.get("apply_runtime", True))
    reset_dialog = bool(body.get("reset_dialog", False))
    no_initial_chat = bool(body.get("no_initial_chat", True))
    if not plugin_id:
        raise HTTPException(status_code=400, detail="缺少 plugin_id")

    try:
        install_info = plugin_market.install_plugin_from_catalog(
            plugin_id=plugin_id,
            plugins_dir=plugin_manager.plugins_dir,
            index_url=index_url,
            overwrite=overwrite,
        )
        reload_summary = plugin_manager.reload()
        _sync_plugin_trigger_filters()
        runtime_info = None
        if apply_runtime:
            runtime_info = await rebuild_runtime(reset_dialog=reset_dialog, no_initial_chat=no_initial_chat)
        return {
            "status": "ok",
            "install": install_info,
            "reload": reload_summary,
            "runtime": runtime_info,
            "items": plugin_manager.list_plugins(),
        }
    except plugin_market.PluginAlreadyInstalledError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except plugin_market.PluginMarketError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"插件安装失败: {e}")


@app.post("/faust/admin/plugins/install-zip")
async def admin_plugins_install_zip(payload: dict | None = None):
    body = payload or {}
    zip_path = str(body.get("zip_path") or "").strip()
    expected_plugin_id = str(body.get("plugin_id") or "").strip() or None
    overwrite = bool(body.get("overwrite", False))
    apply_runtime = bool(body.get("apply_runtime", True))
    reset_dialog = bool(body.get("reset_dialog", False))
    no_initial_chat = bool(body.get("no_initial_chat", True))
    if not zip_path:
        raise HTTPException(status_code=400, detail="缺少 zip_path")

    try:
        install_info = plugin_market.install_plugin_from_zip(
            zip_path=zip_path,
            plugins_dir=plugin_manager.plugins_dir,
            overwrite=overwrite,
            expected_plugin_id=expected_plugin_id,
        )
        reload_summary = plugin_manager.reload()
        _sync_plugin_trigger_filters()
        runtime_info = None
        if apply_runtime:
            runtime_info = await rebuild_runtime(reset_dialog=reset_dialog, no_initial_chat=no_initial_chat)
        return {
            "status": "ok",
            "install": install_info,
            "reload": reload_summary,
            "runtime": runtime_info,
            "items": plugin_manager.list_plugins(),
        }
    except plugin_market.PluginAlreadyInstalledError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except plugin_market.PluginMarketError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ZIP 插件安装失败: {e}")


@app.post("/faust/admin/plugins/package-zip")
async def admin_plugins_package_zip(payload: dict | None = None):
    body = payload or {}
    plugin_id = str(body.get("plugin_id") or body.get("id") or "").strip()
    output_dir = body.get("output_dir")
    zip_name = body.get("zip_name")
    if not plugin_id:
        raise HTTPException(status_code=400, detail="缺少 plugin_id")

    try:
        package_info = plugin_market.package_plugin_to_zip(
            plugin_id=plugin_id,
            plugins_dir=plugin_manager.plugins_dir,
            output_dir=output_dir,
            zip_name=zip_name,
        )
        return {
            "status": "ok",
            "package": package_info,
        }
    except plugin_market.PluginMarketError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"插件打包失败: {e}")


@app.delete("/faust/admin/plugins/{plugin_id}")
async def admin_delete_plugin(plugin_id: str, apply_runtime: bool = True, reset_dialog: bool = False, no_initial_chat: bool = True):
    try:
        delete_info = plugin_market.delete_installed_plugin(
            plugin_id=plugin_id,
            plugins_dir=plugin_manager.plugins_dir,
            state_file=plugin_manager.state_file,
        )
        reload_summary = plugin_manager.reload()
        _sync_plugin_trigger_filters()
        runtime_info = None
        if apply_runtime:
            runtime_info = await rebuild_runtime(reset_dialog=reset_dialog, no_initial_chat=no_initial_chat)
        return {
            "status": "ok",
            "deleted": delete_info,
            "reload": reload_summary,
            "runtime": runtime_info,
            "items": plugin_manager.list_plugins(),
        }
    except plugin_market.PluginMarketError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"插件删除失败: {e}")

@app.delete("/faust/admin/agents/{agent_name}/checkpoint")
async def admin_delete_agent_checkpoint(agent_name: str):
    if agent_name == AGENT_NAME:
        raise HTTPException(status_code=400, detail=f"不能删除当前正在使用的 Agent '{AGENT_NAME}' 的 checkpoint")
    os.remove(pjoin("agents", agent_name, "faust_checkpoint.db"))
    if os.path.exists(pjoin("agents", agent_name, "faust_store.db")):
        os.remove(pjoin("agents", agent_name, "faust_store.db"))
    if os.path.exists(pjoin("agents", agent_name, "faust_checkpoint.db-shm")):
        os.remove(pjoin("agents", agent_name, "faust_checkpoint.db-shm"))
    if os.path.exists(pjoin("agents", agent_name, "faust_checkpoint.db-wal")):
        os.remove(pjoin("agents", agent_name, "faust_checkpoint.db-wal"))
    return {
        "status": "ok",
        "detail": f"Agent '{agent_name}' 的 checkpoint 已删除，下一次重启或切换 Agent 将会重新创建一个新的 checkpoint 文件。",
    }

@app.post("/faust/chat")
#@deprecated(reason="This endpoint is kept for compatibility and development but the primary chat interface is now the websocket /faust/chat for frontend streaming.")
async def chat_post(payload: dict):
    """
     Post方式的聊天接口
        兼容性HTTP端点。内部仍然返回完整回复。
        已经弃用
        请使用websocket /faust/chat接口以获得更好的前端流式体验和更低的延迟。
        保留原因：方便调试
    """
    text = None
    if isinstance(payload, dict):
        text = payload.get('text') or payload.get('message')
    if not text:
        return {"error": "no text provided"}
    if not RUNTIME_READY or agent is None:
        return {"error": _runtime_not_ready_message(), "runtime": _runtime_status_payload()}
    try:
        await asyncio.to_thread(araya_runtime.get_araya_runtime(refresh=True).mark_main_agent_activity)
        events.ignore_trigger_event.set()
        resp = await invoke_agent_locked(agent,{"messages":[{"role":"user","content":text}]})
        reply = _message_content_to_text(resp["messages"][-1].content)
        schedule_kb_record_sync(text, reply)
        log.info('Chat POST 回复完成')
        events.ignore_trigger_event.clear()
        return {"reply": reply,"warning": "使用websocket /faust/chat接口以获得更好的前端流式体验和更低的延迟。"}
    except Exception as e:
        log.error("Chat POST 错误: %s", e)
        return {"error": _format_chat_error(e), "warning": "使用websocket /faust/chat接口以获得更好的前端流式体验和更低的延迟。"}

@app.websocket("/faust/chat")
    
async def chat_websocket(websocket: WebSocket):
    """
    主要的聊天接口，使用WebSocket
    """
    await websocket.accept()
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {"text": raw}
            text = None
            if isinstance(payload, dict):
                text = payload.get("text") or payload.get("message")
            if not text:
                await websocket.send_text(json.dumps({"type": "error", "error": "no text provided"}, ensure_ascii=False))
                continue
            if not RUNTIME_READY or agent is None:
                await websocket.send_text(json.dumps({"type": "error", "error": _runtime_not_ready_message(), "runtime": _runtime_status_payload()}, ensure_ascii=False))
                continue

            try:
                araya_runtime.get_araya_runtime(refresh=True).mark_main_agent_activity()
                events.ignore_trigger_event.set()
                await websocket.send_text(json.dumps({"type": "start"}, ensure_ascii=False))
                reply = ""
                log.info("收到聊天消息: %s", text[:100])
                async for event in stream_chat_agent_events(agent, {"messages":[{"role":"user","content":text}]}) :
                    if not isinstance(event, dict):
                        continue
                    if event.get("type") == "delta":
                        delta_text = _message_content_to_text(event.get("content"))
                        if not delta_text:
                            continue
                        reply += delta_text
                        log.debug("聊天增量: %s", delta_text[:80])
                        await websocket.send_text(json.dumps({"type": "delta", "content": delta_text}, ensure_ascii=False))
                        continue
                    if event.get("type") in {"tool_start", "tool_result"}:
                        await websocket.send_text(json.dumps(event, ensure_ascii=False))
                schedule_kb_record_sync(text, reply)
                await websocket.send_text(json.dumps({"type": "done", "reply": reply}, ensure_ascii=False))
                log.debug("聊天流结束")
                events.ignore_trigger_event.clear()
            except Exception as e:
                events.ignore_trigger_event.clear()
                log.error("Chat WebSocket 错误: %s", e)
                await websocket.send_text(json.dumps({"type": "error", "error": _format_chat_error(e)}, ensure_ascii=False))
    except WebSocketDisconnect:
        log.info("Chat WebSocket 断开")

@app.websocket("/faust/command")
async def command_websocket(websocket: WebSocket):
    await websocket.accept()
    backend2frontend.FrontEndSay("Hello World! 你好,世界!")
    try:
        while True:
            if backend2frontend.hasFrontEndTask():
                task = await backend2frontend.popFrontEndTask()
                log.debug("从 backend2frontend 队列发送前端任务: %s", task[:80] if isinstance(task, str) else str(task)[:80])
                if task:
                    await websocket.send_text(task)
            if trigger_manager.has_queue_task() and not events.ignore_trigger_event.is_set():
                if not RUNTIME_READY or agent is None:
                    await asyncio.sleep(0.1)
                    continue
                # activate chat
                task=trigger_manager.get_next_trigger()
                trigger_text = f"<Trigger>触发器唤醒了你，请根据触发器内容执行相应操作。{str(task)}"
                if isinstance(task, dict):
                    ttype = task.get("type")
                    callback_id = task.get("callback_id")
                    if ttype == "event" and task.get("event_name") == "nimble_result" and callback_id:
                        result = nimble.get_nimble_result(callback_id, cleanup=False)
                        trigger_text = f"<Trigger>灵动交互窗口收到用户提交。callback_id={callback_id}，用户结果={result}。请继续处理。"
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
                resp = await invoke_agent_locked(agent,{"messages":[{"role":"user","content":trigger_text}]})
                reply = resp["messages"][-1].content
                log.debug('触发器激活回复: %s', str(reply)[:120])
                if("<NO_TTS_OUTPUT>" in reply):
                    continue
                await websocket.send_text(f"SAY {reply}")
            try:
                command = await asyncio.wait_for(forward_queue.get(), timeout=0.01)
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
@app.post("/faust/command/forward")
async def command_forward_post(payload: dict):
    """Forwards a command from frontend to the agent and returns the reply."""
    command = None
    if isinstance(payload, dict):
        command = payload.get('command')
    if not command:
        return {"error": "no command provided"}
    await forward_queue.put(command)
    events.backend2frontendQueue_event.set()
    return {"status": "command forwarded"}
@app.post("/faust/humanInLoop/feedback")
async def human_in_loop_feedback_post(payload: dict):
    """Handles feedback from the human-in-the-loop system."""
    feedback = None
    request_id = None
    reason = None
    log.debug("HIL feedback payload: %s", payload)
    if isinstance(payload, dict):
        feedback = payload.get('feedback')
        request_id = payload.get('request_id') or payload.get('id')
        reason = payload.get('reason')
    if feedback is None:
        return {"error": "no feedback provided"}
    approved = bool(feedback)
    resolved = False
    if request_id:
        resolved = events.resolve_hil_request(str(request_id), {
            "approved": approved,
            "reason": reason or ("approved" if approved else "rejected"),
            "request_id": str(request_id),
        })
        backend2frontend.FrontEndCloseNimbleWindow({"callback_id": str(request_id), "reason": "approved" if approved else "rejected"})
    else:
        if approved:
            events.HIL_feedback_event.set()
        else:
            events.HIL_feedback_fail_event.set()
        resolved = True
    return {"status": "feedback received", "request_id": request_id, "resolved": resolved}

@app.post("/faust/nimble/callback")
async def nimble_callback_post(payload: dict):
    """Receive a nimble window submit callback from the frontend.

    Body example:
    {
      "callback_id": "nimble_xxx",
      "data": {...},
      "close": true
    }
    """
    callback_id = None
    data = None
    should_close = False
    if isinstance(payload, dict):
        callback_id = payload.get("callback_id")
        data = payload.get("data")
        should_close = bool(payload.get("close"))
    if not callback_id:
        return {"error": "no callback_id provided"}

    session = nimble.set_nimble_result(callback_id, data, closed=should_close)
    if not session:
        return {"error": f"unknown callback_id: {callback_id}"}

    if should_close:
        trigger_manager.delete_trigger(session["reminder_trigger_id"])
        trigger_manager.delete_trigger(session["expire_trigger_id"])
        backend2frontend.FrontEndCloseNimbleWindow({"callback_id": callback_id, "reason": "submitted"})

    return {"status": "ok", "callback_id": callback_id}
@app.post("/faust/command/feedback")
async def command_feedback_post(payload: dict):
    """Handles feedback for commands from the frontend."""
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
@app.post("/faust/nimble/close")
async def nimble_close_post(payload: dict):
    """Close a nimble window from the frontend and clean up its bound triggers."""
    callback_id = None
    reason = "closed_by_user"
    if isinstance(payload, dict):
        callback_id = payload.get("callback_id")
        reason = payload.get("reason") or reason
    if not callback_id:
        return {"error": "no callback_id provided"}

    session = nimble.close_nimble_session(callback_id, reason=reason)
    if not session:
        return {"error": f"unknown callback_id: {callback_id}"}

    trigger_manager.delete_trigger(session["result_trigger_id"])
    trigger_manager.delete_trigger(session["reminder_trigger_id"])
    trigger_manager.delete_trigger(session["expire_trigger_id"])
    backend2frontend.FrontEndCloseNimbleWindow({"callback_id": callback_id, "reason": reason})
    nimble.cleanup_nimble_session(callback_id)
    return {"status": "closed", "callback_id": callback_id}


@app.get("/faust/audio/config")
async def speech_config_get():
    conf.reload_configs()
    return {"status": "ok", "config": speech_runtime.frontend_speech_config()}


@app.websocket("/faust/logger/ws")
async def logger_websocket(websocket: WebSocket):
    """WebSocket 端点：前端订阅日志流。"""
    from faust_backend.logger import subscribe_ws, unsubscribe_ws

    await websocket.accept()
    log.info("日志 WebSocket 客户端已连接")
    q = await subscribe_ws()
    try:
        while True:
            payload = await q.get()
            try:
                await websocket.send_text(json.dumps(payload, ensure_ascii=False))
            except WebSocketDisconnect:
                break
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    finally:
        await unsubscribe_ws(q)
        log.info("日志 WebSocket 客户端已断开")


@app.get("/faust/audio/vad/status")
async def speech_vad_status_get():
    return await vad_runtime.vad_runtime.status()


@app.websocket("/faust/audio/ws/vad")
async def speech_vad_ws(websocket: WebSocket):
    await websocket.accept()
    await vad_runtime.vad_runtime.connection_opened()
    try:
        while True:
            data = await websocket.receive_bytes()
            audio = np.frombuffer(data, dtype=np.float32).copy()
            if len(audio) != vad_runtime.WINDOW_SIZE:
                continue
            result = await vad_runtime.vad_runtime.infer_frame(audio)
            await websocket.send_text(json.dumps(result, ensure_ascii=False))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.error("VAD WebSocket 错误: %s", e)
    finally:
        await vad_runtime.vad_runtime.connection_closed()
        try:
            await websocket.close()
        except Exception:
            pass


@app.post("/faust/audio/tts")
async def speech_tts_post(payload: dict):
    text = ""
    lang = None
    if isinstance(payload, dict):
        text = str(payload.get("text") or "").strip()
        lang = payload.get("lang") or payload.get("text_language")
    if not text:
        raise HTTPException(status_code=400, detail="缺少 TTS 文本")

    conf.reload_configs()
    try:
        audio_bytes, content_type = await speech_runtime.synthesize_tts(text, lang)
        return Response(content=audio_bytes, media_type=content_type)
    except speech_runtime.SpeechRuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS 代理失败: {e}")


@app.post("/faust/audio/asr")
async def speech_asr_post(file: UploadFile = File(...)):
    conf.reload_configs()
    try:
        audio_bytes = await file.read()
        result = await asyncio.to_thread(
            speech_runtime.transcribe_audio,
            file.filename or "audio.wav",
            audio_bytes,
            file.content_type or "audio/wav",
        )
        return result
    except speech_runtime.SpeechRuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ASR 代理失败: {e}")


@app.post("/faust/status")
async def status_post():
    """Returns JSON {'status': 'ok'} to indicate the service is running."""
    active_tasks = trigger_manager.get_trigger_information()
    return {"status": "ok", "active_tasks": active_tasks}

async def _graceful_shutdown_task():
    global uvicorn_server
    log.info("正在优雅关闭...")
    await asyncio.sleep(0.1)

    uvicorn_server.should_exit = True
    log.info("Uvicorn 关闭标志已设置")

@app.post("/faust/shutdown")
async def shutdown_post():
    """Triggers a graceful shutdown for the FAUST backend process."""
    asyncio.create_task(_graceful_shutdown_task())
    return {"status": "shutting_down"}
@app.on_event("shutdown")
async def shutdown_event():
    global plugin_heartbeat_task
    log.info("开始关闭 Agent...")
    if not args.save_in_memory:
        await conn.commit()
        await conn.close()
        await conn_for_store.commit()
        await conn_for_store.close()
    trigger_manager.stop_trigger_watchdog_thread()
    if plugin_heartbeat_task is not None:
        plugin_heartbeat_task.cancel()
        try:
            await plugin_heartbeat_task
        except Exception:
            pass
        plugin_heartbeat_task = None
    await araya_runtime.get_araya_runtime(refresh=True).shutdown()
    trigger_manager.exitflag=True
    await vad_runtime.vad_runtime.shutdown()
    log.info("正在关闭 FAUST 后端主服务...")

if __name__ == "__main__":
    log.info("FAUST 后端主服务正在启动，端口 %d...", PORT)
    config = uvicorn.Config(app, host="127.0.0.1", port=PORT,log_level="info")
    uvicorn_server = uvicorn.Server(config)
    uvicorn_server.run()