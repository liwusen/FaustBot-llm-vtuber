print("[main]Starting")
from fastapi import FastAPI,WebSocket, WebSocketDisconnect, HTTPException, Query
import json
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver
import faust_backend.config_loader as conf
import faust_backend.backend2front as backend2frontend
import os
import datetime
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
import aiosqlite
import asyncio
import queue
os.environ["DEEPSEEK_API_KEY"]=conf.DEEPSEEK_API_KEY
os.environ["SEARCHAPI_API_KEY"]=conf.SEARCH_API_KEY
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
import faust_backend.rag_client as rag_client
from faust_backend.plugin_system import PluginManager
import tqdm
from os.path import join as pjoin
from faust_backend.config_loader import args
import time
import inspect
print("[main]Libs Loaded")
#Shared Events
app = FastAPI()
uvicorn_server = None
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
PORT = 13900
os.chdir(os.path.dirname(os.path.abspath(__file__)))
asyncio.run(backend2frontend.frontendGetMotions())
forward_queue=queue.Queue()
agent=None
agent_lock = asyncio.Lock()
plugin_manager = PluginManager()
plugin_hot_reload_task = None
plugin_hot_reload_busy = False
AGENT_NAME=conf.AGENT_NAME
PROMPT = ""
if not os.path.exists(os.path.join("agents",f"{AGENT_NAME}")):
    print(f"[main] Agent file for '{AGENT_NAME}' not found. Please make sure 'agents/{AGENT_NAME}' exists.")
    exit(1)
AGENT_ROOT=os.path.join("agents",f"{AGENT_NAME}")
def makeup_init_prompt():
    global PROMPT, AGENT_ROOT, AGENT_NAME
    AGENT_NAME = conf.AGENT_NAME
    AGENT_ROOT=os.path.join("agents",f"{AGENT_NAME}")
    with open(os.path.join(AGENT_ROOT,"AGENT.md"),"r",encoding="utf-8") as f:
        PROMPT=f.read()
    with open(os.path.join(AGENT_ROOT,"ROLE.md"),"r",encoding="utf-8") as f:
        PROMPT+=f.read()
    with open(os.path.join(AGENT_ROOT,"COREMEMORY.md"),"r",encoding="utf-8") as f:
        PROMPT+=f.read()
    with open(os.path.join(AGENT_ROOT,"TASK.md"),"r",encoding="utf-8") as f:
        PROMPT+=f.read()
makeup_init_prompt()

THREAD_ID=84
# HTTP POST chat endpoint
#agent.invoke({"messages":[{"role":"system","content":PROMPT}]},{"configurable":{"thread_id":THREAD_ID}})
def startServices():
    if not args.no_run_other_backend_services:
        print("[main] Starting backend services...")
        for service in tqdm.tqdm(service_manager.get_service_keys(), desc="[main]Starting services"):
            try:
                service_manager.start_service(service, wait=False)
            except Exception as e:
                print(f"[service_manager] Failed to start {service}: {e}")
            time.sleep(0.5)
        print("[main] Other backend services started.")


def schedule_rag_record_sync(user_text: str, assistant_text: str) -> None:
    if not getattr(conf, "RAG_ENABLED", True):
        return
    if not str(user_text).strip() or not str(assistant_text).strip():
        return
    print("[main] Scheduling RAG record sync for new chat history part.")
    async def _job():
        try:
            llm_tools.refresh_runtime_paths()
            record_path = await llm_tools.RAG_TRACKER.new_chat_history_part(user_text, assistant_text)
            print(f"[main] Chat record synced into RAG[增量更新模式]: {record_path}")
        except Exception as exc:
            print(f"[main] Failed to sync chat record into RAG: {exc}")

    asyncio.create_task(_job())

