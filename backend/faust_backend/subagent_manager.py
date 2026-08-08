from langchain.agents import create_agent
from langchain_core.tools import StructuredTool
from dataclasses import dataclass, field
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
import aiosqlite
from langchain.chat_models import BaseChatModel
from asyncio import Event, Lock
import asyncio
import contextvars
from langchain.agents.middleware.types import AgentMiddleware
import json
import os
import time
from langgraph.graph.state import CompiledStateGraph
from pathlib import Path
from typing import Any, AsyncGenerator

from faust_backend.logger import get_logger

log = get_logger("faust.subagents")

@dataclass
class Subagent:
    agent: CompiledStateGraph | None = None
    systemPrompt: str | None = None
    toolset: list[StructuredTool] = field(default_factory=list)
    toolsetNames: list[str] = field(default_factory=list)
    outputStore: list = field(default_factory=list)
    eventLog: list[dict[str, Any]] = field(default_factory=list)
    name: str | None = None
    status: str = "idle"
    lastEvent: dict[str, Any] | None = None
    lastError: str = ""
    finalResult: str = ""
    updatedAt: float = field(default_factory=time.time)
    activeTask: asyncio.Task | None = None
    lock: Lock = field(default_factory=asyncio.Lock)


class SubagentManager:
    def __init__(self, checkpointerPath: str | None = None):
        self.subagents: dict[str, Subagent] = {}
        self.subagentPublicToolSets: dict[str, list[StructuredTool]] = {}
        self.checkpointerPath = checkpointerPath or ":memory:"
        self.checkpointerConn:aiosqlite.Connection | None = None
        self.checkpointer:AsyncSqliteSaver | None = None
        self._checkpointer_lock = asyncio.Lock()
        self.chatModel: BaseChatModel | None = None
        self.middlewares: list[AgentMiddleware] = []
        self.identityPrefix = "你是 FaustBot 主 Agent 创建的 Subagent。你需要专注执行分配给你的子任务，并把输出保持为可观察、可审计的工作流。"
        self.abortEvents: dict[str, Event] = {}
        self._status_dirty: bool = False
        self._event_queue: asyncio.Queue | None = None
        self._state_path = str(Path(self.checkpointerPath).with_name("subagents.json"))
        self._restored_payload = self._read_state_file()

    def _read_state_file(self) -> dict[str, Any]:
        if not self._state_path or not os.path.exists(self._state_path):
            return {}
        try:
            return json.loads(Path(self._state_path).read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_state_file(self) -> None:
        payload = {
            "subagents": [
                {
                    "name": subagent.name,
                    "systemPrompt": subagent.systemPrompt,
                    "toolsetNames": list(subagent.toolsetNames),
                    "outputStore": subagent.outputStore,
                    "eventLog": subagent.eventLog,
                    "status": subagent.status,
                    "lastEvent": subagent.lastEvent,
                    "lastError": subagent.lastError,
                    "finalResult": subagent.finalResult,
                    "updatedAt": subagent.updatedAt,
                }
                for subagent in self.subagents.values()
            ]
        }
        path = Path(self._state_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def restore_persisted_state(self) -> None:
        """从持久化文件恢复 Subagent 状态（不含 agent 实例）。"""
        payload = dict(self._restored_payload or {})
        self.subagents = {}
        for item in list(payload.get("subagents") or []):
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            toolset_names = [str(v) for v in list(item.get("toolsetNames") or [])]
            tools: list[StructuredTool] = []
            for toolset_name in toolset_names:
                tools.extend(list(self.subagentPublicToolSets.get(toolset_name) or []))
            # 与 newSubagent 保持一致：finalResultTool 是运行时单独注入的，
            # 不属于任何 toolset，恢复时必须补回，否则二次运行无法写入 finalResult
            tools.append(self._build_final_result_tool(name))
            status = str(item.get("status") or "idle")
            if status in {"running", "pending", "stopping"}:
                status = "stopped"
            self.subagents[name] = Subagent(
                agent=None,
                systemPrompt=str(item.get("systemPrompt") or ""),
                toolset=tools,
                toolsetNames=toolset_names,
                outputStore=list(item.get("outputStore") or []),
                eventLog=list(item.get("eventLog") or []),
                name=name,
                status=status,
                lastEvent=item.get("lastEvent") if isinstance(item.get("lastEvent"), dict) else None,
                lastError=str(item.get("lastError") or ""),
                finalResult=str(item.get("finalResult") or ""),
                updatedAt=float(item.get("updatedAt") or time.time()),
            )

    async def reset_persistent_state(self) -> None:
        """清空所有 Subagent 并重置持久化文件。"""
        for name in list(self.subagents.keys()):
            await self.removeSubagent(name)
        self.subagents = {}
        self.abortEvents = {}
        self._write_state_file()

    async def _ensure_checkpointer(self) -> AsyncSqliteSaver:
        if self.checkpointer is not None:
            return self.checkpointer

        async with self._checkpointer_lock:
            if self.checkpointer is not None:
                return self.checkpointer
            self.checkpointerConn = await aiosqlite.connect(self.checkpointerPath)
            self.checkpointer = AsyncSqliteSaver(conn=self.checkpointerConn)
            await self.checkpointer.setup()
            return self.checkpointer

    async def aclose(self) -> None:
        """关闭 SubagentManager：停止所有 Subagent 并关闭 checkpointer。"""
        for name in list(self.subagents.keys()):
            await self.removeSubagent(name)
        if self.checkpointerConn is not None:
            await self.checkpointerConn.commit()
            await self.checkpointerConn.close()
            self.checkpointerConn = None
        self.checkpointer = None

    def _create_langchain_agent(self,
                                tools: list[StructuredTool],
                                systemPrompt: str,
                                middlewares: list[AgentMiddleware],
                                checkpointer: AsyncSqliteSaver,
                                model: Any | None = None):
        kwargs: dict[str, Any] = {
            "model": model if model is not None else self.chatModel,
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
        
    def newToolset(self, toolSetName: str = "Unnamed Toolset",
                   tools: list[StructuredTool] | set[StructuredTool] | tuple[StructuredTool, ...] | None = None):
        """注册一个工具组，供 Subagent 创建时选择。"""
        self.subagentPublicToolSets[toolSetName] = list(tools or [])
    
    def setChatModel(self, chatModel: BaseChatModel):
        """设置 Subagent 使用的 LLM。"""
        self.chatModel = chatModel

    def setMiddlewares(self, middlewares: list[AgentMiddleware]) -> None:
        """设置所有 Subagent 共享的中间件。"""
        self.middlewares = list(middlewares or [])

    def setIdentityPrefix(self, prefix: str) -> None:
        """设置 Subagent 系统提示词的前缀标识。"""
        self.identityPrefix = str(prefix or "").strip() or self.identityPrefix

    def _compose_system_prompt(self, systemPrompt: str) -> str:
        prompt = str(systemPrompt or "").strip()
        if not prompt:
            return self.identityPrefix
        return f"{self.identityPrefix}\n\n{prompt}"

    def _touch_subagent(self, subagent: Subagent, *, status: str | None = None, last_error: str | None = None) -> None:
        old_status = subagent.status
        if status is not None:
            subagent.status = status
        if last_error is not None:
            subagent.lastError = last_error
        subagent.updatedAt = time.time()
        if status is not None and status != old_status:
            self._status_dirty = True
        self._write_state_file()

    def consume_status_dirty(self) -> bool:
        """检查并消费脏标记：Subagent 状态发生变化时返回 True。由 WS handler 轮询。"""
        dirty = self._status_dirty
        self._status_dirty = False
        return dirty

    def get_event_queue(self) -> asyncio.Queue:
        """获取 Subagent 流式事件队列（lazy-init），用于 WS 转发。"""
        if self._event_queue is None:
            self._event_queue = asyncio.Queue(maxsize=500)
        return self._event_queue

    def _append_event(self, subagent: Subagent, event: dict[str, Any]) -> None:
        normalized = dict(event or {})
        normalized["ts"] = time.time()
        subagent.lastEvent = normalized
        subagent.eventLog.append(normalized)
        if len(subagent.eventLog) > 200:
            subagent.eventLog = subagent.eventLog[-200:]
        self._touch_subagent(subagent)

    def _event_summary(self, event: dict[str, Any] | None) -> str:
        if not event:
            return ""
        event_type = str(event.get("type") or "").strip()
        if event_type in {"delta", "reasoning_delta"}:
            return str(event.get("content") or "")[:160]
        if event_type == "tool_start":
            return f"tool_start: {event.get('tool_name') or ''}"
        if event_type == "tool_result":
            return f"tool_result: {event.get('tool_name') or ''}"
        return json.dumps(event, ensure_ascii=False)[:160]

    @staticmethod
    def _tool_name(tool_item: StructuredTool) -> str:
        return str(getattr(tool_item, "name", "") or "").strip()

    def format_available_toolsets(self) -> str:
        """返回所有已注册工具组的可读列表（Agent 知识用）。"""
        lines = ["# Available Toolsets", ""]
        for toolset_name in sorted(self.subagentPublicToolSets.keys()):
            lines.append(f"## {toolset_name}")
            tool_names = [self._tool_name(tool_item) for tool_item in self.subagentPublicToolSets[toolset_name]]
            if tool_names:
                for tool_name in tool_names:
                    lines.append(f"- {tool_name}")
            else:
                lines.append("- (empty)")
            lines.append("")
        return "\n".join(lines).strip()

    def get_status(self, agent_name: str) -> dict[str, Any]:
        """获取指定 Subagent 的完整状态（含 recent_events）。"""
        if agent_name not in self.subagents:
            raise ValueError(f"Not a valid subagent name: {agent_name}")
        subagent = self.subagents[agent_name]
        return {
            "agent_id": f"subagent-{subagent.name}",
            "name": subagent.name,
            "status": subagent.status,
            "toolsets": list(subagent.toolsetNames),
            "system_prompt_summary": str(subagent.systemPrompt or "")[:240],
            "last_event": dict(subagent.lastEvent or {}),
            "last_event_summary": self._event_summary(subagent.lastEvent),
            "recent_events": [dict(item) for item in subagent.eventLog[-50:]],
            "last_error": subagent.lastError,
            "final_result": subagent.finalResult,
            "updated_at": subagent.updatedAt,
            "active_task": bool(subagent.activeTask and not subagent.activeTask.done()),
            "output_runs": len(subagent.outputStore),
            "event_count": len(subagent.eventLog),
        }

    def list_statuses(self) -> list[dict[str, Any]]:
        """获取所有 Subagent 的完整状态列表。"""
        return [self.get_status(name) for name in sorted(self.subagents.keys())]

    def _light_status(self, agent_name: str) -> dict[str, Any]:
        subagent = self.subagents[agent_name]
        return {
            "agent_id": f"subagent-{subagent.name}",
            "name": subagent.name,
            "status": subagent.status,
            "toolsets": list(subagent.toolsetNames),
            "system_prompt_summary": str(subagent.systemPrompt or "")[:240],
            "last_event_summary": self._event_summary(subagent.lastEvent),
            "final_result": subagent.finalResult,
            "last_error": subagent.lastError,
            "event_count": len(subagent.eventLog),
        }

    def list_statuses_light(self) -> list[dict[str, Any]]:
        """获取所有 Subagent 的轻量状态（不含 recent_events），用于高频 WS 推送。"""
        return [self._light_status(name) for name in sorted(self.subagents.keys())]

    def format_subagent_overview(self, agent_name: str) -> str:
        """返回 Subagent 概览文本（Agent 用）。"""
        status = self.get_status(agent_name)
        lines = [
            f"Subagent: {status['name']}",
            f"Status: {status['status']}",
            f"Toolsets: {', '.join(status['toolsets']) if status['toolsets'] else '(none)'}",
            f"ActiveTask: {status['active_task']}",
            f"UpdatedAt: {status['updated_at']}",
        ]
        if status["last_event_summary"]:
            lines.append(f"LastEvent: {status['last_event_summary']}")
        if status["last_error"]:
            lines.append(f"LastError: {status['last_error']}")
        if status["final_result"]:
            lines.append(f"FinalResult: {status['final_result'][:160]}")
        return "\n".join(lines)

    def format_subagent_final_result(self, agent_name: str) -> str:
        """返回 Subagent 的最终结论文本。"""
        if agent_name not in self.subagents:
            raise ValueError(f"Not a valid subagent name: {agent_name}")
        result = str(self.subagents[agent_name].finalResult or "").strip()
        return result or "(no final result)"

    def format_subagent_output(self, agent_name: str) -> str:
        """返回 Subagent 完整执行记录（含思考链、工具调用），供主 Agent 读取。"""
        if agent_name not in self.subagents:
            raise ValueError(f"Not a valid subagent name: {agent_name}")
        subagent = self.subagents[agent_name]
        runs = subagent.outputStore
        if not runs:
            return "(no subagent output)"
        lines: list[str] = [f"# System Prompt Of Subagent({agent_name})", "", str(subagent.systemPrompt or ""), ""]
        for run_index, run in enumerate(runs, start=1):
            lines.append(f"# Run {run_index}:Main Agent(yourself)")
            first_item = run[0] if run else {}
            main_message = ""
            if isinstance(first_item, dict):
                messages = list(first_item.get("messages") or [])
                if messages:
                    main_message = str((messages[-1] or {}).get("content") or "")
            lines.append(f"(主Agent消息):{main_message}")
            lines.append(f"# Run {run_index}:Subagent Output:")

            reasoning_buffer: list[str] = []
            text_buffer: list[str] = []

            def flush_reasoning() -> None:
                if reasoning_buffer:
                    lines.append(f"> 思考:{''.join(reasoning_buffer).strip()}")
                    reasoning_buffer.clear()

            def flush_text() -> None:
                if text_buffer:
                    content = ''.join(text_buffer).strip()
                    if content:
                        lines.append(content)
                    text_buffer.clear()

            for item in run[1:]:
                if not isinstance(item, dict):
                    continue
                event_type = str(item.get("type") or "").strip().lower()
                if event_type == "reasoning_delta":
                    reasoning_buffer.append(str(item.get("content") or ""))
                    continue
                if event_type == "delta":
                    flush_reasoning()
                    text_buffer.append(str(item.get("content") or ""))
                    continue
                flush_reasoning()
                flush_text()
                if event_type == "tool_start":
                    lines.append(f"> 调用工具:{str(item.get('tool_name') or '').strip()}")
                    continue
                if event_type == "error":
                    lines.append(f"> 错误:{str(item.get('content') or item.get('error') or '').strip()}")
                    continue
                if event_type == "final_result":
                    lines.append(f"> 最终结论:{str(item.get('content') or '').strip()}" )
                    continue
                if event_type in {"queued", "input", "tool_result", "stopping", "stopped"}:
                    continue
            flush_reasoning()
            flush_text()
            lines.append(f"# Run {run_index}:End")
            lines.append("")
        if str(subagent.finalResult or "").strip():
            lines.extend(["# Final Result", "", str(subagent.finalResult or "").strip()])
        return "\n".join(lines).rstrip()

    async def wait_for_subagents(self, agent_names: list[str] | None = None) -> list[dict[str, Any]]:
        """等待指定 Subagent 完成当前任务。agent_names 为空时等待所有。"""
        if agent_names:
            names = [str(name or "").strip() for name in agent_names if str(name or "").strip()]
        else:
            names = sorted(self.subagents.keys())
        wait_tasks: list[asyncio.Task] = []
        for name in names:
            subagent = self.subagents.get(name)
            if subagent is None:
                continue
            task = subagent.activeTask
            if task is not None and not task.done():
                wait_tasks.append(task)
        if wait_tasks:
            await asyncio.gather(*wait_tasks, return_exceptions=True)
        return [self._light_status(name) for name in names if name in self.subagents]

    async def newSubagent(self,agent_name:str="Unnamed Agent",
                          toolsetsNames:list[str] | None=None,
                          systemPrompt:str="You are a helpful assistant.",
                          middlewares:list[AgentMiddleware] | None=None,
                          model:str | None=None):
        """创建一个新的 Subagent。model 为 'provider::model' spec；None 用默认 Subagent 模型。
        重复名称会报错。"""
        if self.chatModel is None:
            raise RuntimeError("Chat model is not configured")
        if agent_name in self.subagents:
            raise ValueError(f"Subagent already exists: {agent_name}")

        checkpointer = await self._ensure_checkpointer()
        # model 可选：None 用默认 Subagent 模型；传入则校验白名单并构建对应模型
        chat_model = self.chatModel
        if model is not None:
            from faust_backend.runtime import state as runtime_state
            from faust_backend.provider import (
                build_ReasoningChatOpenAI_from_spec,
                is_subagent_model_allowed,
            )
            providers = runtime_state.get_model_providers()
            if not is_subagent_model_allowed(providers, model):
                raise ValueError(f"Model '{model}' not in subagent_models whitelist.")
            # [R5] thinking 开关与主 Agent 一致：由目标 provider 的 thinking_type 决定
            _pname, _ = model.split("::", 1)
            _prov = next((p for p in providers.providers if p.name == _pname), None)
            _thinking = "medium" if _prov and _prov.thinking_type != "none" else None
            chat_model = await build_ReasoningChatOpenAI_from_spec(
                providers, spec=model, intensity=_thinking
            )
        requested_toolsets = list(toolsetsNames or [])
        active_middlewares = list(middlewares if middlewares is not None else self.middlewares)
        
        newAgentTools:list[StructuredTool]=[]
        if requested_toolsets:
            for toolsetName in requested_toolsets:
                if toolsetName not in self.subagentPublicToolSets:
                    raise ValueError(f"Unknown toolset: {toolsetName}")
                newAgentTools += list(self.subagentPublicToolSets[toolsetName])

        newAgentTools.append(self._build_final_result_tool(agent_name))

        final_prompt = self._compose_system_prompt(systemPrompt)
        
        langchainAgent = self._create_langchain_agent(
            tools=newAgentTools,
            systemPrompt=final_prompt,
            middlewares=active_middlewares,
            checkpointer=checkpointer,
            model=chat_model,
        )

        subagentInstance=Subagent(systemPrompt=final_prompt,
                                  agent=langchainAgent,
                                  toolset=list(newAgentTools),
                                  toolsetNames=requested_toolsets,
                                  name=agent_name)
        self._touch_subagent(subagentInstance, status="idle", last_error="")
        
        self.subagents[agent_name]=subagentInstance
        self._status_dirty = True
        log.info("Created subagent %s with toolsets=%s", agent_name, requested_toolsets)
        self._write_state_file()
        return self.get_status(agent_name)

    async def ainvokeSubagent(self,agent_name:str,message:dict,lockTimeout:int|None=0):
        """同步调用 Subagent（yield 事件流）。用于 _run_background。"""
        if agent_name not in self.subagents:
            raise ValueError(f"Not a valid subagent name: {agent_name}")
        
        subagent = self.subagents[agent_name]
        lock = subagent.lock

        if lockTimeout == 0:
            if lock.locked():
                raise RuntimeError(f"Agent '{agent_name}' is currently locked.")
            await lock.acquire()
        elif lockTimeout is not None:
            try:
                # 锁空闲时会立刻成功；被占用时按 timeout 超时并抛出异常
                await asyncio.wait_for(lock.acquire(), timeout=lockTimeout)
            except asyncio.TimeoutError:
                raise RuntimeError(f"Agent '{agent_name}' is currently locked.")
        else:
            await lock.acquire()
        
        self._touch_subagent(subagent, status="running", last_error="")
        config = {"configurable": {"thread_id": agent_name}, "recursion_limit": 300}
        subagent.outputStore.append([message])
        self._write_state_file()
        self._append_event(subagent, {"type": "input", "message": message})

        if self.abortEvents.get(agent_name) is not None:
            raise RuntimeError(f"Agent '{agent_name}' already has an active abort event.")
        abort_event = Event()
        self.abortEvents[agent_name] = abort_event

        # 如果 agent 是 None（例如从持久化恢复后），重新创建
        if subagent.agent is None:
            checkpointer = await self._ensure_checkpointer()
            subagent.agent = self._create_langchain_agent(
                tools=subagent.toolset,
                systemPrompt=subagent.systemPrompt,
                middlewares=self.middlewares,
                checkpointer=checkpointer,
            )
            log.info("Rebuilt agent for subagent '%s'", agent_name)

        try:
            async for event in self._ainvokeAgent(subagent.agent,config=config,payload=message,abortEvent=abort_event):
                yield event
                subagent.outputStore[-1].append(event)
                self._append_event(subagent, event)
                # 推送 Subagent 流式事件到队列供 WS 转发
                event_copy = dict(event)
                event_copy["agent_id"] = f"subagent-{agent_name}"
                try:
                    self.get_event_queue().put_nowait(event_copy)
                except asyncio.QueueFull:
                    pass
        finally:
            self.abortEvents.pop(agent_name, None)
            if subagent.status not in {"error", "stopped"}:
                self._touch_subagent(subagent, status="idle")
            subagent.activeTask = None
            if lock.locked():
                lock.release()
    
    async def _run_background(self, agent_name: str, message: dict, lockTimeout: int | None = None) -> None:
        subagent = self.subagents[agent_name]
        try:
            async for _event in self.ainvokeSubagent(agent_name, message, lockTimeout=lockTimeout):
                pass
        except asyncio.CancelledError:
            self._touch_subagent(subagent, status="stopped")
            self._append_event(subagent, {"type": "stopped"})
            raise
        except Exception as exc:
            self._touch_subagent(subagent, status="error", last_error=str(exc))
            self._append_event(subagent, {"type": "error", "content": str(exc)})
            log.warning("Subagent %s failed: %s", agent_name, exc)

    async def invokeSubagent(self, agent_name: str, message: dict, *, lockTimeout: int | None = None) -> dict[str, Any]:
        """异步调度 Subagent（不阻塞，返回后 Subagent 在后台运行）。"""
        if agent_name not in self.subagents:
            raise ValueError(f"Not a valid subagent name: {agent_name}")
        subagent = self.subagents[agent_name]
        if subagent.activeTask is not None and not subagent.activeTask.done():
            raise RuntimeError(f"Agent '{agent_name}' is currently locked.")
        self._touch_subagent(subagent, status="pending", last_error="")
        self._append_event(subagent, {"type": "queued", "message": message})
        # 用空白 contextvars 上下文创建任务，切断 LangChain 父 run 回调继承，
        # 否则 subagent 事件会冒泡进主 Agent 的 astream_events 流并被误标为 agent_id=main
        task = asyncio.get_running_loop().create_task(
            self._run_background(agent_name, message, lockTimeout=lockTimeout),
            context=contextvars.Context(),
        )
        subagent.activeTask = task
        return self._light_status(agent_name)

    async def abortSubagent(self,agent_name):
        """通过 asyncio.Event 发送中止信号。返回是否成功发出。"""
        abort_event = self.abortEvents.get(agent_name)
        if abort_event is None:
            return False
        subagent = self.subagents.get(agent_name)
        if subagent is not None:
            self._touch_subagent(subagent, status="stopping")
            self._append_event(subagent, {"type": "stopping"})
        abort_event.set()
        return True

    async def removeSubagent(self, agent_name: str) -> bool:
        """停止并删除 Subagent。等待 2 秒后强制 cancel。"""
        subagent = self.subagents.get(agent_name)
        if subagent is None:
            return False
        await self.abortSubagent(agent_name)
        task = subagent.activeTask
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except Exception:
                    pass
            except Exception:
                pass
        self.subagents.pop(agent_name, None)
        self.abortEvents.pop(agent_name, None)
        self._status_dirty = True
        self._write_state_file()
        return True

    def _build_final_result_tool(self, agent_name: str) -> StructuredTool:
        async def _set_final_result(result: str) -> str:
            subagent = self.subagents.get(agent_name)
            if subagent is None:
                raise RuntimeError(f"Subagent not found: {agent_name}")
            subagent.finalResult = str(result or "").strip()
            self._append_event(subagent, {"type": "final_result", "content": subagent.finalResult})
            self._write_state_file()
            return "final result recorded"

        _set_final_result.__doc__ = "设置当前 Subagent 的最终结论与总结。完成任务后始终调用一次。"
        return StructuredTool.from_function(
            coroutine=_set_final_result,
            name="finalResultTool",
            description="设置当前 Subagent 的最终结论与总结。完成任务后始终调用一次。",
            return_direct=False,
        )

    def getOutputStore(self,agent_name:str):
        """获取 Subagent 的输出存储（按 run 分组的事件列表）。"""
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
        """Async Invoke A langchain Agent and trun its event stram output to dict.

        Args:
            agent: Langchain Agent
            config (dict): config dict
            payload (dict): messageCon

        Yields:
            dict: _description_
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
                additional_kwargs = chunk.additional_kwargs or {}
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

if __name__ == "__main__":
    import asyncio

    async def main():
        manager = SubagentManager(checkpointerPath="subagents.db")
        manager.setChatModel(BaseChatModel())  # Replace with actual model
        manager.newToolset("Example Toolset", tools=[])  # Add actual tools
        await manager.newSubagent(agent_name="TestAgent", toolsetsNames=["Example Toolset"])
        status = manager.get_status("TestAgent")
        print(status)

    asyncio.run(main())