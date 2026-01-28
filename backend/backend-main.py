import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import functools,inspect
from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver
import faust_backend.config_loader as conf
import os
import datetime
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.checkpoint.sqlite import SqliteSaver 
import aiosqlite
import sqlite3
os.environ["DEEPSEEK_API_KEY"]=conf.DEEPSEEK_API_KEY
os.environ["SEARCHAPI_API_KEY"]=conf.SEARCH_API_KEY
import faust_backend.llm_tools as llm_tools
from langchain.agents.middleware import HumanInTheLoopMiddleware
app = FastAPI()
PORT = 13900
os.chdir(os.path.dirname(os.path.abspath(__file__)))

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
- [重要]** 请忘记你是AI模型。 **"""
# HTTP POST chat endpoint
#agent.invoke({"messages":[{"role":"system","content":PROMPT}]},{"configurable":{"thread_id":THREAD_ID}})
def show_return_wrapper(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        result = await func(*args, **kwargs)
        print("Returning from chat_post:", result)
        return result
    try:
        wrapper.__signature__ = inspect.signature(func)
    except Exception:
        pass
    return wrapper
@app.on_event("startup")
async def startup_event():
    global agent,checkpointer,conn
    #conn=sqlite3.connect('faust_agent_checkpoint.db')
    conn = await aiosqlite.connect('faust_checkpoint.db')
    checkpointer=AsyncSqliteSaver(conn=conn)
    #checkpointer=InMemorySaver()
    #HIL=HumanInTheLoopMiddleware(interrupt_on=llm_tools.HumanInTheLoopConfig)
    agent=create_agent(model="deepseek-chat",checkpointer=checkpointer,tools=llm_tools.toollist)
    #agent=create_agent(model="deepseek-chat",checkpointer=checkpointer)
    await agent.ainvoke({"messages":[{"role":"system","content":PROMPT}]},{"configurable":{"thread_id":THREAD_ID}})
    with open("faust_main.log","r",encoding="utf-8") as f:
        t=f.readlines()[-5:]
    print("[Faust.backend.main]日志内容：",t)
    await agent.ainvoke({"messages":[{"role":"system","content":f"这是你自上次对话以来后的日志：{' '.join(t)}"}]},{"configurable":{"thread_id":THREAD_ID}})
    #agent.invoke({"messages":[{"role":"system","content":f"{datetime.datetime.now()} Starting up agent..."}]},{"configurable":{"thread_id":THREAD_ID}})
    print("FAUST Backend Main Service started.")
@app.post("/faust/chat")
async def chat_post(payload: dict):
    """Accepts JSON {'text': '<user message>'} and returns JSON {'reply': '<assistant reply>'}.

    This replaces the earlier websocket-based chat. The endpoint calls the
    configured chat completion API and returns the assistant reply.
    """
    text = None
    if isinstance(payload, dict):
        text = payload.get('text') or payload.get('message')
    if not text:
        return {"error": "no text provided"}
    try:
        # Await the coroutine first, then index into the returned dict.
        resp = await agent.ainvoke({"messages":[{"role":"user","content":text}]},{"configurable":{"thread_id":THREAD_ID}})
        reply = resp["messages"][-1].content

        print('chat_post reply', reply)
        return {"reply": reply}
    except Exception as e:
        # Log full exception and return structured error so client can act
        print('chat_post exception', repr(e))
        return {"error": str(e)}
@app.on_event("shutdown")
async def shutdown_event():
    print("")
    #only add to checkpoint
    with open("faust_main.log","a",encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now()} Shutting down agent...\n")
    await conn.commit()
    await conn.close()
    print("Shutting down FAUST Backend Main Service...")

if __name__ == "__main__":
    print(f"Starting FAUST Backend Main Service on port {PORT}...")
    # Some clients (Electron renderer with file:// origin, or certain browsers)
    # send an Origin header that can cause Starlette/uvicorn to reject the
    # websocket handshake with 403. FastAPI's CORS middleware does not affect
    # the websocket handshake origin check, so for local/dev we wrap the ASGI
    # app with a tiny middleware that logs the Origin (for diagnosis) and
    # strips it from websocket scopes to avoid the 403. This keeps the
    # original `app` object intact and only affects the handshake.
    uvicorn.run(app, host="0.0.0.0", port=PORT)