OVERWRITE_LOCK=True
async def invoke_agent_locked(target_agent, payload, config=None):
    if config is None:
        config = {"configurable": {"thread_id": THREAD_ID}}
    print("[main.ai_call] Waiting for lock")
    async with agent_lock:
        print("[main.ai_call] Start Invoking llm")
        res=await target_agent.ainvoke(payload, config)
        print("[main.ai_call] End Invoking llm")
        return res


async def stream_agent_locked(target_agent, payload, config=None):
    if config is None:
        config = {"configurable": {"thread_id": THREAD_ID}}
    print("[main.ai_call] Waiting for lock")
    async with agent_lock:
        print("[main.ai_call] Start Invoking llm")
        async for message_chunk, metadata in target_agent.astream(payload, config, stream_mode="messages"):
            if message_chunk.content and metadata.get("langgraph_node")!="tools":
                yield message_chunk, metadata
        print("[main.ai_call] End Invoking llm")
startServices()


def _compose_runtime_extensions():
    base_tools = list(llm_tools.toollist)
    tools = plugin_manager.compose_tools(base_tools=base_tools, agent_name=AGENT_NAME)
    middlewares = plugin_manager.compose_middlewares(agent_name=AGENT_NAME)
    return tools, middlewares


def _sync_plugin_trigger_filters():
    trigger_manager.set_append_filters([plugin_manager.filter_trigger_on_append])
    trigger_manager.set_fire_filters([plugin_manager.filter_trigger_on_fire])


async def _plugin_hot_reload_loop():
    global plugin_hot_reload_busy
    while True:
        try:
            status = plugin_manager.hot_reload_status()
            await asyncio.sleep(float(status.get("interval_sec") or 2.0))
            tick = plugin_manager.hot_reload_tick()
            if tick.get("changed") and not plugin_hot_reload_busy:
                plugin_hot_reload_busy = True
                try:
                    _sync_plugin_trigger_filters()
                    await rebuild_runtime(reset_dialog=False)
                finally:
                    plugin_hot_reload_busy = False
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[plugin.hot-reload] loop error: {e}")
            await asyncio.sleep(1.0)


def _create_agent_with_extensions(*, model: str, checkpointer, store):
    tools, middlewares = _compose_runtime_extensions()
    kwargs = {
        "model": model,
        "checkpointer": checkpointer,
        "tools": tools,
        "store": store,
    }

    sig = inspect.signature(create_agent)
    if "middlewares" in sig.parameters:
        kwargs["middlewares"] = middlewares
    elif "middleware" in sig.parameters:
        kwargs["middleware"] = middlewares
    elif middlewares:
        print("[plugin] create_agent 不支持 middleware 参数，已跳过插件 middlewares 注入")

    return create_agent(**kwargs)


