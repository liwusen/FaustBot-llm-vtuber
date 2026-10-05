"""Agent Communicate 插件入口：把外部 ACP 编码 Agent 接进 FaustBot 的 VFS。

不新增任何 Agent 工具：全部能力经 ``read`` / ``write`` / ``edit`` + ``faustbot://agents/``。

规格：docs/agent-communicate-spec.md（实施计划 docs/superpowers/plans/2026-10-05-agent-communicate.md）。
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

_PLUGIN_DIR = str(Path(__file__).resolve().parent)
if _PLUGIN_DIR not in sys.path:
    # 同仓库 desktop-mood / agile-engine 的自举惯例：不这样，同目录模块 import 不到
    sys.path.insert(0, _PLUGIN_DIR)

try:
    import acp  # noqa: F401  仅做依赖可见性检查
except ImportError as exc:  # pragma: no cover - 依赖缺失必须显式失败
    raise RuntimeError(
        "agent-communicate 需要 ACP 官方 SDK：请先安装 agent-client-protocol==0.12.1"
        "（requirements.txt 已声明）。插件不会降级为'功能静默不可用'。"
    ) from exc

from faust_backend.logger import get_logger
from faust_backend.plugin_system import FaustPlugin, PluginContext, hookimpl

from agents import AgentRegistry, AgentRuntime, AgentSettings, build_settings
from tasks import EnvelopeError, TaskStore
from vfs_surface import VfsSurface

log = get_logger("faust.plugins.agent-communicate")

IDLE_TICK_SEC = 30.0
SSE_INTERVAL_SEC = 0.5
SSE_TAIL_LIMIT = 4000

# 内置 opencode 预设：加载时把 command[0] 解析为绝对路径；
# 首次真实握手成功后，运行时展示的 description 会被 agentInfo.name + version 覆盖（不写回配置）
AGENTS_DEFAULT: list[dict[str, Any]] = [
    {
        "name": "opencode",
        "command": ["opencode", "acp"],
        "cwd": "",
        "env": {},
        "enabled": True,
        "description": "本机 opencode CLI（ACP 模式，自动探测时命令会解析为绝对路径）",
        "handshake_timeout_sec": 30,
        "session_timeout_sec": 60,
        "task_timeout_sec": 1800,
        "permission_timeout_sec": 300,
        "permission_default": "deny",
    }
]

CONFIG_SCHEMA: list[dict[str, Any]] = [
    {
        "key": "agents",
        "type": "json",
        "label": "外部 Agent 定义",
        "description": "数组，元素：name / command(str[]) / cwd / env / enabled / description / 三级超时 / permission_default",
        "default": AGENTS_DEFAULT,
    },
    {"key": "auto_start", "type": "bool", "label": "加载时预热进程", "default": False},
    {"key": "inherit_env", "type": "bool", "label": "继承完整环境变量（关闭会打掉凭据）", "default": True},
    {"key": "env_extra", "type": "json", "label": "追加环境变量", "default": {}},
    {"key": "idle_ttl_sec", "type": "int", "label": "空闲多久关进程（秒，sessionId 留存）", "default": 900},
    {"key": "max_queue", "type": "int", "label": "每 Agent 队列上限", "default": 5},
    {"key": "task_timeout_sec", "type": "int", "label": "默认任务硬超时（秒）", "default": 1800},
    {"key": "permission_timeout_sec", "type": "int", "label": "权限等待上限（秒）", "default": 300},
    {"key": "permission_default", "type": "str", "label": "超时默认动作（deny / allow）", "default": "deny"},
    {"key": "record_thoughts", "type": "bool", "label": "把思考记入事件流", "default": True},
    {"key": "notify_default", "type": "str", "label": "信封缺省 notify（batched/normal/none）", "default": "batched"},
    {"key": "restore_tasks", "type": "int", "label": "重载后恢复的任务节点数", "default": 20},
    {"key": "discover_enabled", "type": "bool", "label": "自动探测外部 Agent", "default": True},
    {"key": "discover_cache_ttl_sec", "type": "int", "label": "探测结果缓存 TTL（秒）", "default": 3600},
]

PROMPT_SUFFIX = (
    "\n[Agent 通信]\n"
    "需要把编码任务外包给外部 Agent（如 opencode）时，读 skill://agent-communicate/SKILL.md，"
    "并按其中的流程用 faustbot://agents/ 提交任务；外部 Agent 的权限请求会以触发器唤醒你，"
    "你需要在 faustbot://agents/{name}/permissions/{id}.md 里裁决（唤醒不会打断当前 turn）。"
)


class Plugin(FaustPlugin):
    def __init__(self) -> None:
        self.ctx: PluginContext | None = None
        self.registry: AgentRegistry | None = None
        self.store: TaskStore | None = None
        self.surface: VfsSurface | None = None
        self._idle_task: asyncio.Task | None = None
        self._last_discover_refresh = 0.0

    # ── 生命周期 ──

    async def startup(self, ctx: PluginContext) -> None:
        """挂载点；热重载会反复调用，因此必须幂等。"""
        self.ctx = ctx
        await ctx.register_config(CONFIG_SCHEMA)  # type: ignore[arg-type]
        config = dict(await ctx.list_configs() or {})
        settings = build_settings(config)

        registry = AgentRegistry(settings, runtime_factory=self._make_runtime)
        await registry.refresh_discovery(cache=ctx.storage)

        store = TaskStore(
            data_dir=Path(ctx.plugin_data_dir or (ctx.plugin_dir / "data")),
            registry=registry,
            on_permission_node=self._set_permission_node,
        )
        surface = VfsSurface(ctx, registry, store)
        self.registry = registry
        self.store = store
        self.surface = surface

        restored = store.restore(settings.restore_tasks)
        await surface.mount(restored=restored)
        log.info(
            "agent-communicate 已挂载：agents=%s，恢复任务=%d",
            ", ".join(registry.agent_names()) or "(无)",
            len(restored),
        )

        if settings.auto_start:
            for name in registry.agent_names():
                runtime = registry.get(name)
                if runtime is None:
                    continue
                try:
                    await runtime.ensure_initialized()
                except Exception as exc:  # noqa: BLE001 - 预热失败只记录，不阻塞加载
                    log.warning("预热 %s 失败: %s", name, exc)

        self._start_idle_loop()

    @hookimpl
    async def plugin_unloaded(self, ctx: PluginContext) -> None:
        del ctx
        if self._idle_task is not None:
            self._idle_task.cancel()
            await asyncio.gather(self._idle_task, return_exceptions=True)
            self._idle_task = None
        if self.store is not None:
            await self.store.shutdown()
        if self.registry is not None:
            await self.registry.stop_all()
        if self.surface is not None:
            await self.surface.unmount()
        log.info("agent-communicate 已卸载：进程与 VFS 节点已清理")

    def _start_idle_loop(self) -> None:
        if self._idle_task is not None and not self._idle_task.done():
            self._idle_task.cancel()
        self._idle_task = asyncio.create_task(self._idle_loop(), name="agent-communicate-idle")

    async def _idle_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(IDLE_TICK_SEC)
                if self.registry is None:
                    continue
                stopped = await self.registry.stop_idle()
                for name in stopped:
                    log.info("%s: 空闲超时，已关闭进程（sessionId 保留）", name)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("空闲回收循环异常: %s", exc)

    @hookimpl
    async def heartbeat(self, ctx: PluginContext) -> None:
        if self.registry is None or self.store is None:
            return
        try:
            await self.registry.stop_idle()
        except Exception as exc:  # noqa: BLE001
            log.debug("心跳空闲回收失败: %s", exc)
        ttl = max(0, int(self.registry.settings.discover_cache_ttl_sec))
        if ttl and (time.time() - self._last_discover_refresh) > ttl:
            self._last_discover_refresh = time.time()
            try:
                await self.registry.refresh_discovery(cache=ctx.storage)
            except Exception as exc:  # noqa: BLE001
                log.debug("心跳探测刷新失败: %s", exc)

    def _make_runtime(self, settings: AgentSettings) -> AgentRuntime:
        assert self.registry is not None
        registry_settings = self.registry.settings
        store = self.store
        name = settings.name
        return AgentRuntime(
            settings,
            inherit_env=registry_settings.inherit_env,
            env_extra=registry_settings.env_extra,
            on_session_update=(store.make_session_update_handler() if store is not None else None),
            # 回调按 Agent 绑定，避免"按 sessionId 猜是哪个 Agent"这种不确定归属
            on_permission=(
                (lambda session_id, tool_call, options: self._on_permission_request(
                    name, session_id, tool_call, options
                ))
                if store is not None
                else None
            ),
            on_unhandled=(
                (lambda method, params: store.note_unhandled(name, method, params))
                if store is not None
                else None
            ),
        )

    async def _on_permission_request(
        self, agent: str, session_id: str, tool_call: Any, options: list
    ) -> Any:
        store = self.store
        if store is None:
            raise RuntimeError("TaskStore 尚未初始化")
        return await store.on_permission_request(agent, session_id, tool_call, list(options))

    async def _set_permission_node(self, agent: str, request_id: str, present: bool) -> None:
        if self.surface is None:
            return
        await self.surface.mount_permission_node(agent, request_id, present=present)

    # ── 配置变更 ──

    @hookimpl
    async def config_changed(self, key: str, old: Any, new: Any, ctx: PluginContext) -> None:
        del old, ctx
        if self.registry is None or self.surface is None or self.store is None:
            return
        if key == "agents":
            try:
                payload = json.loads(new) if isinstance(new, str) and new.strip() else new
            except json.JSONDecodeError:
                log.warning("agents 配置不是合法 JSON，忽略本次变更: %s", str(new)[:120])
                return
            await self._apply_agents_change(payload or [])
            return
        if key == "discover_enabled":
            await self.registry.refresh_discovery(cache=self.ctx.storage if self.ctx else None)
            await self._sync_agent_nodes()
            return
        if key in ("discover_cache_ttl_sec",):
            return
        if key in ("inherit_env", "env_extra", "idle_ttl_sec", "max_queue", "task_timeout_sec",
                   "permission_timeout_sec", "permission_default", "record_thoughts", "notify_default"):
            await self._rebuild_settings()
            return

    async def _rebuild_settings(self) -> None:
        """配置值变了但 Agent 集合不变：就地替换 settings（保留已建运行时）。"""
        if self.ctx is None or self.registry is None or self.store is None:
            return
        config = dict(await self.ctx.list_configs() or {})
        self.registry.update_settings(build_settings(config))
        await self._sync_agent_nodes()

    async def _apply_agents_change(self, raw_agents: Any) -> None:
        """agents 变更后：关闭被移除/改动的 Agent 进程并清理其 VFS 子树。"""
        if self.ctx is None or self.registry is None or self.store is None or self.surface is None:
            return
        if not isinstance(raw_agents, list):
            log.warning("agents 配置必须是数组，忽略本次变更")
            return
        config = dict(await self.ctx.list_configs() or {})
        config["agents"] = raw_agents
        previous = {item.name: item for item in self.registry.resolve_agents()}
        self.registry.update_settings(build_settings(config))
        current = {item.name: item for item in self.registry.resolve_agents()}

        for name in list(previous):
            if name in current and current[name] == previous[name]:
                continue
            runtime = self.registry.drop_runtime(name)
            if runtime is not None:
                await runtime.stop()
            await self.surface.drop_agent_nodes(name)
            log.info("agents 配置变更：%s 的进程与节点已清理", name)
        await self._sync_agent_nodes()

    async def _sync_agent_nodes(self) -> None:
        if self.registry is None or self.surface is None:
            return
        for name in self.registry.agent_names():
            await self.surface.mount_agent_nodes(name)

    # ── Prompt ──

    @hookimpl
    def register_prompt_suffix(self) -> list[str]:
        return [PROMPT_SUFFIX]

    # ── 前端 ──

    @hookimpl
    def register_frontend(self) -> list[dict]:
        return [
            {"type": "css", "path": "/faust/plugins/agent-communicate/frontend/panel-v2.css"},
            {"type": "js", "path": "/faust/plugins/agent-communicate/frontend/panel-v2.js"},
        ]

    @hookimpl
    async def communicate_handler(self, payload: dict, ctx: PluginContext) -> dict | None:
        del ctx
        action = str((payload or {}).get("action") or "").strip()
        try:
            return await self._handle_action(action, payload or {})
        except EnvelopeError as exc:
            return {"status": "error", "detail": str(exc)}
        except Exception as exc:  # noqa: BLE001 - 前端要看到诚实错误，而不是空白
            log.exception("communicate 动作 %s 失败", action)
            return {"status": "error", "detail": f"{type(exc).__name__}: {exc}"}

    async def _handle_action(self, action: str, payload: dict) -> dict:
        registry, store, surface = self.registry, self.store, self.surface
        if registry is None or store is None or surface is None:
            return {"status": "error", "detail": "插件尚未完成 startup"}

        if action == "get_state":
            return {"status": "ok", **self._state_snapshot()}

        if action in ("agent_start", "agent_stop"):
            name = str(payload.get("name") or "")
            runtime = registry.get(name)
            if runtime is None:
                return {"status": "error", "detail": f"未注册的 Agent `{name}`"}
            if action == "agent_start":
                try:
                    await runtime.ensure_initialized()
                except Exception as exc:  # noqa: BLE001
                    return {"status": "error", "detail": str(exc)}
                return {"status": "ok", "detail": f"{name} 已就绪（pid={runtime.bridge.pid}）"}
            await runtime.stop()
            return {"status": "ok", "detail": f"{name} 进程已关闭（sessionId 保留）"}

        if action in ("session_new", "session_close"):
            name = str(payload.get("name") or "")
            runtime = registry.get(name)
            if runtime is None:
                return {"status": "error", "detail": f"未注册的 Agent `{name}`"}
            if action == "session_new":
                session_id = await runtime.ensure_session(mode="new")
                return {"status": "ok", "session_id": session_id, "detail": f"新会话 {session_id}"}
            closed = await runtime.close_session()
            return {
                "status": "ok",
                "detail": f"{name} 会话{'已关闭' if closed else '本就不存在'}",
            }

        if action == "task_submit":
            name = str(payload.get("name") or "")
            envelope = payload.get("envelope")
            if isinstance(envelope, (dict, list)):
                envelope = json.dumps(envelope, ensure_ascii=False)
            ack = await surface.submit(name, envelope if envelope is not None else "")
            return {"status": "ok", "task_id": surface.last_task_id(name), "detail": ack}

        if action == "task_cancel":
            name = str(payload.get("name") or "")
            ok, detail = await store.cancel(name, str(payload.get("task_id") or "") or None)
            return {"status": "ok" if ok else "error", "detail": detail}

        if action == "permission_answer":
            name = str(payload.get("name") or "")
            detail = store.answer_permission_direct(
                name,
                str(payload.get("request_id") or ""),
                str(payload.get("outcome") or "deny"),
                str(payload.get("scope") or "once"),
            )
            return {"status": "ok", "detail": detail}

        if action == "agents_save":
            agents = payload.get("agents")
            if not isinstance(agents, list):
                return {"status": "error", "detail": "agents 必须是数组"}
            cleaned: list[dict[str, Any]] = []
            for item in agents:
                if not isinstance(item, dict):
                    continue
                command = item.get("command")
                if isinstance(command, str):
                    command = command.split()
                cleaned.append(
                    {
                        "name": str(item.get("name") or "").strip(),
                        "command": [str(part) for part in (command or []) if str(part).strip()],
                        "cwd": str(item.get("cwd") or ""),
                        "env": item.get("env") if isinstance(item.get("env"), dict) else {},
                        "enabled": bool(item.get("enabled", True)),
                        "description": str(item.get("description") or ""),
                        "handshake_timeout_sec": item.get("handshake_timeout_sec") or 30,
                        "session_timeout_sec": item.get("session_timeout_sec") or 60,
                        "task_timeout_sec": item.get("task_timeout_sec") or 1800,
                        "permission_timeout_sec": item.get("permission_timeout_sec") or 300,
                        "permission_default": str(item.get("permission_default") or "deny"),
                    }
                )
            if any(not item["name"] for item in cleaned):
                return {"status": "error", "detail": "每个 Agent 都必须有 name"}
            if self.ctx is None:
                return {"status": "error", "detail": "插件上下文不可用"}
            await self.ctx.set_config("agents", json.dumps(cleaned, ensure_ascii=False))
            await self._apply_agents_change(cleaned)
            return {"status": "ok", "detail": f"已保存 {len(cleaned)} 个 Agent 定义"}

        if action == "discover_refresh":
            rows = await registry.refresh_discovery(
                force=True, cache=self.ctx.storage if self.ctx else None
            )
            await self._sync_agent_nodes()
            return {"status": "ok", "discover": rows}

        return {"status": "error", "detail": f"未知 action: {action or '(空)'}"}

    def _state_snapshot(self) -> dict[str, Any]:
        assert self.registry is not None and self.store is not None
        registry, store = self.registry, self.store
        agents: list[dict[str, Any]] = []
        for name in registry.agent_names():
            runtime = registry.get(name)
            info: dict[str, Any] = {
                "name": name,
                "enabled": True,
                "description": "",
                "phase": "stopped",
                "running": False,
                "session_id": None,
                "cwd": "",
                "current_task": None,
                "queue_depth": store.queue_depth(name),
                "last_error": None,
                "available_commands": [],
                "config_options": [],
                "tasks": [],
                "pending_permissions": [],
            }
            if runtime is not None:
                status = runtime.status()
                info.update(
                    {
                        "enabled": status["enabled"],
                        "description": status["description"],
                        "phase": status["phase"],
                        "running": status["running"],
                        "session_id": status["session_id"],
                        "cwd": status["cwd"],
                        "last_error": status["last_error"],
                        "available_commands": status["available_commands"],
                        "config_options": status["config_options"],
                    }
                )
            current = store.current_task(name)
            info["current_task"] = (
                {"id": current.id, "status": current.status, "elapsed_sec": current.elapsed_sec}
                if current
                else None
            )
            info["tasks"] = [
                {
                    "id": task.id,
                    "status": task.status,
                    "status_label": task.status_label(),
                    "prompt": task.prompt_summary(80),
                    "elapsed_sec": task.elapsed_sec,
                    "created_at": task.created_at,
                    "queue_depth": task.queue_depth,
                    "result_preview": (task.result_text or task.error or "")[:200],
                }
                for task in reversed(store.tasks_of(name))
            ]
            info["pending_permissions"] = [
                {
                    "request_id": item.request_id,
                    "task_id": item.task_id,
                    "tool_title": item.tool_title,
                    "summary": item.summary,
                    "created_at": item.created_at,
                    "deadline_ts": item.deadline_ts,
                    "timeout_sec": item.timeout_sec,
                    "remaining_sec": item.remaining_sec,
                }
                for item in store.pending_permissions(name)
            ]
            agents.append(info)
        return {
            "agents": agents,
            "discover": registry.discover_rows(),
            "config": {
                "max_queue": store.max_queue,
                "notify_default": store.notify_default,
                "record_thoughts": store.record_thoughts,
                "permission_default": registry.settings.permission_default,
                "permission_timeout_sec": registry.settings.permission_timeout_sec,
                "task_timeout_sec": registry.settings.task_timeout_sec,
                "idle_ttl_sec": registry.settings.idle_ttl_sec,
                "inherit_env": registry.settings.inherit_env,
                "discover_enabled": registry.settings.discover_enabled,
                # 显式配置（未与自动发现合并），供前端表格编辑
                "agents": [item.to_dict() for item in registry.settings.agents],
            },
        }

    @hookimpl
    async def sse_communicate_handler(self, params: dict, ctx: PluginContext) -> Any:
        """推送当前任务输出直到终态（每条 data 为 {state, output_tail, event_tail}，末尾 {done: true}）。"""
        del ctx
        name = str((params or {}).get("name") or "")
        task_id = str((params or {}).get("task_id") or "")
        while True:
            snapshot = await self._sse_payload(name, task_id)
            yield f"data: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
            if snapshot.get("done"):
                # 规格 §14.4：终态后再单独推一条 {done: true} 并结束
                yield 'data: {"done": true}\n\n'
                return
            await asyncio.sleep(SSE_INTERVAL_SEC)

    async def _sse_payload(self, name: str, task_id: str) -> dict[str, Any]:
        if self.store is None:
            return {"state": {"error": "插件尚未完成 startup"}, "output_tail": "", "event_tail": "", "done": True}
        task = self.store.get(task_id) if task_id else self.store.current_task(name)
        if task is None:
            return {
                "state": {"name": name, "task_id": task_id, "status": None},
                "output_tail": "",
                "event_tail": "",
                "done": True,
            }
        payload = {
            "state": {
                "name": task.agent,
                "task_id": task.id,
                "status": task.status,
                "status_label": task.status_label(),
                "elapsed_sec": task.elapsed_sec,
                "error": task.error,
            },
            "output_tail": task.output_text[-SSE_TAIL_LIMIT:],
            "event_tail": task.events_text[-SSE_TAIL_LIMIT:],
        }
        payload["done"] = task.is_terminal
        return payload


def get_plugin() -> Plugin:
    return Plugin()


__all__ = ["CONFIG_SCHEMA", "Plugin", "get_plugin"]
