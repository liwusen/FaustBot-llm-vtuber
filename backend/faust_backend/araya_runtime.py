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
        self._active_run_task: asyncio.Task | None = None
        self._run_lock = asyncio.Lock()
        self._last_main_activity_ts = time.time()
        self._target_agent_name = self._resolve_target_agent_name()
        self.paths = self._build_paths()

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
        current = str(getattr(conf, "AGENT_NAME", "faust") or "faust").strip()
        if not current or current.lower() == ARAYA_AGENT_NAME:
            return "faust"
        return current

    def refresh_target_agent(self) -> str:
        self._target_agent_name = self._resolve_target_agent_name()
        return self._target_agent_name

    def mark_main_agent_activity(self) -> float:
        self._last_main_activity_ts = time.time()
        state = self._load_state()
        state["last_main_activity_ts"] = self._last_main_activity_ts
        self._save_state(state)
        return self._last_main_activity_ts

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
                "idle_minutes": float(getattr(conf, "ARAYA_IDLE_MINUTES", 30) or 30),
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
        data.setdefault("idle_minutes", float(getattr(conf, "ARAYA_IDLE_MINUTES", 30) or 30))
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
                    normalized[key] = [str(getattr(item, "content", item)) for item in value[-6:]]
                else:
                    normalized[str(key)] = self._normalize_response(value)
            return normalized
        if isinstance(response, (list, tuple)):
            return [self._normalize_response(item) for item in response]
        return str(response)

    def get_status(self) -> dict[str, Any]:
        state = self._load_state()
        state["target_agent"] = self.refresh_target_agent()
        state["running"] = bool(self._task and not self._task.done())
        state["run_in_progress"] = bool(self._active_run_task and not self._active_run_task.done())
        state["enabled_by_config"] = bool(getattr(conf, "ARAYA_ENABLED", True))
        state["idle_seconds"] = max(0.0, time.time() - float(state.get("last_main_activity_ts") or time.time()))
        if self.paths.last_log_file.exists():
            try:
                with self.paths.last_log_file.open("r", encoding="utf-8") as f:
                    state["last_log"] = json.load(f)
            except Exception:
                state["last_log"] = None
        else:
            state["last_log"] = None
        self._save_state(state)
        return state

    async def startup(self) -> None:
        self.refresh_target_agent()
        self._save_state(self._load_state())
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="araya-runtime")

    async def shutdown(self) -> None:
        if self._active_run_task is not None:
            self._active_run_task.cancel()
            try:
                await self._active_run_task
            except asyncio.CancelledError:
                pass
            self._active_run_task = None
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(30.0)
                if not self.should_trigger():
                    continue
                await self.run_once(reason="idle")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                state = self._load_state()
                state["last_run_status"] = "error"
                state["last_error"] = str(exc)
                self._save_state(state)

    def should_trigger(self) -> bool:
        if not bool(getattr(conf, "ARAYA_ENABLED", True)):
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

    def update_settings(self, *, enabled: bool | None = None, idle_minutes: float | None = None) -> dict[str, Any]:
        state = self._load_state()
        if enabled is not None:
            state["enabled"] = bool(enabled)
        if idle_minutes is not None:
            state["idle_minutes"] = max(1.0, float(idle_minutes))
        self._save_state(state)
        return self.get_status()

    def _build_tools(self):
        manager = kb_manager.get_kb_manager(refresh=True)
        @tool
        def arayaGetTimeTool() -> dict:
            """获取当前时间戳和 ISO 格式的 UTC 时间字符串。"""
            return {"time": time.time(), "time_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        @tool
        def arayaKbListTool(scope: str = "") -> dict:
            """列出知识库中某个目录范围下的树结构。"""
            try:
                print(f"[Araya]arayaKbListTool called with scope: {scope}")
                return manager.list_tree(scope)
            except Exception as e:
                print(f"[Araya]Error in arayaKbListTool: {e}")
                return {}

        @tool
        def arayaKbReadTool(path: str) -> dict:
            """读取知识库中文件节点的完整内容。"""
            try:
                print(f"[Araya]arayaKbReadTool called with path: {path}")
                return manager.read_node(path)
            except Exception as e:
                print(f"[Araya]Error in arayaKbReadTool: {e}")
                return {}

        @tool
        async def arayaKbWriteTool(path: str, content: str, declared_by: str = "araya", index: bool = True, tags: list[str] | None = None) -> dict:
            """写入知识库文件节点，并可附加标签。"""
            try:
                print(f"[Araya]arayaKbWriteTool called with path: {path}, content length: {len(content)}, declared_by: {declared_by}, index: {index}, tags: {tags}")
                return await manager.write_node(path, content, declared_by=declared_by, index=index, tags=tags or [])
            except Exception as e:
                print(f"[Araya]Error in arayaKbWriteTool: {e}")
                return {}

        @tool
        async def arayaKbSearchTool(query: str, scope: str = "", top_k: int = 8, return_mode: str = "snippets", tags: list[str] | None = None, ignore_score_patch: bool = False) -> list[dict]:
            """在知识库指定范围内搜索，可按标签过滤。"""
            try:
                print(f"[Araya]arayaKbSearchTool called with query: {query}, scope: {scope}, top_k: {top_k}, return_mode: {return_mode}, tags: {tags}, ignore_score_patch: {ignore_score_patch}")
                return await manager.search(query=query, scope=scope, top_k=int(top_k), return_mode=return_mode, tags=tags or [], ignore_score_patch=ignore_score_patch)
            except Exception as e:
                print(f"[Araya]Error in arayaKbSearchTool: {e}")
                return []

        @tool
        async def arayaKbTagSetTool(path: str, tags: list[str], managed_by: str = "araya") -> dict:
            """为知识库文档设置标签。"""
            try:
                print(f"[Araya]arayaKbTagSetTool called with path: {path}, tags: {tags}, managed_by: {managed_by}")
                return await manager.set_tags(path, tags or [], managed_by=managed_by)
            except Exception as e:
                print(f"[Araya]Error in arayaKbTagSetTool: {e}")
                return {"success": False, "error": str(e)}

        @tool
        async def arayaKbScorePatchTool(path: str, score_patch: float, managed_by: str = "araya") -> dict:
            """为知识库文档设置 score patch。"""
            
            print(f"[Araya]arayaKbScorePatchTool called with path: {path}, score_patch: {score_patch}, managed_by: {managed_by}")
            try:
                return await manager.set_score_patch(path, score_patch, managed_by=managed_by)
            except Exception as e:
                print(f"[Araya]Error in arayaKbScorePatchTool: {e}")
                return {"success": False, "error": str(e)}
        @tool
        def arayaKbChangedNodesTool(since_ts: float, scope: str = "", tags: list[str] | None = None) -> list[dict]:
            """获取自某个时间戳以来发生变更的知识库节点。"""
            print(f"[Araya]arayaKbChangedNodesTool called with since_ts: {since_ts}, scope: {scope}, tags: {tags}")
            
            try:
                return manager.get_changed_nodes(since_ts, scope=scope, tags=tags or [])
            except Exception as e:
                print(f"[Araya]Error in arayaKbChangedNodesTool: {e}")
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

    def trigger_run(self, reason: str = "manual") -> dict[str, Any]:
        state = self._load_state()
        if self._active_run_task is not None and not self._active_run_task.done():
            return {
                "accepted": False,
                "reason": str(reason or "manual"),
                "status": "already_running",
                "target_agent": self.refresh_target_agent(),
                "last_trigger_ts": float(state.get("last_trigger_ts") or 0.0),
            }
        self._active_run_task = asyncio.create_task(self.run_once(reason=reason), name="araya-manual-run")
        return {
            "accepted": True,
            "reason": str(reason or "manual"),
            "status": "queued",
            "target_agent": self.refresh_target_agent(),
            "queued_at": time.time(),
        }

    async def run_once(self, reason: str = "manual") -> dict[str, Any]:
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
            }
            try:
                chat_model = ChatOpenAI(
                    model=conf.CHAT_MODEL,
                    api_key=conf.CHAT_API_KEY,
                    base_url=conf.CHAT_API_BASE,
                )
                araya_agent = create_agent(
                    model=chat_model,
                    tools=self._build_tools(),
                )
                instruction = (
                    f"{prompt}\n\n"
                    f"当前维护目标 Agent: {self._target_agent_name}\n"
                    f"本次触发原因: {reason}\n"
                    f"请先读取 records/ 和 diary/ 下与最近变更相关的内容，再检查自上次触发以来的变更节点。\n"
                    f"changed-nodes 的 since_ts 使用 {previous_trigger_ts}。\n"
                    f"必要时请维护 /auto_index.md，并且仅使用当前可用工具完成知识库维护。\n"
                    f"调用工具时，必须严格使用工具参数的原生 JSON 结构，不要把 JSON 对象再编码成字符串。"
                )
                response = await araya_agent.ainvoke({"messages": [{"role": "user", "content": instruction}]})
                print(f"[Araya]Araya agent response: {response}")
                result_payload["response"] = self._normalize_response(response)
                result_payload["status"] = "ok"
                state["last_error"] = ""
            except Exception as exc:
                result_payload["status"] = "error"
                result_payload["error"] = str(exc)
                state["last_error"] = str(exc)
            finished_at = time.time()
            result_payload["finished_at"] = finished_at
            result_payload["finished_at_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(finished_at))
            result_payload["duration_seconds"] = round(finished_at - started_at, 3)
            state["last_trigger_ts"] = finished_at
            state["last_run_status"] = result_payload["status"]
            state["target_agent"] = self._target_agent_name
            self._save_state(state)
            self._write_run_log(result_payload)
            self._active_run_task = None
            return result_payload


_ARAYA_RUNTIME: ArayaRuntime | None = None


def get_araya_runtime(refresh: bool = False) -> ArayaRuntime:
    global _ARAYA_RUNTIME
    if _ARAYA_RUNTIME is None:
        _ARAYA_RUNTIME = ArayaRuntime()
    if refresh:
        _ARAYA_RUNTIME.refresh_target_agent()
    return _ARAYA_RUNTIME