async def rebuild_runtime(*, reset_dialog: bool = False):
    print("[main] Rebuilding runtime with reset_dialog =", reset_dialog)
    global agent, checkpointer, conn, storer, conn_for_store, AGENT_NAME, AGENT_ROOT
    conf.reload_configs()
    os.environ["DEEPSEEK_API_KEY"] = conf.DEEPSEEK_API_KEY
    os.environ["SEARCHAPI_API_KEY"] = conf.SEARCH_API_KEY
    AGENT_NAME = conf.AGENT_NAME
    AGENT_ROOT = os.path.join("agents", f"{AGENT_NAME}")
    print("[main]Rubuilding Target Agent:", AGENT_NAME)
    if not os.path.exists(AGENT_ROOT):
        raise FileNotFoundError(f"Agent file for '{AGENT_NAME}' not found. Please make sure 'agents/{AGENT_NAME}' exists.")

    makeup_init_prompt()
    llm_tools.refresh_runtime_paths()
    plugin_reload = plugin_manager.reload()
    print(f"[plugin] reload summary: {plugin_reload}")
    _sync_plugin_trigger_filters()
    if not args.save_in_memory:
        try:
            if 'conn' in globals() and conn:
                await conn.commit()
                await conn.close()
        except Exception:
            pass
        try:
            if 'conn_for_store' in globals() and conn_for_store:
                await conn_for_store.commit()
                await conn_for_store.close()
        except Exception:
            pass
        conn = await aiosqlite.connect(pjoin(AGENT_ROOT,'faust_checkpoint.db'))
        checkpointer=AsyncSqliteSaver(conn=conn)
        conn_for_store = await aiosqlite.connect(pjoin(AGENT_ROOT,'faust_store.db'))
        storer=AsyncSqliteStore(conn=conn_for_store)
        print(f"[main] Checkpoint and store initialized with SQLite for rebuild.\
               pos_checkpoint: {pjoin(AGENT_ROOT,'faust_checkpoint.db')},\
               pos_store: {pjoin(AGENT_ROOT,'faust_store.db')}")
    else:
        checkpointer=InMemorySaver()
        storer=InMemoryStore()
    print("[main] Checkpoint and store initialized for rebuild.")
    agent = _create_agent_with_extensions(model="deepseek-chat", checkpointer=checkpointer, store=storer)
    try:
        await admin_runtime.align_rag_agent(AGENT_NAME)
    except Exception as e:
        print(f"[main] RAG agent align skipped: {e}")
    print("[main] Agent recreated for rebuild.")
    if reset_dialog:
        await invoke_agent_locked(agent,{"messages":[{"role":"system","content":PROMPT}]})
    else:
        await invoke_agent_locked(agent,{"messages":[{"role":"user","content":f"请继续按当前角色设定工作。\n 如果你需要重新了解你的角色设定，请读取agents/{AGENT_NAME}/AGENT.md、ROLE.md、COREMEMORY.md、TASK.md等文件来获取最新的设定内容。\n 这一条对话无需写入日记"}]})
    print("[main] Runtime rebuild completed.")
    return {
        "agent_name": AGENT_NAME,
        "agent_root": AGENT_ROOT,
    }

@app.on_event("startup")
async def startup_event():
    global agent,checkpointer,conn,storer,conn_for_store,plugin_hot_reload_task
    #--- Initialize the agent and its tools&middleware, including setting up the checkpoint saver and store.
    if not os.path.exists(pjoin(AGENT_ROOT,'faust_checkpoint.db')):
        print(f"[main] Checkpoint database not found at {pjoin(AGENT_ROOT,'faust_checkpoint.db')}. Starting with a fresh checkpoint.")
        print("[main.startup]PROMPT makeup done.")
        print("[main.startup]PROMPT content:\n",PROMPT)
        NOT_INITIALIZED = True
    else:
        NOT_INITIALIZED = False
    if not args.save_in_memory:
        conn = await aiosqlite.connect(pjoin(AGENT_ROOT,'faust_checkpoint.db'))
        checkpointer=AsyncSqliteSaver(conn=conn)
        conn_for_store = await aiosqlite.connect(pjoin(AGENT_ROOT,'faust_store.db'))
        storer=AsyncSqliteStore(conn=conn_for_store)
    else:
        checkpointer=InMemorySaver()
        storer=InMemoryStore()
    plugin_reload = plugin_manager.reload()
    print(f"[plugin] reload summary: {plugin_reload}")
    _sync_plugin_trigger_filters()
    middlewares=[]
    #--- End of checkpoint middleware and store setup
    #--- Create the agent with the specified model, tools, and checkpoint/store.
    agent=_create_agent_with_extensions(model="deepseek-chat", checkpointer=checkpointer, store=storer)
    print("[main]Agent created with Deepseek-chat model and tools.")
    llm_tools.refresh_runtime_paths()
    if NOT_INITIALIZED:
        await invoke_agent_locked(agent,{"messages":[{"role":"system","content":PROMPT}]})
    else:
        await invoke_agent_locked(agent,{"messages":[{"role":"user","content":"请继续按当前角色设定工作。\n 如果你需要重新了解你的角色设定，请读取agents/{AGENT_NAME}/AGENT.md、ROLE.md、COREMEMORY.md、TASK.md等文件来获取最新的设定内容。\n 这一条对话无需写入日记"}]})
    try:
        await admin_runtime.align_rag_agent(AGENT_NAME)
    except Exception as e:
        print(f"[main] Startup RAG initialization skipped: {e}")
    #--- Start the trigger watchdog thread to monitor and activate triggers.
    print("[main] Trigger Watchdog Thread starting...")
    trigger_manager.start_trigger_watchdog_thread()
    try:
        await minecraft_client.ensure_started()
    except Exception as e:
        print(f"[main] Minecraft bridge not connected on startup: {e}")
    llm_tools.STARTED=True# 声明启动完成
    if plugin_hot_reload_task is None:
        plugin_hot_reload_task = asyncio.create_task(_plugin_hot_reload_loop())
    print("[main]FAUST Backend Main Service started.")


