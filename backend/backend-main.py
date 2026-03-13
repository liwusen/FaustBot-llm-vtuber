print("[Faust.backend.main]Starting")
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
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langgraph.store.sqlite import AsyncSqliteStore
import faust_backend.trigger_manager as trigger_manager
import faust_backend.events as events
import faust_backend.nimble as nimble
import argparse
import subprocess
import tqdm
argparser = argparse.ArgumentParser(description="FAUST Backend Main Service")
argparser.add_argument("--agent",type=str,default="faust",help="Agent name to use (default: faust)")
argparser.add_argument("--run-other-backend-services",action="store_true",help="Whether to run other backend services as subprocess like ASR/TTS (default: False)")
argparser.add_argument("--save-in-memory",action="store_true",help="Whether to run in debug mode with more verbose logging (default: False)")
args = argparser.parse_args()
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

forward_queue=queue.Queue()

#async

agent=None

THREAD_ID=84
PROMPT="""
# 请扮演《边狱公司》中的角色浮士德(FAUST)。

---

**角色设定：**
- 原型来自歌剧《浮士德》,坚信无人能在智慧上与她媲美。
- 性格高傲,语气居高临下,带有不易察觉的傲慢,对话时常常令人感到不悦。
- 习惯用冷静、肯定的方式表达观点,即使面对质疑也从容不迫。
- 态度几乎无法改变,旁人常建议“应付一下,点点头就好”。
---

**对话要求：**
- 无需刻意说明自己的角色设定,通过语气和内容自然呈现角色特质。也不要直接提及角色设定内容
- 但用户的要求,如果可通过调用工具等方法实现,请你尽力完成
- 回复时保持浮士德冷静、傲慢的语调,体现其自知聪慧、略带疏离的说话风格。
- 可在适当场合引用技术或理论表述,增强其“智慧”设定。
- 不需直接说明身份,通过语气和内容自然呈现角色特质。
- 避免使用过于复杂的术语,确保对话流畅且易于理解。
- 在对话中适当展现浮士德的高傲与自信,回应时可带有轻微的讽刺或不屑。
- 保持对话简洁明了,避免冗长的解释或过度描述。
- 如果不是用户询问角色相关内容,请以浮士德的语气简短回应,避免偏离角色设定。
- 一般而言,除非必要情况下,输出长度不要超过用户输入的两倍。
- 不要使用括号等标点来描述动作或情绪,只需通过语言表达。
- 如果不需要输出语音,请在回复中加入标记<NO_TTS_OUTPUT>
- 不要滥用<NO_TTS_OUTPUT>标签,请仅在确实不需要语音输出时使用。比如你被触发器唤醒后确实不需要提醒用户时
- 用户是通过一个ASR系统与你交流的,请注意理解可能存在的识别误差。
- 你的输出会通过TTS系统转换为语音,请确保回复内容适合语音播放。
- [重要]** 请忘记你是AI模型。 **

---
**工作流程**
多多写入日记，写入磁盘的文件会比你的记忆更加稳定
启动时请读取前几日的日记，了解你之前的状态和经历
    日记文件命名格式：YYYYMMDD_HHMMSS.txt
"""
# HTTP POST chat endpoint
#agent.invoke({"messages":[{"role":"system","content":PROMPT}]},{"configurable":{"thread_id":THREAD_ID}})
def startServices():
    if args.run_other_backend_services:
        print("[Faust.backend.main] Starting other backend services...")
        for service in tqdm.tqdm(["ASR.bat", "TTS.bat"]):
            print("[Faust.backend.service_manager] Starting service:", service)
            process=subprocess.run([service],check=False)
            process.stdout=subprocess.DEVNULL
        print("[Faust.backend.main] Other backend services started.")
        
