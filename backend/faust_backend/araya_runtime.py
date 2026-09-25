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
from faust_backend.logger import get_logger
from faust_backend.runtime.model_retry import with_model_retry

import traceback

log = get_logger("faust.araya")

ARAYA_AGENT_NAME = "araya"
@dataclass
class ArayaPaths:
    root: Path
    state_file: Path
    last_log_file: Path
    history_log_file: Path
    trace_file: Path


class ArayaRuntime:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None
        self._run_lock: asyncio.Lock | None = None
        self._last_main_activity_ts = time.time()
        self._target_agent_name = self._resolve_target_agent_name()
        # 本轮 run 的目标 Agent 快照（run 期间不变，工具的记忆库绑定取自它）
        self._run_target_agent: str | None = None
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
            trace_file=Path(conf.DATA_ROOT) / "araya_last_trace.json",
        )

    def _resolve_target_agent_name(self) -> str:
        current = str(conf.AGENT_NAME or "faust").strip()
        if not current or current.lower() == ARAYA_AGENT_NAME:
            return "faust"
        return current

    def _run_in_progress(self) -> bool:
        return bool(self._run_lock is not None and self._run_lock.locked())

    def refresh_target_agent(self) -> str:
        """重解析维护目标 Agent；run 进行中保持本轮快照，不中途改目标。

        前端每次请求都会带 `refresh=True` 调到这里，若运行中改写
        `_target_agent_name`，本轮的工具绑定、prompt 与日志就会指向不同 Agent。
        """
        if self._run_in_progress():
            return self.run_target_agent
        self._target_agent_name = self._resolve_target_agent_name()
        return self._target_agent_name

    @property
    def run_target_agent(self) -> str:
        """本轮 run 的目标 Agent（无 run 时即当前维护目标）。

        以 `_run_lock` 是否持锁判断「run 进行中」：run 被提前关闭（SSE 断连 →
        `aclose()`）时锁同样释放，快照随之失效，不会把旧目标粘住。
        """
        if self._run_in_progress():
            return self._run_target_agent or self._target_agent_name
        return self._target_agent_name

    @staticmethod
    def maintenance_watermark(state: dict[str, Any]) -> float:
        """changed-nodes 的窗口起点：上次成功维护的结束时间（无则 0）。"""
        return float(state.get("last_maintenance_ts") or 0.0)

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
        for name in ("AGENT.md", "ROLE.md", "COREMEMORY.md"):
            path = agent_root / name
            if path.exists():
                parts.append(path.read_text(encoding="utf-8"))
        if not parts:
            raise FileNotFoundError("Araya prompt files are missing")
        return "\n".join(parts)

    def _sync_templates(self) -> None:
        try:
            from faust_backend.admin_runtime import sync_araya_template_files
            result = sync_araya_template_files()
            updated = [k for k, v in result.items() if v]
            if updated:
                log.info("Araya 模板文件已同步: %s", ", ".join(updated))
        except Exception as e:
            log.warning("Araya 模板同步失败: %s", e)

    def _load_state(self) -> dict[str, Any]:
        if not self.paths.state_file.exists():
            return {
                "enabled": True,
                "idle_minutes": float(conf.ARAYA_IDLE_MINUTES or 30),
                "last_main_activity_ts": self._last_main_activity_ts,
                "last_trigger_ts": 0.0,
                "last_maintenance_ts": 0.0,
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
        # 老 state 没有维护水印：用上次触发时间兜底，否则升级后第一轮会把全库
        # 当成增量（1300+ 节点）灌进上下文。
        data.setdefault("last_maintenance_ts", float(data.get("last_trigger_ts") or 0.0))
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

    def _write_last_trace(self, payload: dict[str, Any]) -> None:
        self.paths.trace_file.parent.mkdir(parents=True, exist_ok=True)
        with self.paths.trace_file.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def get_last_trace(self) -> dict[str, Any] | None:
        if not self.paths.trace_file.exists():
            return None
        try:
            with self.paths.trace_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

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
        state["last_trace_file"] = str(self.paths.trace_file)
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
        self._sync_templates()
        self._save_state(self._load_state())
        await self._init_agent()
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
                await asyncio.sleep(5)
                await self._trigger_idle_run()
            except Exception as exc:
                log.error("_loop 异常: %s", exc)
                state = self._load_state()
                state["last_run_status"] = "error"
                state["last_error"] = str(exc)
                self._save_state(state)

    async def _trigger_idle_run(self) -> float | None:
        """按空闲策略触发一轮维护，返回本轮窗口起点（未触发则 None）。

        窗口起点必须在抢占 `last_trigger_ts` 之前取：抢占会把 last_trigger_ts 写成
        "现在"，而它同时被 prompt 当作 changed-nodes 的 since_ts（旧实现因此每轮
        窗口恒为 0 秒、changed-nodes 永远返回空）。
        """
        if not self.should_trigger():
            return None
        # 已有 run 正在执行时不重复触发（避免 5 秒轮询叠起多个并发 run）
        if self._run_lock is not None and self._run_lock.locked():
            return None
        log.info("Araya loop decided to trigger a run based on idle time")
        state = self._load_state()
        window_start_ts = self.maintenance_watermark(state)
        # 抢占 last_trigger_ts：立即标记"已触发"，防止下次轮询(5秒后)再次命中
        state["last_trigger_ts"] = time.time()
        self._save_state(state)
        asyncio.create_task(self.run_once_async(reason="idle", since_ts=window_start_ts))
        return window_start_ts

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
        return self.get_status()

    def _build_tools(self):

        def _m():
            from faust_backend.memory import get_memory
            # 必须按本轮维护目标取库：全局 `get_memory()` 跟随前端激活 Agent，
            # 运行中切换激活 Agent 会让维护读写落到别的 Agent 的记忆库
            # （09-22 事故：faust 一轮维护把索引/日志/5 个实体写进了 ishmael 库）。
            return get_memory(self.run_target_agent)

        @tool
        def arayaGetTimeTool() -> dict:
            """获取当前时间、上次触发时间，以及 changed-nodes 的窗口起点（上次成功维护结束时间）。"""
            state = self._load_state()
            last_trigger_ts = float(state.get("last_trigger_ts") or 0.0)
            window_start_ts = self.maintenance_watermark(state)
            log.info("arayaGetTimeTool called, last_trigger_ts=%s window_start=%s",
                     last_trigger_ts, window_start_ts)
            return {
                "time": time.time(),
                "time_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "last_trigger_ts": last_trigger_ts,
                "last_trigger_time_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(last_trigger_ts)),
                "window_start_ts": window_start_ts,
                "window_start_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(window_start_ts)),
            }

        # ── tree / file tools ──

        @tool
        async def arayaListTreeTool(scope: str = "") -> dict:
            """列出记忆库中某个目录范围下的树结构。"""
            try:
                log.info("arayaListTreeTool called with scope: %s", scope)
                return await _m().tree_list(scope)
            except Exception as e:
                log.error("Error in arayaListTreeTool: %s", e)
                return {}

        @tool
        async def arayaReadFileTool(path: str) -> dict:
            """读取记忆库中文件节点的完整内容。"""
            try:
                log.info("arayaReadFileTool called with path: %s", path)
                return await _m().file_read(path)
            except FileNotFoundError:
                return {"path": path, "error": "not_found"}
            except Exception as e:
                log.error("Error in arayaReadFileTool: %s", e)
                return {}

        @tool
        async def arayaWriteFileTool(path: str, content: str, declared_by: str = "araya",
                                     index: bool = True, tags: list[str] | None = None,
                                     description: str = "") -> dict:
            """写入记忆库文件节点，并可附加标签和摘要描述。"""
            try:
                log.info("arayaWriteFileTool called with path: %s, content length: %s", path, len(content))
                return await _m().file_write(path, content, description=description,
                                              declared_by=declared_by, index=index, tags=tags or [])
            except Exception as e:
                log.error("Error in arayaWriteFileTool: %s", e)
                return {}

        @tool
        async def arayaDeleteFileTool(path: str) -> dict:
            """删除记忆库中的文件节点。"""
            try:
                log.info("arayaDeleteFileTool called with path: %s", path)
                return await _m().file_delete(path)
            except FileNotFoundError:
                return {"path": path, "error": "not_found"}
            except Exception as e:
                log.error("Error in arayaDeleteFileTool: %s", e)
                return {}

        # ── search ──

        @tool
        async def arayaSearchMemoryTool(query: str, scope: str = "", top_k: int = 8, return_mode: str = "snippets", tags: list[str] | None = None) -> list[dict]:
            """在记忆库指定范围内搜索，可按标签过滤。组合向量检索与图谱联想。"""
            try:
                log.info("arayaSearchMemoryTool called with query: %s", query)
                return await _m().search(query=query, scope=scope, top_k=int(top_k), return_mode=return_mode, tags=tags or [], use_graph=True)
            except Exception as e:
                log.error("Error in arayaSearchMemoryTool: %s", e)
                return []

        # ── tags / score_patch ──

        @tool
        async def arayaSetTagsTool(path: str, tags: list[str]) -> dict:
            """为记忆库文档设置标签。"""
            try:
                log.info("arayaSetTagsTool called with path: %s", path)
                return await _m().set_tags(path, tags or [])
            except Exception as e:
                log.error("Error in arayaSetTagsTool: %s", e)
                return {"success": False, "error": str(e)}

        @tool
        async def arayaSetScorePatchTool(path: str, score_patch: float) -> dict:
            """为记忆库文档设置 score patch（重要性权重），范围 -0.15 到 +0.15。"""
            log.info("arayaSetScorePatchTool called with path: %s", path)
            try:
                return await _m().set_score_patch(path, score_patch)
            except Exception as e:
                log.error("Error in arayaSetScorePatchTool: %s", e)
                return {"success": False, "error": str(e)}

        @tool
        async def arayaChangedNodesTool(since_ts: float | None = None, scope: str = "",
                                        tags: list[str] | None = None,
                                        include_entities: bool = False,
                                        limit: int = 300) -> dict:
            """获取自窗口起点以来被写入过的记忆节点，按更新时间倒序。

            since_ts 省略时用运行时窗口起点（上次成功维护结束时间），正常巡检直接省略。
            limit 为返回条数上限；truncated 为真说明还有更早的变更未返回。
            include_entities=True 时一并列出变更的实体节点（默认只看文件/目录）。
            """
            state = self._load_state()
            window_start = (float(since_ts) if since_ts is not None
                            else self.maintenance_watermark(state))
            log.info("arayaChangedNodesTool called since_ts=%s（生效窗口起点 %s）scope=%s tags=%s",
                     since_ts, window_start, scope, tags or [])
            try:
                # 多取一条用来精确判断是否被截断
                items = await _m().get_changed_nodes(window_start, scope=scope, tags=tags or [],
                                                     include_entities=include_entities,
                                                     limit=int(limit) + 1)
            except Exception as e:
                log.error("Error in arayaChangedNodesTool: %s", e)
                return {"since_ts": window_start, "count": 0, "truncated": False,
                        "items": [], "error": str(e)}
            return {
                "since_ts": window_start,
                "since_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(window_start)),
                "now": time.time(),
                "count": min(len(items), int(limit)),
                "truncated": len(items) > int(limit),
                "items": items[:int(limit)],
            }

        # ── graph / entity tools ──

        @tool
        def arayaSearchEntityTool(query: str, type_filter: str = "", top_k: int = 20) -> list[dict]:
            """在知识图谱中搜索实体节点（按名称模糊匹配）。"""
            try:
                results = _m().entity_search(query, type_filter=type_filter or None, top_k=int(top_k))
                return results
            except Exception as e:
                log.error("Error in arayaSearchEntityTool: %s", e)
                return []

        @tool
        def arayaListEntitiesTool() -> list[dict]:
            """列出知识图谱中所有实体节点。"""
            try:
                return _m().entity_iter()
            except Exception as e:
                log.error("Error in arayaListEntitiesTool: %s", e)
                return []

        @tool
        def arayaGetNeighborsTool(entity_id: str, depth: int = 1) -> list[dict]:
            """获取知识图谱中某个实体 depth 跳内的邻居节点（含每条边的类型与方向）。

            entity_id 必须是实体 ID（ent_…），不是名字：先用 arayaSearchEntityTool 取 ID。
            """
            try:
                return _m().get_neighbors(entity_id, depth=int(depth))
            except Exception as e:
                log.error("Error in arayaGetNeighborsTool: %s", e)
                return []

        @tool
        def arayaAddEntityTool(name: str, entity_type: str = "custom",
                                properties_json: str = "{}", kb_refs_json: str = "[]",
                                description: str = "") -> str:
            """向知识图谱中添加一个实体节点。description 为实体的自然语言描述。
            kb_refs_json 传来源文件路径（如 ["/records/2026-09-18.md"]），会同时建立「文件→实体」的 from 边。
            返回实体 ID。"""
            try:
                from faust_backend.memory.store import _path_id
                properties = json.loads(properties_json) if str(properties_json or "").strip() else {}
                kb_refs = json.loads(kb_refs_json) if str(kb_refs_json or "").strip() else []
                m = _m()
                eid = m.entity_add(name, entity_type, description=description,
                                   properties=properties, kb_refs=kb_refs)
                for ref in kb_refs:
                    ref_nid = _path_id(str(ref))
                    if m._has_node(ref_nid):
                        m._add_edge(ref_nid, eid, "from")
                return str(eid)
            except Exception as e:
                log.error("Error in arayaAddEntityTool: %s", e)
                return f"error: {e}"

        @tool
        def arayaDeleteEntityTool(entity_id: str) -> bool:
            """从知识图谱中删除一个实体节点。"""
            try:
                return _m().entity_delete(entity_id)
            except Exception as e:
                log.error("Error in arayaDeleteEntityTool: %s", e)
                return False

        @tool
        def arayaAddRelationTool(source_id: str, target_id: str, rel_type: str = "relates_to") -> str:
            """在知识图谱中在两个实体之间添加一条有向关系边。返回关系 key。"""
            try:
                key = _m().relation_add(source_id, target_id, rel_type)
                return str(key)
            except Exception as e:
                log.error("Error in arayaAddRelationTool: %s", e)
                return f"error: {e}"

        @tool
        def arayaRemoveRelationTool(source_id: str, target_id: str) -> bool:
            """从知识图谱中移除两个实体之间的一条有向关系边。"""
            try:
                _m().relation_remove(source_id, target_id)
                return True
            except Exception as e:
                log.error("Error in arayaRemoveRelationTool: %s", e)
                return False

        @tool
        def arayaMergeEntTool(keep_id: str, absorb_id: str) -> dict:
            """合并两个重复实体：保留 keep_id，把 absorb_id 并入后删除。

            属性/描述/kb_refs 会合并到保留实体；被吸收实体的全部关系边改指到保留实体，
            重复边与自环自动丢弃。先用 arayaSearchEntityTool 确认两个 ID 是同一实体。
            """
            try:
                log.info("arayaMergeEntTool keep=%s absorb=%s", keep_id, absorb_id)
                return _m().entity_merge(keep_id, absorb_id)
            except Exception as e:
                log.error("Error in arayaMergeEntTool: %s", e)
                return {"ok": False, "error": str(e)}

        @tool
        def arayaListRelationsTool() -> list[dict]:
            """列出知识图谱中所有关系边。"""
            try:
                return _m().relation_iter()
            except Exception as e:
                log.error("Error in arayaListRelationsTool: %s", e)
                return []

        @tool
        async def arayaFileEditTool(path: str, old_str: str, new_str: str) -> dict|str:
            """
            Replace an exact text snippet in a memory document with new content.

            Reads the document, finds ALL occurrences of old_str, and replaces it
            with new_str ONLY if old_str matches exactly once.

            MATCH RULES (strict):
            - old_str must match the document content EXACTLY, character for
              character: including indentation, trailing spaces, and comments.
            - If old_str appears MORE THAN ONCE, the edit FAILS. Include more
              surrounding lines in old_str to make it unique.
            - To create a new document, use arayaFileWriteTool instead.

            Args:
                path: The path of the document to edit in the memory store.
                old_str: Exact text to replace. Must be unique in the document.
                new_str: Replacement text (empty string deletes old_str).
            """
            try:
                ret = await _m().file_read(path)
                original = ret["content"]
                desc = ret.get("description", "")
                meta = ret.get("meta", {}) or {}
                tags = meta.get("tags", []) or []
            except FileNotFoundError:
                log.info("arayaFileEditTool OUTPUT 文件不存在: %s", path)
                return f"文件不存在: {path}"
            from faust_backend.tools._patch_utils import replace_exact
            if old_str == "":
                return (
                    "edit: old_str 不能为空（空串会匹配整个文档）。\n"
                    "处理: 新建文档用 arayaFileWriteTool；在文档末尾追加时，"
                    "old_str 应包含文档末尾的锚点行（先读取确认最后一行）。"
                )
            if old_str == new_str:
                return "edit: old_str 与 new_str 完全相同，本次编辑无任何变更，未写入。"
            result, match_count = replace_exact(original, old_str, new_str)
            if result is None:
                if match_count == 0:
                    _msg = (
                        f"edit: old_str 在 memory://{path} 中匹配 0 处，文档未被修改。\n"
                        f"处理: 1) 重新读取 memory://{path}，从输出中逐字复制 old_str；"
                        "2) 检查缩进、行尾空格、全角/半角字符是否一致。"
                    )
                    log.info("arayaFileEditTool OUTPUT %s", _msg[:120])
                    return _msg
                _msg = (
                    f"edit: old_str 在 memory://{path} 中匹配 {match_count} 处"
                    f"（要求恰好 1 处），文档未被修改。\n"
                    f"处理: 在 old_str 前后多包含几行上下文使其唯一；"
                    f"若确实要替换所有 {match_count} 处相同片段，"
                    f"请改为逐处编辑（每处 old_str 带不同上下文）。"
                )
                log.info("arayaFileEditTool OUTPUT %s", _msg[:120])
                return _msg
            await _m().file_write(path, result, declared_by="araya", description=desc, tags=tags)
            _msg = f"已编辑 memory://{path} (1 处替换)"
            log.info("arayaFileEditTool OUTPUT %s", _msg[:120])
            return _msg

        @tool
        async def arayaAttachmentReadTool(path: str) -> dict:
            """从记忆库读取图片，返回 multimodal 格式以便查看图片内容。"""
            try:
                log.info("arayaAttachmentReadTool path=%s", path)
                result = await _m().attachment_read(path)
                return {
                    "kind": "multimodal_tool_result",
                    "text": result.get("description", ""),
                    "images": [{
                        "url": f"data:{result['content_type']};base64,{result['content_base64']}"
                    }],
                }
            except Exception as e:
                log.error("Error in arayaAttachmentReadTool: %s", e)
                return {"status": "error", "error": str(e)}

        return [
            arayaGetTimeTool,
            arayaListTreeTool,
            arayaReadFileTool,
            arayaWriteFileTool,
            arayaDeleteFileTool,
            arayaSearchMemoryTool,
            arayaSetTagsTool,
            arayaSetScorePatchTool,
            arayaChangedNodesTool,
            arayaSearchEntityTool,
            arayaListEntitiesTool,
            arayaGetNeighborsTool,
            arayaAddEntityTool,
            arayaDeleteEntityTool,
            arayaMergeEntTool,
            arayaAddRelationTool,
            arayaRemoveRelationTool,
            arayaListRelationsTool,
            arayaAttachmentReadTool,
            arayaFileEditTool,
        ]

    async def _init_agent(self) -> None:
        from faust_backend.runtime import state as runtime_state
        from faust_backend.provider import build_main_chat_model
        from faust_backend.runtime.middleware import wrap_tools
        from faust_backend.runtime.mm_bridge import MultimodalBridgeMiddleware
        self._chat_model = await build_main_chat_model(
            runtime_state.get_model_providers(), intensity=None
        )
        log.info("Creating Araya agent with model: %s", self._chat_model.model_name)
        self._agent = create_agent(
            model=self._chat_model,
            # 与主 Agent 同一套输出管线：工具输出进 OutputStore，图片经桥接注入
            tools=wrap_tools(self._build_tools()),
            middleware=with_model_retry([MultimodalBridgeMiddleware()]),
        )

    async def _close_model(self) -> None:
        if self._chat_model is None:
            return
        try:
            async_client = getattr(self._chat_model, 'async_client', None)
            if async_client is not None:
                # async_client 是 openai 的 AsyncCompletions 资源对象，
                # 底层真实客户端在 _client（AsyncOpenAI）上
                openai_client = getattr(async_client, '_client', None)
                if openai_client is not None and not openai_client.is_closed():
                    await openai_client.close()
            client = getattr(self._chat_model, 'client', None)
            if client is not None:
                openai_sync = getattr(client, '_client', None)
                if openai_sync is not None and not openai_sync.is_closed():
                    openai_sync.close()
            # 置 None 防止 GC 时 openai SDK 的 __del__ 对已关闭的 AsyncClient
            # 再次 create_task(aclose())，导致 "Event loop is closed" 警告
            self._chat_model = None
        except Exception:
            pass

    async def trigger_run(self, reason: str = "manual") -> dict[str, Any]:
        """Legacy trigger - schedules a background task. Prefer stream_once_async for SSE."""
        state = self._load_state()
        # _run_lock 可能在懒初始化前被调用（如测试直接触发），用 getattr 防御而非断言
        run_lock = getattr(self, "_run_lock", None)
        if run_lock is not None and run_lock.locked():
            return {"accepted": False, "reason": str(reason or "manual"), "status": "already_running", "target_agent": self.refresh_target_agent(), "last_trigger_ts": float(state.get("last_trigger_ts") or 0.0)}
        log.info("Triggering Araya async run with reason: %s", reason)
        asyncio.create_task(self.run_once_async(reason, since_ts=self.maintenance_watermark(state)))
        return {"accepted": True, "reason": str(reason or "manual"), "status": "queued", "target_agent": self.refresh_target_agent(), "queued_at": time.time()}

    async def run_once_async(self, reason: str = "manual", since_ts: float | None = None) -> dict[str, Any]:
        """Legacy non-streaming run. Prefer stream_once_async for SSE."""
        result = None
        async for event in self.stream_once_async(reason, since_ts=since_ts):
            if event.get("event") == "done":
                result = json.loads(event.get("data", "{}"))
            elif event.get("event") == "error":
                data = json.loads(event.get("data", "{}"))
                result = {"status": "error", "error": data.get("error", "unknown")}
        return result or {"status": "error", "error": "no result"}

    def run_once(self, reason: str = "manual", since_ts: float | None = None) -> dict[str, Any]:
        return asyncio.run(self.run_once_async(reason, since_ts=since_ts))

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

    async def stream_once_async(self, reason: str = "manual", since_ts: float | None = None):
        """Async generator yielding SSE events during agent execution.

        Each yield: {"event": "step|done|error", "data": "<json_string>"}
        Call this directly from the SSE endpoint — no create_task.

        `since_ts` 是 changed-nodes 的窗口起点；省略时取上次成功维护的结束时间。
        """
        if self._run_lock is None:
            self._run_lock = asyncio.Lock()

        if self._run_lock.locked():
            yield {"event": "error", "data": json.dumps({"message": "Araya is already running"})}
            return

        async with self._run_lock:
            # 目标 Agent 在本轮开始时快照：run 期间即使用户切换激活 Agent，
            # 工具的记忆库绑定、prompt、日志都保持同一目标（`run_target_agent`）。
            target_agent = self._resolve_target_agent_name()
            self._target_agent_name = target_agent
            self._run_target_agent = target_agent
            started_at = time.time()
            state = self._load_state()
            window_start_ts = (float(since_ts) if since_ts is not None
                               else self.maintenance_watermark(state))
            prompt = self._load_prompt()

            result_payload: dict[str, Any] = {
                "reason": str(reason or "manual"),
                "started_at": started_at,
                "started_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_at)),
                "target_agent": target_agent,
                "since_ts": window_start_ts,
                "status": "running",
                "error": "",
                "response": "",
            }
            instruction = (
                f"{prompt}\n\n"
                f"当前维护目标 Agent: {target_agent}\n"
                f"本次触发原因: {reason}\n"
                f"请先读取 records/ 和 diary/ 下与最近变更相关的内容，再检查自上次维护以来的变更节点。\n"
                f"changed-nodes 的窗口起点（since_ts）是 {window_start_ts}；"
                f"省略 since_ts 时工具会自动使用该窗口起点，无需自己换算。\n"
                f"必要时请维护 /auto_index.md，并对 knowledge graph 中的实体和关系进行整合/修剪。\n"
                f"调用工具时，必须严格使用工具参数的原生 JSON 结构，不要把 JSON 对象再编码成字符串。"
                f"每个修改过的文件只需要完整处理一次，处理完成后绝对不要重复处理。\n"
            )
            trace_payload: dict[str, Any] = {
                "conversation_id": f"araya-{int(started_at * 1000)}",
                "reason": str(reason or "manual"),
                "target_agent": target_agent,
                "started_at": started_at,
                "started_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_at)),
                "status": "running",
                "error": "",
                "messages": [{"role": "user", "content": instruction}],
                "tool_calls": [],
            }
            tool_call_seq = 0
            in_flight_calls: dict[str, dict[str, Any]] = {}


            yield {"event": "step", "data": json.dumps({"type": "start", "reason": reason, "target_agent": target_agent})}

            full_response = ""
            run_error = ""
            try:
                if self._agent is None:
                    await self._init_agent()
                agt = self._agent
                payload = {"messages": [{"role": "user", "content": instruction}]}
                config = {"configurable": {"thread_id": int(time.time())}, "recursion_limit": 500}

                yield {"event": "step", "data": json.dumps({"type": "llm_start"})}

                assert agt is not None
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
                            if trace_payload.get("messages") and trace_payload["messages"][-1].get("role") == "assistant":
                                trace_payload["messages"][-1]["content"] += delta
                            else:
                                trace_payload["messages"].append({"role": "assistant", "content": delta})
                            yield {"event": "step", "data": json.dumps({"type": "llm_chunk", "content": delta})}

                    elif event_name == "on_tool_start":
                        tool_name = str(raw_event.get("name") or data.get("name") or "tool").strip()
                        tool_args = data.get("input")
                        tool_call_seq += 1
                        call_id = f"call_{tool_call_seq}"
                        in_flight_calls[call_id] = {
                            "id": call_id,
                            "tool": tool_name,
                            "args": tool_args,
                            "started_at": time.time(),
                        }
                        trace_payload["tool_calls"].append({
                            "id": call_id,
                            "tool": tool_name,
                            "args": tool_args,
                            "result": None,
                            "duration_seconds": None,
                        })
                        trace_payload["messages"].append({"role": "tool", "tool_name": tool_name, "call_id": call_id, "args": tool_args})
                        yield {"event": "step", "data": json.dumps({"type": "tool_start", "tool": tool_name, "args": tool_args, "call_id": call_id})}

                    elif event_name == "on_tool_end":
                        tool_name = str(raw_event.get("name") or data.get("name") or "tool").strip()
                        call_id = next((cid for cid, item in reversed(list(in_flight_calls.items())) if item.get("tool") == tool_name), "")
                        result_value = data.get("output")
                        duration_seconds = None
                        if call_id and call_id in in_flight_calls:
                            started = float(in_flight_calls[call_id].get("started_at") or time.time())
                            duration_seconds = round(time.time() - started, 3)
                            del in_flight_calls[call_id]
                        for item in trace_payload["tool_calls"]:
                            if item.get("id") == call_id:
                                item["result"] = self._normalize_response(result_value)
                                item["duration_seconds"] = duration_seconds
                                break
                        trace_payload["messages"].append({
                            "role": "tool_result",
                            "tool_name": tool_name,
                            "call_id": call_id,
                            "result": self._normalize_response(result_value),
                            "duration_seconds": duration_seconds,
                        })
                        yield {"event": "step", "data": json.dumps({
                            "type": "tool_end",
                            "tool": tool_name,
                            "call_id": call_id,
                            "result": self._normalize_response(result_value),
                            "duration": duration_seconds,
                        })}

                result_payload["response"] = full_response or "(no text response)"
                result_payload["status"] = "ok"
                trace_payload["status"] = "ok"
                state["last_error"] = ""

            except Exception as exc:
                log.error("stream_once_async 错误: %s", exc)
                log.debug("Traceback:\n%s", traceback.format_exc())
                run_error = str(exc)
                result_payload["status"] = "error"
                result_payload["error"] = run_error
                trace_payload["status"] = "error"
                trace_payload["error"] = run_error
                state["last_error"] = run_error

            finished_at = time.time()
            result_payload["finished_at"] = finished_at
            result_payload["finished_at_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(finished_at))
            result_payload["duration_seconds"] = round(finished_at - started_at, 3)
            trace_payload["finished_at"] = finished_at
            trace_payload["finished_at_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(finished_at))
            trace_payload["duration_seconds"] = round(finished_at - started_at, 3)

            state["last_trigger_ts"] = finished_at
            # 只有成功的轮次才推进 changed-nodes 水印：失败的轮次（模型/工具异常、
            # 0.4s 就崩）什么都没维护，若推进水印会把这段窗口永久跳过。
            if result_payload["status"] == "ok":
                state["last_maintenance_ts"] = finished_at
            state["last_run_status"] = result_payload["status"]
            state["target_agent"] = target_agent
            self._save_state(state)
            self._write_run_log(result_payload)
            self._write_last_trace(trace_payload)

            if run_error:
                yield {"event": "error", "data": json.dumps({"error": run_error, "duration": result_payload["duration_seconds"], "target_agent": target_agent})}
            else:
                yield {"event": "done", "data": json.dumps({
                    "status": "ok",
                    "response": full_response,
                    "duration": result_payload["duration_seconds"],
                    "target_agent": target_agent,
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