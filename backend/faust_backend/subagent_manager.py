from langchain.agents import create_agent
from langchain_core.tools import StructuredTool
from dataclasses import dataclass,field
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
import aiosqlite
from langchain.chat_models import BaseChatModel
from asyncio import Event,Lock
import asyncio
from langchain.agents.middleware.types import AgentMiddleware
import json
from langgraph.graph.state import CompiledStateGraph
from typing import Any

@dataclass
class Subagent:
    agent: CompiledStateGraph | None = None
    systemPrompt:str | None = None
    toolset: set[StructuredTool] = field(default_factory=set)
    outputStore: list = field(default_factory=list)
    name: str | None = None
    lock: Lock = field(default_factory=asyncio.Lock)

class SubagentManager:
    def __init__(self,checkpointerPath:str=None):
        self.subagents: dict[str, Subagent] = {}
        self.subagentPublicToolSets:dict[str,set[StructuredTool]]= {}
        self.checkpointerPath = checkpointerPath or ":memory:"
        self.checkpointerConn:aiosqlite.Connection | None = None
        self.checkpointer:AsyncSqliteSaver | None = None
        self._checkpointer_lock = asyncio.Lock()
        self.chatModel:BaseChatModel=None
        self.abortEvents:dict[str,Event]={}

    async def _ensure_checkpointer(self) -> AsyncSqliteSaver:
        if self.checkpointer is not None:
            return self.checkpointer

        async with self._checkpointer_lock:
            if self.checkpointer is not None:
                return self.checkpointer
            self.checkpointerConn = await aiosqlite.connect(self.checkpointerPath)
            self.checkpointer = AsyncSqliteSaver(conn=self.checkpointerConn)
            setup = getattr(self.checkpointer, "setup", None)
            if callable(setup):
                result = setup()
                if asyncio.iscoroutine(result):
                    await result
            return self.checkpointer

    def _create_langchain_agent(self,
                                tools: list[StructuredTool],
                                systemPrompt: str,
                                middlewares: list[AgentMiddleware],
                                checkpointer: AsyncSqliteSaver):
        kwargs: dict[str, Any] = {
            "model": self.chatModel,
            "tools": tools,
            "checkpointer": checkpointer,
            "system_prompt": systemPrompt,
        }
        if middlewares:
            try:
                kwargs["middleware"] = middlewares
                return create_agent(**kwargs)
            except TypeError:
                kwargs.pop("middleware", None)
            try:
                kwargs["middlewares"] = middlewares
                return create_agent(**kwargs)
            except TypeError:
                kwargs.pop("middlewares", None)
        return create_agent(**kwargs)
        
    def newToolset(self,toolSetName:str="Unnamed Toolset",
                         tools:set[StructuredTool] | None=None):
        self.subagentPublicToolSets[toolSetName]=set(tools or set())
    
    def setChatModel(self,chatModel):
        self.chatModel=chatModel

    async def newSubagent(self,agent_name:str="Unnamed Agent",
                          toolsetsNames:list[str] | None=None,
                          systemPrompt:str="You are a helpful assistant.",
                          middlewares:list[AgentMiddleware] | None=None):
        if self.chatModel is None:
            raise RuntimeError("Chat model is not configured")

        checkpointer = await self._ensure_checkpointer()
        requested_toolsets = list(toolsetsNames or [])
        active_middlewares = list(middlewares or [])
        
        newAgentTools:list[StructuredTool]=[]
        if requested_toolsets:
            for toolsetName in requested_toolsets:
                if toolsetName not in self.subagentPublicToolSets:
                    raise ValueError(f"Unknown toolset: {toolsetName}")
                newAgentTools+=list(self.subagentPublicToolSets[toolsetName])
        
        langchainAgent = self._create_langchain_agent(
            tools=newAgentTools,
            systemPrompt=systemPrompt,
            middlewares=active_middlewares,
            checkpointer=checkpointer,
        )

        subagentInstance=Subagent(systemPrompt=systemPrompt,
                                  agent=langchainAgent,
                                  toolset=set(newAgentTools),
                                  name=agent_name)
        
        self.subagents[agent_name]=subagentInstance

    async def ainvokeSubagent(self,agent_name:str,message:dict,lockTimeout:int|None=None):
        if agent_name not in self.subagents:
            raise ValueError(f"Not a valid subagent name: {agent_name}")
        
        subagent = self.subagents[agent_name]
        lock = subagent.lock

        if lockTimeout:
            try:
            # 锁空闲时会立刻成功；被占用时立即超时并抛出异常
                await asyncio.wait_for(lock.acquire(), timeout=lockTimeout)
            except asyncio.TimeoutError:
                raise RuntimeError(f"Agent '{agent_name}' is currently locked.")
        else:
            await lock.acquire()
        
        config = {"configurable": {"thread_id": agent_name}, "recursion_limit": 300}
        subagent.outputStore.append([message])

        if self.abortEvents.get(agent_name) is not None:
            raise RuntimeError(f"Agent '{agent_name}' already has an active abort event.")
        abort_event = Event()
        self.abortEvents[agent_name] = abort_event

        try:
            async for event in self._ainvokeAgent(subagent.agent,config=config,payload=message,abortEvent=abort_event):
                yield event
                subagent.outputStore[-1].append(event)
        finally:
            self.abortEvents.pop(agent_name, None)
            if lock.locked():
                lock.release()
            

    async def abortSubagent(self,agent_name):
        abort_event = self.abortEvents.get(agent_name)
        if abort_event is None:
            return False
        abort_event.set()
        return True

    def getOutputStore(self,agent_name:str):
        return self.subagents[agent_name].outputStore
            

    @staticmethod
    def _is_ai_message_chunk(message_chunk) -> bool:
        msg_type = str(message_chunk.type).strip().lower()
        if msg_type == "ai":
            return True
        cls_name = message_chunk.__class__.__name__.lower()
        return "aimessage" in cls_name
    
    @staticmethod
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
                if str(block.get("type") or "").strip().lower() == "text":
                    text_val = block.get("text")
                    if text_val is not None:
                        parts.append(str(text_val))
            return "".join(parts)
        return str(content)
    
    @staticmethod
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
    
    @staticmethod
    def _tool_value_to_text(value) -> str | dict:
        if value is None:
            return ""
        if isinstance(value, dict):
            return value  # 让外层的 json.dumps 只序列化一次
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, indent=2)
        except Exception:
            return str(value)
        
    async def _ainvokeAgent(self,agent:CompiledStateGraph,config:dict,payload:dict,abortEvent:Event=None):
        """Async Invoke A langchain Agent and trun its output to dict.

        Args:
            agent: Langchain Agent
            config (dict): config dict
            payload (dict): messageCon

        Yields:
            _type_: _description_
        """        
        async for event in agent.astream_events(payload,config=config,version="v2"):
            if abortEvent and abortEvent.is_set():
                raise asyncio.CancelledError("User interrupted")
            if not isinstance(event, dict):
                continue
            event_name = str(event.get("event") or "").strip().lower()
            data = event.get("data") or {}
            if event_name == "on_chat_model_stream":
                chunk = data.get("chunk")
                if not chunk or not SubagentManager._is_ai_message_chunk(chunk):
                    continue
                # Extract reasoning/thinking delta (OpenAI o1/o3, DeepSeek R1, etc.)
                additional_kwargs = getattr(chunk, "additional_kwargs", {}) or {}
                reasoning = (
                    additional_kwargs.get("reasoning_content")
                    or additional_kwargs.get("reasoning")
                    or additional_kwargs.get("think")
                )
                if reasoning:
                    yield {"type": "reasoning_delta", "content": reasoning}
                delta_text = SubagentManager._message_content_to_text(chunk.content)
                if delta_text:
                    yield {"type": "delta", "content": delta_text}
                continue
            if event_name == "on_tool_start":
                yield {
                    "type": "tool_start",
                    "tool_name": str(event.get("name") or data.get("name") or "tool").strip(),
                    "args": SubagentManager._normalize_tool_args(data.get("input")),
                    "call_id": str(event.get("run_id") or ""),
                }
                continue
            if event_name == "on_tool_end":
                yield {
                    "type": "tool_result",
                    "tool_name": str(event.get("name") or data.get("name") or "tool").strip(),
                    "output": SubagentManager._tool_value_to_text(data.get("output")),
                    "call_id": str(event.get("run_id") or ""),
                }
                continue