@app.get("/faust/admin/config")
async def admin_get_config():
    return admin_runtime.get_config_view()


@app.post("/faust/admin/config")
async def admin_save_config(payload: dict):
    return admin_runtime.save_config(payload or {})


@app.post("/faust/admin/config/reload")
async def admin_reload_config(payload: dict | None = None):
    info = await rebuild_runtime(reset_dialog=bool((payload or {}).get("reset_dialog", False)))
    return {
        "status": "ok",
        "runtime": info,
        "summary": admin_runtime.runtime_summary(),
        "callback": {
            "type": "runtime_reloaded",
            "scope": "config",
            "agent_name": info.get("agent_name"),
            "reset_dialog": bool((payload or {}).get("reset_dialog", False)),
        }
    }


@app.get("/faust/admin/runtime")
async def admin_runtime_summary_api():
    return {"status": "ok", "runtime": admin_runtime.runtime_summary()}


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


@app.post("/faust/admin/runtime/reload-agent")
async def admin_reload_agent():
    info = await rebuild_runtime(reset_dialog=False)
    return {
        "status": "ok",
        "runtime": info,
        "callback": {
            "type": "runtime_reloaded",
            "scope": "agent",
            "agent_name": info.get("agent_name"),
            "reset_dialog": False,
        }
    }