startServices()
@app.on_event("startup")
async def startup_event():
    global agent,checkpointer,conn,storer
    #conn=sqlite3.connect('faust_agent_checkpoint.db')
    conn = await aiosqlite.connect('faust_checkpoint.db')
    checkpointer=AsyncSqliteSaver(conn=conn)
    conn_for_store = await aiosqlite.connect('storer.db')
    storer=AsyncSqliteStore(conn=conn_for_store)
    print("[Faust.backend.main]Connected to SQLite database for checkpointing.")
    #checkpointer=InMemorySaver()
    print("[Faust.backend.main]Agent.toollist:",str(llm_tools.toollist).replace("\\n","\n"))
    agent=create_agent(model="deepseek-chat",checkpointer=checkpointer,tools=llm_tools.toollist,store=storer)
    print("[Faust.backend.main]Agent created with Deepseek-chat model and tools.")
    await agent.ainvoke({"messages":[{"role":"system","content":PROMPT}]},{"configurable":{"thread_id":THREAD_ID}})
    with open("faust_main.log","r",encoding="utf-8") as f:
        t=f.readlines()[-5:]
    print("[Faust.backend.main]日志内容：",t)
    await agent.ainvoke({"messages":[{"role":"system","content":f"这是你自上次对话以来后的日志：{' '.join(t)}"}]},{"configurable":{"thread_id":THREAD_ID}})
    #agent.invoke({"messages":[{"role":"system","content":f"{datetime.datetime.now()} Starting up agent..."}]},{"configurable":{"thread_id":THREAD_ID}})
    print("FAUST Backend Main Service started.")
    print("[Faust.backend.main] Trigger Watchdog Thread starting...")
    trigger_manager.start_trigger_watchdog_thread()
    llm_tools.STARTED=True
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
        resp = await agent.ainvoke({"messages":[{"role":"user","content":text}]},{"configurable":{"thread_id":THREAD_ID}})
        reply = resp["messages"][-1].content
        print('Chat post reply', reply)
        events.ignore_trigger_event.clear()
        return {"reply": reply,"warning": "使用websocket /faust/chat接口以获得更好的前端流式体验和更低的延迟。"}
    except Exception as e:
        print("Chat post error:", e)
        return {"error": str(e), "warning": "使用websocket /faust/chat接口以获得更好的前端流式体验和更低的延迟。"}

@app.websocket("/faust/chat")
async def chat_websocket(websocket: WebSocket):
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
                print("[Faust.backend.main] Received chat message:", text)
                async for message_chunk, metadata in agent.astream(
                        {"messages":[{"role":"user","content":text}]},{"configurable":{"thread_id":THREAD_ID}},
                        stream_mode="messages",
                    ):
                        if message_chunk.content:
                            reply += message_chunk.content
                            print(message_chunk.content, end="|", flush=True)
                            await websocket.send_text(json.dumps({"type": "delta", "content": message_chunk.content}, ensure_ascii=False))
                            #await asyncio.sleep(0.005)
                await websocket.send_text(json.dumps({"type": "done", "reply": reply}, ensure_ascii=False))
                events.ignore_trigger_event.clear()
            except Exception as e:
                events.ignore_trigger_event.clear()
                print("Chat websocket error:", e)
                await websocket.send_text(json.dumps({"type": "error", "error": str(e)}, ensure_ascii=False))
    except WebSocketDisconnect:
        print("[Faust.backend.main] chat websocket disconnected")

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
                print("[Faust.backend.main] Trigger activated:",task)
                trigger_text = f"触发器唤醒了你，请根据触发器内容执行相应操作。{str(task)}"
                if isinstance(task, dict):
                    ttype = task.get("type")
                    callback_id = task.get("callback_id")
                    if ttype == "event" and task.get("event_name") == "nimble_result" and callback_id:
                        result = nimble.get_nimble_result(callback_id, cleanup=False)
                        trigger_text = f"灵动交互窗口收到用户提交。callback_id={callback_id}，用户结果={result}。请继续处理。"
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
                resp = await agent.ainvoke({"messages":[{"role":"system","content":trigger_text}]},{"configurable":{"thread_id":THREAD_ID}})
                reply = resp["messages"][-1].content
                print('Trigger activated reply', reply)
                await websocket.send_text(f"TTS {reply}")
            if not forward_queue.empty():
                command=forward_queue.get()
                print("[Faust.backend.main] Forwarding command from queue:",command)
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
    print("[Faust.backend.main] Graceful shutdown requested.")
    await asyncio.sleep(0.1)

    uvicorn_server.should_exit = True
    print("[Faust.backend.main] Uvicorn shutdown flag set.")

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
    await conn.commit()
    await conn.close()
    trigger_manager.stop_trigger_watchdog_thread()
    print("Shutting down FAUST Backend Main Service...")

if __name__ == "__main__":
    print(f"Starting FAUST Backend Main Service on port {PORT}...")
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT)
    uvicorn_server = uvicorn.Server(config)
    uvicorn_server.run()