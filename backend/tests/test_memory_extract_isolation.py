"""回归：后台实体抽取不得冒泡进主 Agent 的事件流。

`memory.tools.schedule_extract` 必须用空白 contextvars 上下文创建任务。否则后台那次
LLM 调用会继承 LangChain 的 `var_child_runnable_config`，注册成主 run 的子 run，它的
`on_chat_model_stream` 冒泡进主 Agent 的 `astream_events`，被 `_stream_agent_producer`
当成主回复的 delta 发给前端（抽取的 JSON 逐块混进回复）。
"""

from __future__ import annotations

import asyncio

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda

from faust_backend.memory import tools as memory_tools


def test_schedule_extract_does_not_leak_into_parent_agent_stream(monkeypatch):
    fake_model = GenericFakeChatModel(
        messages=iter([AIMessage(content="BG_EXTRACT_JSON leaked token")])
    )
    bg_done = asyncio.Event()

    async def fake_bg_extract(text: str, doc_path: str = "") -> None:
        try:
            await fake_model.ainvoke([HumanMessage(content=text)])
        finally:
            bg_done.set()

    monkeypatch.setattr(memory_tools, "_bg_extract_and_save", fake_bg_extract)

    async def fake_agent_tool_run(_payload):
        memory_tools.schedule_extract("memory body", "/notes/x.md")
        await asyncio.wait_for(bg_done.wait(), 5.0)  # 后台跑完再结束主 run
        return "main reply"

    async def _run() -> list[str]:
        leaked: list[str] = []
        async for event in RunnableLambda(fake_agent_tool_run).astream_events(
            {}, version="v2"
        ):
            if event.get("event") != "on_chat_model_stream":
                continue
            chunk = (event.get("data") or {}).get("chunk")
            leaked.append(str(getattr(chunk, "content", "")))
        return leaked

    assert asyncio.run(_run()) == []