@app.post("/faust/admin/runtime/reload-all")
async def admin_reload_all():
    info = await rebuild_runtime(reset_dialog=True)
    return {
        "status": "ok",
        "runtime": info,
        "callback": {
            "type": "runtime_reloaded",
            "scope": "all",
            "agent_name": info.get("agent_name"),
            "reset_dialog": True,
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
    info = await rebuild_runtime(reset_dialog=True)
    return {
        "status": "ok",
        "switch": result,
        "runtime": info,
        "callback": {
            "type": "runtime_reloaded",
            "scope": "agent_switch",
            "agent_name": info.get("agent_name"),
            "reset_dialog": True,
        }
    }


@app.get("/faust/admin/live2d/models")
async def admin_list_live2d_models():
    return {"items": admin_runtime.list_available_models()}


@app.get("/faust/admin/plugins")
async def admin_list_plugins():
    return {
        "status": "ok",
        "items": plugin_manager.list_plugins(),
        "hot_reload": plugin_manager.hot_reload_status(),
    }


@app.post("/faust/admin/plugins/reload")
async def admin_reload_plugins(payload: dict | None = None):
    summary = plugin_manager.reload()
    _sync_plugin_trigger_filters()
    apply_runtime = bool((payload or {}).get("apply_runtime", True))
    runtime_info = None
    if apply_runtime:
        runtime_info = await rebuild_runtime(reset_dialog=False)
    return {
        "status": "ok",
        "reload": summary,
        "runtime": runtime_info,
        "items": plugin_manager.list_plugins(),
        "hot_reload": plugin_manager.hot_reload_status(),
    }


@app.get("/faust/admin/plugins/hot-reload")
async def admin_plugins_hot_reload_status():
    return {"status": "ok", "hot_reload": plugin_manager.hot_reload_status()}


@app.post("/faust/admin/plugins/hot-reload/start")
async def admin_plugins_hot_reload_start(payload: dict | None = None):
    interval_sec = (payload or {}).get("interval_sec")
    state = plugin_manager.configure_hot_reload(enabled=True, interval_sec=interval_sec)
    return {"status": "ok", "hot_reload": state}


@app.post("/faust/admin/plugins/hot-reload/stop")
async def admin_plugins_hot_reload_stop():
    state = plugin_manager.configure_hot_reload(enabled=False)
    return {"status": "ok", "hot_reload": state}


@app.post("/faust/admin/plugins/{plugin_id}/enable")
async def admin_enable_plugin(plugin_id: str, payload: dict | None = None):
    plugin_manager.set_plugin_enabled(plugin_id, True)
    apply_runtime = bool((payload or {}).get("apply_runtime", True))
    runtime_info = None
    if apply_runtime:
        runtime_info = await rebuild_runtime(reset_dialog=False)
    return {"status": "ok", "plugin_id": plugin_id, "enabled": True, "runtime": runtime_info}


@app.post("/faust/admin/plugins/{plugin_id}/disable")
async def admin_disable_plugin(plugin_id: str, payload: dict | None = None):
    plugin_manager.set_plugin_enabled(plugin_id, False)
    apply_runtime = bool((payload or {}).get("apply_runtime", True))
    runtime_info = None
    if apply_runtime:
        runtime_info = await rebuild_runtime(reset_dialog=False)
    return {"status": "ok", "plugin_id": plugin_id, "enabled": False, "runtime": runtime_info}


@app.post("/faust/admin/plugins/{plugin_id}/trigger-control/enable")
async def admin_enable_plugin_trigger_control(plugin_id: str, payload: dict | None = None):
    plugin_manager.set_trigger_control_enabled(plugin_id, True)
    _sync_plugin_trigger_filters()
    apply_runtime = bool((payload or {}).get("apply_runtime", False))
    runtime_info = None
    if apply_runtime:
        runtime_info = await rebuild_runtime(reset_dialog=False)
    return {"status": "ok", "plugin_id": plugin_id, "trigger_control": True, "runtime": runtime_info}


@app.post("/faust/admin/plugins/{plugin_id}/trigger-control/disable")
async def admin_disable_plugin_trigger_control(plugin_id: str, payload: dict | None = None):
    plugin_manager.set_trigger_control_enabled(plugin_id, False)
    _sync_plugin_trigger_filters()
    apply_runtime = bool((payload or {}).get("apply_runtime", False))
    runtime_info = None
    if apply_runtime:
        runtime_info = await rebuild_runtime(reset_dialog=False)
    return {"status": "ok", "plugin_id": plugin_id, "trigger_control": False, "runtime": runtime_info}


@app.post("/faust/admin/plugins/{plugin_id}/tools/{tool_name}/enable")
async def admin_enable_plugin_tool(plugin_id: str, tool_name: str, payload: dict | None = None):
    plugin_manager.set_tool_enabled(plugin_id, tool_name, True)
    apply_runtime = bool((payload or {}).get("apply_runtime", True))
    runtime_info = None
    if apply_runtime:
        runtime_info = await rebuild_runtime(reset_dialog=False)
    return {
        "status": "ok",
        "plugin_id": plugin_id,
        "tool_name": tool_name,
        "enabled": True,
        "runtime": runtime_info,
    }


@app.post("/faust/admin/plugins/{plugin_id}/tools/{tool_name}/disable")
async def admin_disable_plugin_tool(plugin_id: str, tool_name: str, payload: dict | None = None):
    plugin_manager.set_tool_enabled(plugin_id, tool_name, False)
    apply_runtime = bool((payload or {}).get("apply_runtime", True))
    runtime_info = None
    if apply_runtime:
        runtime_info = await rebuild_runtime(reset_dialog=False)
    return {
        "status": "ok",
        "plugin_id": plugin_id,
        "tool_name": tool_name,
        "enabled": False,
        "runtime": runtime_info,
    }


@app.post("/faust/admin/plugins/{plugin_id}/middlewares/{middleware_name}/enable")
async def admin_enable_plugin_middleware(plugin_id: str, middleware_name: str, payload: dict | None = None):
    plugin_manager.set_middleware_enabled(plugin_id, middleware_name, True)
    apply_runtime = bool((payload or {}).get("apply_runtime", True))
    runtime_info = None
    if apply_runtime:
        runtime_info = await rebuild_runtime(reset_dialog=False)
    return {
        "status": "ok",
        "plugin_id": plugin_id,
        "middleware_name": middleware_name,
        "enabled": True,
        "runtime": runtime_info,
    }


@app.post("/faust/admin/plugins/{plugin_id}/middlewares/{middleware_name}/disable")
async def admin_disable_plugin_middleware(plugin_id: str, middleware_name: str, payload: dict | None = None):
    plugin_manager.set_middleware_enabled(plugin_id, middleware_name, False)
    apply_runtime = bool((payload or {}).get("apply_runtime", True))
    runtime_info = None
    if apply_runtime:
        runtime_info = await rebuild_runtime(reset_dialog=False)
    return {
        "status": "ok",
        "plugin_id": plugin_id,
        "middleware_name": middleware_name,
        "enabled": False,
        "runtime": runtime_info,
    }

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

def _rag_base_url() -> str:
    return getattr(conf, "RAG_API_URL", "http://127.0.0.1:18080")


@app.get("/faust/admin/rag/documents")
async def admin_list_rag_documents(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    search: str | None = None,
    time_from: str | None = None,
    time_to: str | None = None,
):
    try:
        data = await rag_client.rag_list_documents_paginated(
            base_url=_rag_base_url(),
            page=page,
            page_size=page_size,
            search=search,
            time_from=time_from,
            time_to=time_to,
        )
        return {"status": "ok", **data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RAG 文档列表查询失败: {e}")


@app.get("/faust/admin/rag/documents/{doc_id}")
async def admin_get_rag_document(doc_id: str):
    try:
        data = await rag_client.rag_get_document_detail(doc_id, base_url=_rag_base_url())
        return {"status": "ok", **data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RAG 文档详情查询失败: {e}")



@app.post("/faust/admin/rag/documents")
async def admin_create_rag_document(payload: dict):
    text = (payload or {}).get("text")
    doc_id = (payload or {}).get("doc_id")
    file_path = (payload or {}).get("file_path")
    try:
        data = await rag_client.rag_insert_document(
            text,
            doc_id=doc_id,
            file_path=file_path,
            base_url=_rag_base_url(),
        )
        return {"status": "ok", **data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RAG 文档创建失败: {e}")


@app.put("/faust/admin/rag/documents/{doc_id}")
async def admin_update_rag_document(doc_id: str, payload: dict):
    text = (payload or {}).get("text")
    file_path = (payload or {}).get("file_path")
    try:
        data = await rag_client.rag_update_document(
            doc_id,
            text=text,
            file_path=file_path,
            base_url=_rag_base_url(),
        )
        return {"status": "ok", **data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RAG 文档更新失败: {e}")


@app.delete("/faust/admin/rag/documents/{doc_id}")
async def admin_delete_rag_document(doc_id: str):
    try:
        data = await rag_client.rag_delete_document(doc_id, base_url=_rag_base_url())
        return {"status": "ok", **data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RAG 文档删除失败: {e}")
@app.get("/faust/admin/rag/documents/{doc_id}/content")
async def admin_get_rag_document_content(doc_id: str):
    try:
        data = await rag_client.rag_get_document_content(doc_id, base_url=_rag_base_url())
        return {"status": "ok", "content": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RAG 文档内容查询失败: {e}")
@app.post("/faust/chat")
#@deprecated(reason="This endpoint is kept for compatibility and development but the primary chat interface is now the websocket /faust/chat for frontend streaming.")
async def chat_post(payload: dict):
    """
     Post方式的聊天接口
        兼容性HTTP端点。内部仍然返回完整回复。
        已经弃用
        请使用websocket /faust/chat接口以获得更好的前端流式体验和更低的延迟。
        保留原因：方便调试,参见debug_console.py对此的使用
    """
    text = None
    if isinstance(payload, dict):
        text = payload.get('text') or payload.get('message')
    if not text:
        return {"error": "no text provided"}
    try:
        events.ignore_trigger_event.set()
        resp = await invoke_agent_locked(agent,{"messages":[{"role":"user","content":text}]})
        reply = resp["messages"][-1].content
        schedule_rag_record_sync(text, reply)
        print('Chat post reply', reply)
        events.ignore_trigger_event.clear()
        return {"reply": reply,"warning": "使用websocket /faust/chat接口以获得更好的前端流式体验和更低的延迟。"}
    except Exception as e:
        print("Chat post error:", e)
        return {"error": str(e), "warning": "使用websocket /faust/chat接口以获得更好的前端流式体验和更低的延迟。"}

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

            try:
                events.ignore_trigger_event.set()
                await websocket.send_text(json.dumps({"type": "start"}, ensure_ascii=False))
                reply = ""
                print("[main] Received chat message:", text)
                async for message_chunk, metadata in stream_agent_locked(agent,{"messages":[{"role":"user","content":text}]}):
                    if message_chunk.content and metadata.get("langgraph_node")!="tools":
                        reply += message_chunk.content
                        print(message_chunk.content, end="|", flush=True)
                        await websocket.send_text(json.dumps({"type": "delta", "content": message_chunk.content}, ensure_ascii=False))
                schedule_rag_record_sync(text, reply)
                await websocket.send_text(json.dumps({"type": "done", "reply": reply}, ensure_ascii=False))
                print()
                events.ignore_trigger_event.clear()
            except Exception as e:
                events.ignore_trigger_event.clear()
                print("Chat websocket error:", e)
                await websocket.send_text(json.dumps({"type": "error", "error": str(e)}, ensure_ascii=False))
    except WebSocketDisconnect:
        print("[main] chat websocket disconnected")

@app.websocket("/faust/command")
async def command_websocket(websocket: WebSocket):
    await websocket.accept()
    backend2frontend.FrontEndSay("Hello World! 你好,世界!")
    try:
        while True:
            if backend2frontend.hasFrontEndTask():
                await websocket.send_text(backend2frontend.popFrontEndTask())
            if trigger_manager.has_queue_task() and not events.ignore_trigger_event.is_set():
                # activate chat
                task=trigger_manager.get_next_trigger()
                print("[main] Trigger activated:",task)
                trigger_text = f"触发器唤醒了你，请根据触发器内容执行相应操作。{str(task)}"
                if isinstance(task, dict):
                    ttype = task.get("type")
                    callback_id = task.get("callback_id")
                    if ttype == "event" and task.get("event_name") == "nimble_result" and callback_id:
                        result = nimble.get_nimble_result(callback_id, cleanup=False)
                        trigger_text = f"灵动交互窗口收到用户提交。callback_id={callback_id}，用户结果={result}。请继续处理。"
                    elif ttype == "event" and task.get("event_name") == "mc_event":
                        payload = task.get("payload") or {}
                        trigger_text = (
                            "Minecraft事件唤醒了你。"
                            f"事件类型={payload.get('mc_event_type')}，"
                            f"事件详情={json.dumps(payload, ensure_ascii=False)}。"
                            "请结合当前游戏状态，决定是否调用 Minecraft 工具继续操作。"
                        )
                    elif ttype == "nimble-reminder" and callback_id:
                        session = nimble.get_nimble_session(callback_id)
                        if not session:
                            continue
                        trigger_text = f"灵动交互窗口仍在等待用户操作。callback_id={callback_id}，标题={session.get('title')}，提醒说明={task.get('recall_description') or session.get('recall_text')}。请判断是否需要继续引导用户。"
                    elif ttype == "nimble-expire" and callback_id:
                        session = nimble.close_nimble_session(callback_id, reason="expired")
                        if session:
                            trigger_manager.delete_trigger(session["result_trigger_id"])
                            trigger_manager.delete_trigger(session["reminder_trigger_id"])
                            trigger_manager.delete_trigger(session["expire_trigger_id"])
                            backend2frontend.FrontEndCloseNimbleWindow({"callback_id": callback_id, "reason": "expired"})
                        trigger_text = f"灵动交互窗口已过期关闭。callback_id={callback_id}。如有必要，请重新创建更明确的新窗口。"
                resp = await invoke_agent_locked(agent,{"messages":[{"role":"system","content":trigger_text}]})
                reply = resp["messages"][-1].content
                print('Trigger activated reply', reply)
                await websocket.send_text(f"SAY {reply}")
            if not forward_queue.empty():
                command=forward_queue.get()
                print("[main] Forwarding command from queue:",command)
                await websocket.send_text(f"{command}")
            await asyncio.sleep(0.05)
    except Exception as e:
        print("Websocket error:", e)
        await websocket.send_text(f"TTS backend error::{e}")
@app.post("/faust/command/forward")
async def command_forward_post(payload: dict):
    """Forwards a command from frontend to the agent and returns the reply."""
    command = None
    if isinstance(payload, dict):
        command = payload.get('command')
    if not command:
        return {"error": "no command provided"}
    forward_queue.put(command)
    events.backend2frontendQueue_event.set()
    return {"status": "command forwarded"}
@app.post("/faust/humanInLoop/feedback")
async def human_in_loop_feedback_post(payload: dict):
    """Handles feedback from the human-in-the-loop system."""
    feedback = None
    print(payload)
    if isinstance(payload, dict):
        feedback = payload.get('feedback')
    if not feedback:
        return {"error": "no feedback provided"}
    if feedback == True:
        events.HIL_feedback_event.set()
    else:
        events.HIL_feedback_fail_event.set()
    return {"status": "feedback received"}

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
    print(f"Received feedback for command {command_id}: {feedback}")
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
@app.post("/faust/status")
async def status_post():
    """Returns JSON {'status': 'ok'} to indicate the service is running."""
    active_tasks = trigger_manager.get_trigger_information()
    return {"status": "ok", "active_tasks": active_tasks}

async def _graceful_shutdown_task():
    global uvicorn_server
    print("[main] Graceful shutdown requested.")
    await asyncio.sleep(0.1)

    uvicorn_server.should_exit = True
    print("[main] Uvicorn shutdown flag set.")

@app.post("/faust/shutdown")
async def shutdown_post():
    """Triggers a graceful shutdown for the FAUST backend process."""
    asyncio.create_task(_graceful_shutdown_task())
    return {"status": "shutting_down"}
@app.on_event("shutdown")
async def shutdown_event():
    global plugin_hot_reload_task
    print("")
    #only add to checkpoint
    with open("faust_main.log","a",encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now()} Shutting down agent...\n")
    if not args.save_in_memory:
        await conn.commit()
        await conn.close()
        await conn_for_store.commit()
        await conn_for_store.close()
    trigger_manager.stop_trigger_watchdog_thread()
    if plugin_hot_reload_task is not None:
        plugin_hot_reload_task.cancel()
        try:
            await plugin_hot_reload_task
        except Exception:
            pass
        plugin_hot_reload_task = None
    print("Shutting down FAUST Backend Main Service...")

if __name__ == "__main__":
    print(f"Starting FAUST Backend Main Service on port {PORT}...")
    config = uvicorn.Config(app, host="127.0.0.1", port=PORT)
    uvicorn_server = uvicorn.Server(config)
    uvicorn_server.run()