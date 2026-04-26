"""
阿赖耶没有在等待任何人!
References:
https://www.bilibili.com/video/BV1VqdLBzEkN"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI

import faust_backend.config_loader as conf
import faust_backend.kb_manager as kb_manager
from faust_backend.logger import get_logger

import traceback

log = get_logger("faust.araya")

ARAYA_AGENT_NAME = "araya"
@dataclass
class ArayaPaths:
    root: Path
    state_file: Path
    last_log_file: Path
    history_log_file: Path


class ArayaRuntime:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None
        self._run_lock: asyncio.Lock | None = None
        self._last_main_activity_ts = time.time()
        self._target_agent_name = self._resolve_target_agent_name()
        self.paths = self._build_paths()
        self._chat_model: ChatOpenAI | None = None
        self._agent: Any = None

    def _build_paths(self) -> ArayaPaths:
        root = Path(conf.CONFIG_ROOT) / "agents" / ARAYA_AGENT_NAME / "runtime"
        root.mkdir(parents=True, exist_ok=True)
        return ArayaPaths(
            root=root,
            state_file=root / "state.json",
            last_log_file=root / "last_run.json",
            history_log_file=root / "runs.jsonl",
        )

    def _resolve_target_agent_name(self) -> str:
        current = str(conf.AGENT_NAME or "faust").strip()
        if not current or current.lower() == ARAYA_AGENT_NAME:
            return "faust"
        return current

    def refresh_target_agent(self) -> str:
        self._target_agent_name = self._resolve_target_agent_name()
        return self._target_agent_name

    def mark_main_agent_activity(self) -> float:
        now = time.time()
        self._last_main_activity_ts = now
        state = self._load_state()
        # ensure we persist the freshly recorded timestamp (don't rely on _load_state to preserve it)
        state["last_main_activity_ts"] = now
        self._save_state(state)
        return now

    def _load_prompt(self) -> str:
        agent_root = Path(conf.CONFIG_ROOT) / "agents" / ARAYA_AGENT_NAME
        parts: list[str] = []
        for name in ("AGENT.md", "ROLE.md", "COREMEMORY.md", "TASK.md"):
            path = agent_root / name
            if path.exists():
                parts.append(path.read_text(encoding="utf-8"))
        if not parts:
            raise FileNotFoundError("Araya prompt files are missing")
        return "\n".join(parts)

    def _load_state(self) -> dict[str, Any]:
        if not self.paths.state_file.exists():
            return {
                "enabled": True,
                "idle_minutes": float(conf.ARAYA_IDLE_MINUTES or 30),
                "last_main_activity_ts": self._last_main_activity_ts,
                "last_trigger_ts": 0.0,
                "last_run_status": "idle",
                "last_error": "",
                "target_agent": self._target_agent_name,
            }
        try:
            with self.paths.state_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("enabled", True)
        data.setdefault("idle_minutes", float(conf.ARAYA_IDLE_MINUTES or 30))
        data.setdefault("last_main_activity_ts", self._last_main_activity_ts)
        data.setdefault("last_trigger_ts", 0.0)
        data.setdefault("last_run_status", "idle")
        data.setdefault("last_error", "")
        data.setdefault("target_agent", self._target_agent_name)
        self._last_main_activity_ts = float(data.get("last_main_activity_ts") or time.time())
        return data

    def _save_state(self, state: dict[str, Any]) -> None:
        self.paths.root.mkdir(parents=True, exist_ok=True)
        with self.paths.state_file.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def _write_run_log(self, payload: dict[str, Any]) -> None:
        self.paths.root.mkdir(parents=True, exist_ok=True)
        with self.paths.last_log_file.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        with self.paths.history_log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _normalize_response(self, response: Any) -> Any:
        if isinstance(response, (str, int, float, bool)) or response is None:
            return response
        if isinstance(response, dict):
            normalized: dict[str, Any] = {}
            for key, value in response.items():
                if key == "messages" and isinstance(value, list):
                    normalized[key] = [str(item.content if hasattr(item, "content") else item) for item in value[-6:]]
                else:
                    normalized[str(key)] = self._normalize_response(value)
            return normalized
        if isinstance(response, (list, tuple)):
            return [self._normalize_response(item) for item in response]
        return str(response)

    def get_status(self) -> dict[str, Any]:
        state = self._load_state()
        state["target_agent"] = self.refresh_target_agent()
        state["running"] = bool(self._task and (not getattr(self._task, "done", lambda: False)()))
        state["run_in_progress"] = bool(getattr(self._run_lock, "locked", lambda: False)())
        state["enabled_by_config"] = bool(conf.ARAYA_ENABLED)
        state["idle_seconds"] = max(0.0, time.time() - float(state.get("last_main_activity_ts") or time.time()))
        if self.paths.last_log_file.exists():
            try:
                with self.paths.last_log_file.open("r", encoding="utf-8") as f:
                    state["last_log"] = json.load(f)
            except Exception:
                state["last_log"] = None
        else:
            state["last_log"] = None
        # add human-readable timestamps for frontend
        try:
            lm_ts = float(state.get("last_main_activity_ts") or 0.0)
            state["last_main_activity_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(lm_ts)) if lm_ts > 0 else "-"
        except Exception:
            state["last_main_activity_at"] = "-"
        try:
            if self.paths.state_file.exists():
                mtime = self.paths.state_file.stat().st_mtime
                state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))
            else:
                state["updated_at"] = "-"
        except Exception:
            state["updated_at"] = "-"

        return state

    async def startup(self) -> None:
        self.refresh_target_agent()
        self._save_state(self._load_state())
        self._init_agent()
        if self._task is None or (hasattr(self._task, "done") and self._task.done()):
            self._stop_event = asyncio.Event()
            self._task = asyncio.create_task(self._loop_async())
        log.info("ArayaRuntime startup complete")

    async def shutdown(self) -> None:
        log.debug("Shutting down ArayaRuntime...")
        if self._stop_event:
            self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except Exception:
                try:
                    self._task.cancel()
                except Exception:
                    pass
            self._task = None
        await self._close_model()

    async def _loop_async(self) -> None:
        while not (self._stop_event and self._stop_event.is_set()):
            try:
                await asyncio.sleep(30.0)
                log.debug("Araya loop tick: checking if should trigger...")
                should = self.should_trigger()
                if not should:
                    continue
                log.info("Araya loop decided to trigger a run based on idle time")
                asyncio.create_task(self.run_once_async(reason="idle"))
            except Exception as exc:
                log.error("_loop 异常: %s", exc)
                state = self._load_state()
                state["last_run_status"] = "error"
                state["last_error"] = str(exc)
                self._save_state(state)

    def should_trigger(self) -> bool:
        if not bool(conf.ARAYA_ENABLED):
            return False
        state = self._load_state()
        if not bool(state.get("enabled", True)):
            return False
        idle_seconds = time.time() - float(state.get("last_main_activity_ts") or time.time())
        threshold = max(60.0, float(state.get("idle_minutes") or 30.0) * 60.0)
        last_trigger_ts = float(state.get("last_trigger_ts") or 0.0)
        if idle_seconds < threshold:
            return False
        return last_trigger_ts < float(state.get("last_main_activity_ts") or 0.0)

    async def update_settings(self, *, enabled: bool | None = None, idle_minutes: float | None = None) -> dict[str, Any]:
        state = self._load_state()
        if enabled is not None:
            state["enabled"] = bool(enabled)
        if idle_minutes is not None:
            state["idle_minutes"] = max(1.0, float(idle_minutes))
        self._save_state(state)
        return await self.get_status()

    def _build_tools(self):
        manager = kb_manager.get_kb_manager(refresh=True)

        @tool
        def arayaGetTimeTool() -> dict:
            """获取当前时间戳和 ISO 格式的 UTC 时间字符串。"""
            return {"time": time.time(), "time_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

        @tool
        def arayaKbListTool(scope: str = "") -> dict:
            """列出知识库中某个目录范围下的树结构（同步）。"""
            try:
                log.info("arayaKbListTool called with scope: %s", scope)
                return manager.list_tree(scope)
            except Exception as e:
                log.error("Error in arayaKbListTool: %s", e)
                return {}

        @tool
        def arayaKbReadTool(path: str) -> dict:
            """读取知识库中文件节点的完整内容（同步）。"""
            try:
                log.info("arayaKbReadTool called with path: %s", path)
                return manager.read_node(path)
            except Exception as e:
                log.error("Error in arayaKbReadTool: %s", e)
                return {}

        @tool
        async def arayaKbWriteTool(path: str, content: str, declared_by: str = "araya", index: bool = True, tags: list[str] | None = None) -> dict:
            """写入知识库文件节点，并可附加标签。"""
            try:
                log.info("arayaKbWriteTool called with path: %s, content length: %s", path, len(content))
                return await manager.write_node(path, content, declared_by=declared_by, index=index, tags=tags or [])
            except Exception as e:
                log.error("Error in arayaKbWriteTool: %s", e)
                return {}

        @tool
        async def arayaKbSearchTool(query: str, scope: str = "", top_k: int = 8, return_mode: str = "snippets", tags: list[str] | None = None, ignore_score_patch: bool = False) -> list[dict]:
            """在知识库指定范围内搜索，可按标签过滤。"""
            try:
                log.info("arayaKbSearchTool called with query: %s", query)
                return await manager.search(query=query, scope=scope, top_k=int(top_k), return_mode=return_mode, tags=tags or [], ignore_score_patch=ignore_score_patch)
            except Exception as e:
                log.error("Error in arayaKbSearchTool: %s", e)
                return []

        @tool
        async def arayaKbTagSetTool(path: str, tags: list[str], managed_by: str = "araya") -> dict:
            """为知识库文档设置标签。"""
            try:
                log.info("arayaKbTagSetTool called with path: %s", path)
                return await manager.set_tags(path, tags or [], managed_by=managed_by)
            except Exception as e:
                log.error("Error in arayaKbTagSetTool: %s", e)
                return {"success": False, "error": str(e)}

        @tool
        async def arayaKbScorePatchTool(path: str, score_patch: float, managed_by: str = "araya") -> dict:
            """为知识库文档设置 score patch。"""
            log.info("arayaKbScorePatchTool called with path: %s", path)
            try:
                return await manager.set_score_patch(path, score_patch, managed_by=managed_by)
            except Exception as e:
                log.error("Error in arayaKbScorePatchTool: %s", e)
                return {"success": False, "error": str(e)}

        @tool
        def arayaKbChangedNodesTool(since_ts: float, scope: str = "", tags: list[str] | None = None) -> list[dict]:
            """获取自某个时间戳以来发生变更的知识库节点（同步）。"""
            log.info("arayaKbChangedNodesTool called")
            try:
                return manager.get_changed_nodes(since_ts, scope=scope, tags=tags or [])
            except Exception as e:
                log.error("Error in arayaKbChangedNodesTool: %s", e)
                return []

        return [
            arayaKbListTool,
            arayaKbReadTool,
            arayaKbWriteTool,
            arayaKbSearchTool,
            arayaKbTagSetTool,
            arayaKbScorePatchTool,
            arayaKbChangedNodesTool,
            arayaGetTimeTool,
        ]

    def _init_agent(self) -> None:
        self._chat_model = ChatOpenAI(
            model=conf.CHAT_MODEL,
            api_key=conf.CHAT_API_KEY,
            base_url=conf.CHAT_API_BASE,
            request_timeout=30,
            max_retries=1,
        )
        log.info("Creating Araya agent with model: %s", conf.CHAT_MODEL)
        self._agent = create_agent(
            model=self._chat_model,
            tools=self._build_tools(),
        )

    async def _close_model(self) -> None:
        if self._chat_model is None:
            return
        try:
            async_client = getattr(self._chat_model, 'async_client', None)
            if async_client is not None:
                await async_client.aclose()
            client = getattr(self._chat_model, 'client', None)
            if client is not None:
                client.close()
        except Exception:
            pass

    async def trigger_run(self, reason: str = "manual") -> dict[str, Any]:
        """Legacy trigger - schedules a background task. Prefer stream_once_async for SSE."""
        state = self._load_state()
        if getattr(self, "_run_lock", None) and self._run_lock.locked():
            return {"accepted": False, "reason": str(reason or "manual"), "status": "already_running", "target_agent": self.refresh_target_agent(), "last_trigger_ts": float(state.get("last_trigger_ts") or 0.0)}
        log.info("Triggering Araya async run with reason: %s", reason)
        asyncio.create_task(self.run_once_async(reason))
        return {"accepted": True, "reason": str(reason or "manual"), "status": "queued", "target_agent": self.refresh_target_agent(), "queued_at": time.time()}

    async def run_once_async(self, reason: str = "manual") -> dict[str, Any]:
        """Legacy non-streaming run. Prefer stream_once_async for SSE."""
        result = None
        async for event in self.stream_once_async(reason):
            if event.get("event") == "done":
                result = json.loads(event.get("data", "{}"))
            elif event.get("event") == "error":
                data = json.loads(event.get("data", "{}"))
                result = {"status": "error", "error": data.get("error", "unknown")}
        return result or {"status": "error", "error": "no result"}

    def run_once(self, reason: str = "manual") -> dict[str, Any]:
        return asyncio.run(self.run_once_async(reason))

    def _is_ai_message_chunk(self, message_chunk) -> bool:
        msg_type = str(getattr(message_chunk, "type", "")).strip().lower()
        if msg_type == "ai":
            return True
        cls_name = message_chunk.__class__.__name__.lower()
        return "aimessage" in cls_name

    def _message_content_to_text(self, content) -> str:
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

    async def stream_once_async(self, reason: str = "manual"):
        """Async generator yielding SSE events during agent execution.

        Each yield: {"event": "step|done|error", "data": "<json_string>"}
        Call this directly from the SSE endpoint — no create_task.
        """
        if self._run_lock is None:
            self._run_lock = asyncio.Lock()

        if self._run_lock.locked():
            yield {"event": "error", "data": json.dumps({"message": "Araya is already running"})}
            return

        async with self._run_lock:
            self.refresh_target_agent()
            started_at = time.time()
            state = self._load_state()
            previous_trigger_ts = float(state.get("last_trigger_ts") or 0.0)
            prompt = self._load_prompt()

            result_payload: dict[str, Any] = {
                "reason": str(reason or "manual"),
                "started_at": started_at,
                "started_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_at)),
                "target_agent": self._target_agent_name,
                "since_ts": previous_trigger_ts,
                "status": "running",
                "error": "",
                "response": "",
            }

            instruction = (
                f"{prompt}\n\n"
                f"当前维护目标 Agent: {self._target_agent_name}\n"
                f"本次触发原因: {reason}\n"
                f"请先读取 records/ 和 diary/ 下与最近变更相关的内容，再检查自上次触发以来的变更节点。\n"
                f"changed-nodes 的 since_ts 使用 {previous_trigger_ts}。\n"
                f"必要时请维护 /auto_index.md，并且仅使用当前可用工具完成知识库维护。\n"
                f"调用工具时，必须严格使用工具参数的原生 JSON 结构，不要把 JSON 对象再编码成字符串。"
            )

            yield {"event": "step", "data": json.dumps({"type": "start", "reason": reason, "target_agent": self._target_agent_name})}

            full_response = ""
            run_error = ""
            try:
                if self._agent is None:
                    self._init_agent()
                agt = self._agent
                payload = {"messages": [{"role": "user", "content": instruction}]}
                config = {"configurable": {"thread_id": int(time.time())}, "recursion_limit": 9999}

                yield {"event": "step", "data": json.dumps({"type": "llm_start"})}

                async for raw_event in agt.astream_events(payload, config=config, version="v2"):
                    if not isinstance(raw_event, dict):
                        continue
                    event_name = str(raw_event.get("event") or "").strip().lower()
                    data = raw_event.get("data") or {}

                    if event_name == "on_chat_model_stream":
                        chunk = data.get("chunk")
                        if not chunk or not self._is_ai_message_chunk(chunk):
                            continue
                        delta = self._message_content_to_text(chunk.content)
                        if delta:
                            full_response += delta
                            yield {"event": "step", "data": json.dumps({"type": "llm_chunk", "content": delta})}

                    elif event_name == "on_tool_start":
                        tool_name = str(raw_event.get("name") or data.get("name") or "tool").strip()
                        tool_args = data.get("input")
                        yield {"event": "step", "data": json.dumps({"type": "tool_start", "tool": tool_name, "args": tool_args})}

                    elif event_name == "on_tool_end":
                        tool_name = str(raw_event.get("name") or data.get("name") or "tool").strip()
                        yield {"event": "step", "data": json.dumps({"type": "tool_end", "tool": tool_name})}

                result_payload["response"] = full_response or "(no text response)"
                result_payload["status"] = "ok"
                state["last_error"] = ""

            except Exception as exc:
                log.error("stream_once_async 错误: %s", exc)
                log.debug("Traceback:\n%s", traceback.format_exc())
                run_error = str(exc)
                result_payload["status"] = "error"
                result_payload["error"] = run_error
                state["last_error"] = run_error

            finished_at = time.time()
            result_payload["finished_at"] = finished_at
            result_payload["finished_at_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(finished_at))
            result_payload["duration_seconds"] = round(finished_at - started_at, 3)

            state["last_trigger_ts"] = finished_at
            state["last_run_status"] = result_payload["status"]
            state["target_agent"] = self._target_agent_name
            self._save_state(state)
            self._write_run_log(result_payload)

            if run_error:
                yield {"event": "error", "data": json.dumps({"error": run_error, "duration": result_payload["duration_seconds"], "target_agent": self._target_agent_name})}
            else:
                yield {"event": "done", "data": json.dumps({
                    "status": "ok",
                    "response": full_response,
                    "duration": result_payload["duration_seconds"],
                    "target_agent": self._target_agent_name,
                    "reason": str(reason or "manual"),
                })}



_ARAYA_RUNTIME: ArayaRuntime | None = None


def get_araya_runtime(refresh: bool = False) -> ArayaRuntime:
    global _ARAYA_RUNTIME
    if _ARAYA_RUNTIME is None:
        _ARAYA_RUNTIME = ArayaRuntime()
    if refresh:
        _ARAYA_RUNTIME.refresh_target_agent()
    return _ARAYA_RUNTIME