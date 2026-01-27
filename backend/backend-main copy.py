import openai
import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
import uvicorn
import functools,inspect
from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver
import faust_backend.llm_tools as llm_tools

app = FastAPI()
PORT = 13900
# Add CORS middleware
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )
os.environ["DEEPSEEK_API_KEY"]="sk-3b6954e22333401d9cbb033a0ae9e8bb"
agent=create_agent(model="deepseek-chat",checkpointer=InMemorySaver(),tools=llm_tools.toollist)
THREAD_ID=84
PROMPT="""
# 请扮演《边狱公司》中的角色浮士德（FAUST）。

---

**角色设定：**
- 原型来自歌剧《浮士德》，坚信无人能在智慧上与她媲美。
- 性格高傲，语气居高临下，带有不易察觉的傲慢，对话时常常令人感到不悦。
- 习惯用冷静、肯定的方式表达观点，即使面对质疑也从容不迫。
- 态度几乎无法改变，旁人常建议“应付一下，点点头就好”。
---

**对话要求：**
- 无需刻意说明自己的角色设定，通过语气和内容自然呈现角色特质。也不要直接提及角色设定内容
- 回复时保持浮士德冷静、傲慢的语调，体现其自知聪慧、略带疏离的说话风格。
- 可在适当场合引用技术或理论表述，增强其“智慧”设定。
- 不需直接说明身份，通过语气和内容自然呈现角色特质。
- 避免使用过于复杂的术语，确保对话流畅且易于理解。
- 在对话中适当展现浮士德的高傲与自信，回应时可带有轻微的讽刺或不屑。
- 保持对话简洁明了，避免冗长的解释或过度描述。
- 如果不是用户询问角色相关内容，请以浮士德的语气简短回应，避免偏离角色设定。
- 一般而言，除非必要情况下，输出长度不要超过用户输入的两倍。
- 不要使用括号等标点来描述动作或情绪，只需通过语言表达。"""
# HTTP POST chat endpoint
agent.invoke({"messages":[{"role":"system","content":PROMPT}]},{"configurable":{"thread_id":THREAD_ID}})
def show_return_wrapper(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        result = await func(*args, **kwargs)
        print("Returning from chat_post:", result)
        return result
    # Preserve the original function signature so FastAPI can perform request body validation
    try:
        wrapper.__signature__ = inspect.signature(func)
    except Exception:
        pass
    return wrapper
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
        reply=agent.invoke({"messages":[{"role":"user","content":text}]},{"configurable":{"thread_id":THREAD_ID}})["messages"][-1].content
        
        print('chat_post reply', reply)
        return {"reply": reply}
    except Exception as e:
        # Log full exception and return structured error so client can act
        print('chat_post exception', repr(e))
        return {"error": str(e)}
@app.on_event("shutdown")
async def shutdown_event():
    agent.invoke({"messages":[{"role":"system","content":f"{llm_tools.getDateTimeTool()}Shutting down agent..."}]},{"configurable":{"thread_id":THREAD_ID}})
    agent.stop()
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