print("[main]Starting")
import requests
from fastapi import FastAPI,WebSocket, WebSocketDisconnect
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
import argparse
import subprocess
import tqdm
from os.path import join as pjoin
from faust_backend.config_loader import args
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
AGENT_NAME=conf.AGENT_NAME
if not os.path.exists(os.path.join("agents",f"{AGENT_NAME}")):
    print(f"[main] Agent file for '{AGENT_NAME}' not found. Please make sure 'agents/{AGENT_NAME}' exists.")
    exit(1)
AGENT_ROOT=os.path.join("agents",f"{AGENT_NAME}")
def makeup_init_prompt():
    global PROMPT
    with open(os.path.join(AGENT_ROOT,"AGENT.md"),"r",encoding="utf-8") as f:
        PROMPT=f.read()
    with open(os.path.join(AGENT_ROOT,"ROLE.md"),"r",encoding="utf-8") as f:
        PROMPT+=f.read()
    with open(os.path.join(AGENT_ROOT,"COREMEMORY.md"),"r",encoding="utf-8") as f:
        PROMPT+=f.read()
    with open(os.path.join(AGENT_ROOT,"TASK.md"),"r",encoding="utf-8") as f:
        PROMPT+=f.read()
    print("[main.startup]PROMPT makeup done.")
    print("[main.startup]PROMPT content:\n",PROMPT)
makeup_init_prompt()

THREAD_ID=84
# HTTP POST chat endpoint
#agent.invoke({"messages":[{"role":"system","content":PROMPT}]},{"configurable":{"thread_id":THREAD_ID}})
def startServices():
    if args.run_other_backend_services:
        print("[main] Starting other backend services...")
        for service in tqdm.tqdm(["ASR.bat", "TTS.bat"]):
            print("[service_manager] Starting service:", service)
            process=subprocess.run([service],check=False)
            process.stdout=subprocess.DEVNULL
        print("[main] Other backend services started.")


async def invoke_agent_locked(target_agent, payload, config=None):
    if config is None:
        config = {"configurable": {"thread_id": THREAD_ID}}
    async with agent_lock:
        return await target_agent.ainvoke(payload, config)


async def stream_agent_locked(target_agent, payload, config=None):
    if config is None:
        config = {"configurable": {"thread_id": THREAD_ID}}
    async with agent_lock:
        async for message_chunk, metadata in target_agent.astream(payload, config, stream_mode="messages"):
            if message_chunk.content and metadata.get("langgraph_node")!="tools":
                yield message_chunk, metadata
        
startServices()
@app.on_event("startup")
async def startup_event():
    global agent,checkpointer,conn,storer,conn_for_store
    #--- Initialize the agent and its tools&middleware, including setting up the checkpoint saver and store.
    if not os.path.exists(pjoin(AGENT_ROOT,'faust_checkpoint.db')):
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
    middlewares=[]
    #--- End of checkpoint middleware and store setup
    #--- Create the agent with the specified model, tools, and checkpoint/store.
    agent=create_agent(model="deepseek-chat",checkpointer=checkpointer,tools=llm_tools.toollist,store=storer)
    print("[main]Agent created with Deepseek-chat model and tools.")
    if NOT_INITIALIZED:
        await invoke_agent_locked(agent,{"messages":[{"role":"system","content":PROMPT}]})
    else:
        await invoke_agent_locked(agent,{"messages":[{"role":"user","content":"对话重新开始了"}]})
    if os.path.exists("faust_main.log"):
        with open("faust_main.log","r",encoding="utf-8") as f:
            t=f.readlines()[-5:]
        print("[Faust.backend.main]日志内容：",t)
        await invoke_agent_locked(agent,{"messages":[{"role":"user","content":f"这是你自上次对话以来后的日志：{' '.join(t)}"}]})
    
    #--- Start the trigger watchdog thread to monitor and activate triggers.
    print("[main] Trigger Watchdog Thread starting...")
    trigger_manager.start_trigger_watchdog_thread()
    try:
        await minecraft_client.ensure_started()
    except Exception as e:
        print(f"[main] Minecraft bridge not connected on startup: {e}")
    llm_tools.STARTED=True# 声明启动完成
    print("[main]FAUST Backend Main Service started.")
    
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
                await websocket.send_text(f"TTS {reply}")
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
    print("Shutting down FAUST Backend Main Service...")

if __name__ == "__main__":
    print(f"Starting FAUST Backend Main Service on port {PORT}...")
    config = uvicorn.Config(app, host="127.0.0.1", port=PORT)
    uvicorn_server = uvicorn.Server(config)
    uvicorn_server.run()