"""agent-communicate 的 VFS 表面：节点挂载、渲染与 write handler。

规格依据：docs/agent-communicate-spec.md §10（VFS 接口）、§11（信封）、§16.2（不变量）。

两条硬不变量（实现时任何时候都不能破）：

1. **读不产生外部 I/O**：symbolic 内容函数只渲染内存快照，绝不发起 RPC / 子进程。
   （`sessions.md` 用的是后台刷新的缓存；`search` 会执行 symbolic 函数，所以除
   `index.md` / `tasks.md` / `result.md` / `/plugins/agent-communicate.md` 外，
   全部节点 ``should_be_included_in_search=False``。）
2. **插件卸载不留节点**：``unmount()`` 必须删干净本插件挂载的全部路径。
"""

from __future__ import annotations

import json
import time
from typing import Any

from faust_backend.logger import get_logger

from agents import AgentRegistry, render_discover_md
from tasks import (
    STATUS_LABELS,
    EnvelopeError,
    PermissionAnswerError,
    Task,
    TaskStore,
    parse_envelope,
)

log = get_logger("faust.plugins.agent-communicate.vfs")

AGENTS_ROOT = "/agents"
PLUGIN_DOC_PATH = "/plugins/agent-communicate.md"

# /agents 下的全局节点（不是 Agent 子树，陈旧清理时必须跳过）
_GLOBAL_AGENT_FILES = frozenset({"index.md", "discover.md"})

SEARCHABLE = {"/agents/index.md"}

ENVELOPE_SCHEMA = """提交信封（写入本节点即提交任务，内容忽略与否取决于是否为 JSON 对象）：

{
  "prompt": "必填，要外部 Agent 做的事",
  "cwd": "可选，任务工作目录（须存在）；缺省用该 Agent 的 cwd",
  "session": "可选，reuse(默认) | new | close | 具体 sessionId",
  "notify": "可选，batched(默认) | normal | none",
  "config": {"model": "…", "mode": "plan"},
  "timeout_sec": 1800
}

纯文本简写：直接写一句任务描述（非 JSON 对象的内容整体视为 prompt）。
未知字段会被忽略并在回执里提示；config 的合法 id 见本 Agent 的 config.json。"""


def _short(text: str, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


class VfsSurface:
    """挂载 /agents/ 下全部节点；渲染只读内存，写入走 store。"""

    def __init__(self, ctx: Any, registry: AgentRegistry, store: TaskStore) -> None:
        self.ctx = ctx
        self.registry = registry
        self.store = store
        self._mounted = False
        self._agent_nodes: dict[str, set[str]] = {}
        self._last_receipt: dict[str, str] = {}
        self._last_task_id: dict[str, str] = {}

    def last_task_id(self, agent: str) -> str | None:
        """最近一次通过本表面成功创建的 task id（供前端/调试精确定位）。"""
        return self._last_task_id.get(str(agent or ""))

    # ── 挂载 ──

    async def mount(self, *, restored: list[Task] | None = None) -> None:
        """幂等挂载（热重载会反复调用 startup）。"""
        await self._cleanup_stale_agents()
        await self._write_plugin_doc()
        await self._mount_global_nodes()
        for name in self.registry.agent_names():
            await self.mount_agent_nodes(name)
        for task in restored or []:
            await self.mount_task_nodes(task.agent, task.id)
        self._mounted = True

    async def _write_plugin_doc(self) -> None:
        text = "\n".join(
            [
                "# Agent Communicate（faustbot://agents/）",
                "",
                "通过 ACP 协议调用**外部编码 Agent**（opencode / omp 等），本插件不新增任何工具。",
                "",
                "## 怎么用",
                "",
                "1. 读 `faustbot://agents/index.md` 看有哪些 Agent、谁在忙。",
                "2. 读 `faustbot://agents/{name}/status.md` 看进程/会话/队列状态。",
                "3. 写 `faustbot://agents/{name}/submit` 提交任务（回执里有 task id）。",
                "4. 读 `faustbot://agents/{name}/tasks.md` 概览，`tasks/{id}/result.md` 取结果，",
                "   `tasks/{id}/events.md` 看思考/工具/权限过程。",
                "5. 外部 Agent 请求权限时会以触发器唤醒你，在",
                "   `faustbot://agents/{name}/permissions/{rid}.md` 写入裁决。",
                "",
                "详细流程见 skill://agent-communicate/SKILL.md。",
                "",
                "## 安全声明",
                "",
                "外部 Agent 是本机子进程，**拥有与 FaustBot 相同的用户权限**（可读写文件、执行命令）。",
                "本插件不声明 fs/terminal 能力，那只是不做透传与观测，**不是沙箱**。",
            ]
        )
        await self.ctx.vfs_write(PLUGIN_DOC_PATH, text, description="Agent 通信插件说明与用法入口")

    async def _mount_global_nodes(self) -> None:
        await self.ctx.vfs_write_symbolic(
            "/agents/index.md",
            lambda _path: self.render_index(),
            should_be_included_in_search=True,
            description="外部 Agent 索引（一行一 Agent）",
        )
        await self.ctx.vfs_write_symbolic(
            "/agents/discover.md",
            lambda _path: self.render_discover(),
            should_be_included_in_search=False,
            description="外部 Agent 探测结论与未发现原因",
        )

    async def _cleanup_stale_agents(self) -> None:
        """清掉不属于当前 Agent 集合的 /agents/* 子树（陈旧节点治理）。

        `index.md` / `discover.md` 是本插件的全局节点而不是 Agent 子树，必须跳过
        （否则每次热重载都会先删掉再重建，白白丢一次内容）。
        """
        names = set(self.registry.agent_names())
        existing = await self.ctx.vfs_list(AGENTS_ROOT) or []
        for name in existing:
            if name in names or name in _GLOBAL_AGENT_FILES:
                continue
            try:
                await self.ctx.vfs_delete(f"{AGENTS_ROOT}/{name}")
                log.info("清理陈旧 Agent 节点子树: %s/%s", AGENTS_ROOT, name)
            except Exception as exc:  # noqa: BLE001
                log.warning("清理 %s/%s 失败: %s", AGENTS_ROOT, name, exc)

    async def mount_agent_nodes(self, agent: str) -> None:
        runtime = self.registry.get(agent)
        if runtime is None:
            return
        base = f"{AGENTS_ROOT}/{agent}"
        await self.ctx.vfs_write_symbolic(
            f"{base}/status.md",
            lambda _path, name=agent: self.render_status(name),
            should_be_included_in_search=False,
            description=f"{agent} 的进程/会话/队列状态",
        )
        await self.ctx.vfs_write_symbolic(
            f"{base}/submit",
            lambda _path, name=agent: self.render_submit(name),
            should_be_included_in_search=False,
            writable=True,
            description="提交任务（写入信封；回执含 task id）",
        )
        await self.ctx.vfs_set_write_handler(
            f"{base}/submit", lambda _node, content, name=agent: self.submit(name, content)
        )
        await self.ctx.vfs_write_symbolic(
            f"{base}/config.json",
            lambda _path, name=agent: self.render_config(name),
            should_be_included_in_search=False,
            description=f"{agent} 当前会话的动态配置项与合法取值",
        )
        await self.ctx.vfs_write_symbolic(
            f"{base}/sessions.md",
            lambda _path, name=agent: self.render_sessions(name),
            should_be_included_in_search=False,
            description=f"{agent} 可续历史会话（缓存快照）",
        )
        await self.ctx.vfs_write_symbolic(
            f"{base}/tasks.md",
            lambda _path, name=agent: self.render_tasks(name),
            should_be_included_in_search=True,
            description=f"{agent} 的任务列表（一行一任务）",
        )
        await self.ctx.vfs_write_symbolic(
            f"{base}/permissions.md",
            lambda _path, name=agent: self.render_permissions(name),
            should_be_included_in_search=False,
            description=f"{agent} 待决权限列表",
        )
        await self.ctx.vfs_write_symbolic(
            f"{base}/cancel",
            lambda _path, name=agent: self.render_cancel(name),
            should_be_included_in_search=False,
            writable=True,
            description="取消当前任务（写入内容被忽略）",
        )
        await self.ctx.vfs_set_write_handler(
            f"{base}/cancel", lambda _node, _content, name=agent: self.cancel(name)
        )
        await self.ctx.vfs_write_symbolic(
            f"{base}/close",
            lambda _path, name=agent: self.render_close(name),
            should_be_included_in_search=False,
            writable=True,
            description="优雅关闭进程（sessionId 留存，写入内容被忽略）",
        )
        await self.ctx.vfs_set_write_handler(
            f"{base}/close", lambda _node, _content, name=agent: self.close(name)
        )
        self._agent_nodes[agent] = {"status.md", "submit", "config.json", "sessions.md", "tasks.md"}

    async def mount_task_nodes(self, agent: str, task_id: str) -> None:
        base = f"{AGENTS_ROOT}/{agent}/tasks/{task_id}"
        await self.ctx.vfs_write_symbolic(
            f"{base}/result.md",
            lambda _path, a=agent, t=task_id: self.render_result(a, t),
            should_be_included_in_search=True,
            description=f"{task_id} 的终态回复",
        )
        await self.ctx.vfs_write_symbolic(
            f"{base}/output.md",
            lambda _path, a=agent, t=task_id: self.render_output(a, t),
            should_be_included_in_search=False,
            description=f"{task_id} 的正文累积",
        )
        await self.ctx.vfs_write_symbolic(
            f"{base}/events.md",
            lambda _path, a=agent, t=task_id: self.render_events(a, t),
            should_be_included_in_search=False,
            description=f"{task_id} 的事件流（思考/工具/权限）",
        )

    async def mount_permission_node(self, agent: str, request_id: str, *, present: bool) -> None:
        path = f"{AGENTS_ROOT}/{agent}/permissions/{request_id}.md"
        if present:
            await self.ctx.vfs_write_symbolic(
                path,
                lambda _path, a=agent, r=request_id: self.render_permission(a, r),
                should_be_included_in_search=False,
                writable=True,
                description=f"权限请求 {request_id} 详情（写入即裁决）",
            )
            await self.ctx.vfs_set_write_handler(
                path, lambda _node, content, a=agent, r=request_id: self.answer_permission(a, r, content)
            )
        else:
            try:
                await self.ctx.vfs_delete(path)
            except Exception as exc:  # noqa: BLE001
                log.debug("卸载权限节点 %s 失败: %s", path, exc)

    async def drop_agent_nodes(self, agent: str) -> None:
        try:
            await self.ctx.vfs_delete(f"{AGENTS_ROOT}/{agent}")
        except Exception as exc:  # noqa: BLE001
            log.warning("删除 %s/%s 节点失败: %s", AGENTS_ROOT, agent, exc)
        self._agent_nodes.pop(agent, None)

    async def unmount(self) -> None:
        """插件卸载：删干净自己挂载的全部节点（core 不会清理插件节点）。"""
        for agent in list(self._agent_nodes):
            await self.drop_agent_nodes(agent)
        existing = await self.ctx.vfs_list(AGENTS_ROOT) or []
        for name in existing:
            try:
                await self.ctx.vfs_delete(f"{AGENTS_ROOT}/{name}")
            except Exception:  # noqa: BLE001
                pass
        if not (await self.ctx.vfs_list(AGENTS_ROOT) or []):
            # 空目录也不留（不变量：卸载后不得残留本插件的节点）
            try:
                await self.ctx.vfs_delete(AGENTS_ROOT)
            except Exception:  # noqa: BLE001
                pass
        for path in (PLUGIN_DOC_PATH,):
            try:
                await self.ctx.vfs_delete(path)
            except Exception:  # noqa: BLE001
                pass
        self._mounted = False

    # ── 渲染（只读内存） ──

    def render_index(self) -> str:
        rows: list[str] = ["# 外部 Agent 索引", ""]
        runtimes = {item.name: item for item in self.registry.runtimes()}
        names = self.registry.agent_names()
        if not names:
            rows.append("（未发现任何可用 Agent；读 discover.md 看原因）")
            return "\n".join(rows)
        for name in names:
            runtime = runtimes.get(name)
            if runtime is None:
                rows.append(f"- {name} ｜ 未启动 ｜ 当前任务 - ｜ 最后活动 -")
                continue
            current = self.store.current_task(name)
            status = {
                "stopped": "未启动",
                "starting": "启动中",
                "ready": "就绪",
                "error": "错误",
            }.get(runtime.phase, runtime.phase)
            last = time.strftime("%H:%M:%S", time.localtime(runtime.last_activity))
            rows.append(
                f"- {name} ｜ {status} ｜ 当前任务 {current.id if current else '-'} "
                f"｜ 队列 {self.store.queue_depth(name)} ｜ 最后活动 {last}"
            )
        rows.append("")
        rows.append("详情：faustbot://agents/{name}/status.md ｜ 提交：写 faustbot://agents/{name}/submit")
        return "\n".join(rows)

    def render_discover(self) -> str:
        age = self.registry.discover_age_sec
        return render_discover_md(self.registry.discover_rows(), cache_age_sec=age if age >= 0 else None)

    def render_status(self, agent: str) -> str:
        runtime = self.registry.get(agent)
        if runtime is None:
            return f"# {agent} 状态\n\n(未注册：配置里没有它，也没探测到)"
        status = runtime.status()
        current = self.store.current_task(agent)
        rows = [
            f"# {agent} 状态",
            "",
            f"- 进程：{'运行中 pid=' + str(status['pid']) if status['running'] else '未启动'}",
            f"- 阶段：{status['phase']}",
            f"- 协议版本：{status['protocol_version']}",
            f"- 会话：{status['session_id'] or '(无)'}",
            f"- 工作目录：{status['cwd']}",
            f"- 空闲时长：{status['idle_sec']:.0f}s",
            f"- 队列深度：{self.store.queue_depth(agent)}/{self.store.max_queue}",
            f"- 待决权限：{self.store.permission_count(agent)}",
        ]
        if current is not None:
            rows.append(
                f"- 当前任务：{current.id}（{current.status_label()}，已等 {current.elapsed_sec:.0f}s，"
                f"上限 {current.timeout_sec:.0f}s）"
            )
        else:
            rows.append("- 当前任务：(无)")
        if status["last_error"]:
            rows.append(f"- 最近错误：{status['last_error']}")
        if status["auth_methods"]:
            methods = ", ".join(f"{item['id']}" for item in status["auth_methods"])
            rows.append(f"- 可用认证方式：{methods}（未登录时需在终端执行登录命令）")
        if status["available_commands"]:
            rows.append(f"- 可用命令：{', '.join(status['available_commands'])}")
        if status["stderr_tail"]:
            rows.append("")
            rows.append("## 最近 stderr（末 10 行）")
            rows.extend(f"    {line}" for line in status["stderr_tail"][-10:])
        rows.append("")
        rows.append(f"提交任务：写 faustbot://agents/{agent}/submit")
        return "\n".join(rows)

    def render_submit(self, agent: str) -> str:
        rows = [
            f"# 向 {agent} 提交任务",
            "",
            "写入本节点即提交；写入内容的解析规则与字段说明见下。",
            "",
            "## 信封 schema",
            "",
            ENVELOPE_SCHEMA,
            "",
            "## 示例",
            "",
            '{"prompt": "解释 backend/main.py 的启动流程", "notify": "batched"}',
            "",
            "纯文本简写：`解释一下 backend/main.py 的启动流程`",
        ]
        receipt = self._last_receipt.get(agent)
        rows += ["", "## 上次提交回执", "", receipt or "（本次插件加载后还没有提交记录）"]
        return "\n".join(rows)

    def render_config(self, agent: str) -> str:
        runtime = self.registry.get(agent)
        if runtime is None:
            return "{}"
        if not runtime.config_options:
            return json.dumps(
                {
                    "session": runtime.session_id,
                    "note": "当前没有会话配置快照（尚未建立会话，或该 Agent 不支持 configOptions）",
                    "configOptions": [],
                },
                ensure_ascii=False,
                indent=2,
            )
        payload = {
            "session": runtime.session_id,
            "configOptions": [item for item in runtime.status()["config_options"]],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def render_sessions(self, agent: str) -> str:
        runtime = self.registry.get(agent)
        if runtime is None:
            return f"# {agent} 历史会话\n\n(未注册)"
        rows = [f"# {agent} 历史会话", ""]
        age = time.time() - runtime.sessions_ts if runtime.sessions_ts else -1
        if age >= 0:
            rows.append(f"（缓存于 {age:.0f}s 前；进程启动/新建会话时后台刷新）")
        else:
            rows.append("（尚未取得快照；下次该 Agent 进程启动时会后台刷新）")
        rows.append("")
        if runtime.sessions_error:
            rows.append(f"最近一次 session/list 失败：{runtime.sessions_error}")
            rows.append("")
        if not runtime.cached_sessions:
            rows.append("（无历史会话，或尚未获取）")
            return "\n".join(rows)
        rows.append("| sessionId | 标题 | cwd | 更新时间 |")
        rows.append("|---|---|---|---|")
        for item in runtime.cached_sessions:
            rows.append(
                f"| `{item.get('session_id')}` | {item.get('title') or '-'} "
                f"| {item.get('cwd') or '-'} | {item.get('updated_at') or '-'} |"
            )
        rows.append("")
        rows.append(f'续接某个会话：提交时把 session 设为该 sessionId（走 session/load）。')
        return "\n".join(rows)

    def render_tasks(self, agent: str) -> str:
        tasks = self.store.tasks_of(agent)
        rows = [f"# {agent} 任务列表", ""]
        if not tasks:
            rows.append("（还没有任务）")
            return "\n".join(rows)
        rows.append("| id | 状态 | 耗时 | 摘要 |")
        rows.append("|---|---|---|---|")
        for task in reversed(tasks):
            rows.append(
                f"| `{task.id}` | {task.status_label()} | {task.elapsed_sec:.0f}s | {task.prompt_summary(50)} |"
            )
        rows.append("")
        rows.append(f"当前队列：{self.store.queue_depth(agent)}/{self.store.max_queue}（排队中的任务按提交顺序串行执行）")
        return "\n".join(rows)

    def render_result(self, agent: str, task_id: str) -> str:
        task = self.store.get(task_id)
        if task is None or task.agent != agent:
            return f"（找不到任务 {task_id}）"
        rows = [
            f"# {task.id} 结果",
            "",
            f"- 状态：{task.status_label()}（{task.status}）",
            f"- 耗时：{task.elapsed_sec:.0f}s",
            f"- session：{task.session_id or '-'}",
            f"- stopReason：{task.stop_reason or '-'}",
        ]
        if task.error:
            rows.append(f"- 原因：{task.error}")
        if not task.is_terminal:
            rows.append("")
            rows.append("**任务尚未进入终态，本文件还不是最终结论。**")
        rows.append("")
        rows.append("## 正文")
        rows.append("")
        rows.append(task.result_body() or "（无正文）")
        return "\n".join(rows)

    def render_output(self, agent: str, task_id: str) -> str:
        task = self.store.get(task_id)
        if task is None or task.agent != agent:
            return f"（找不到任务 {task_id}）"
        body = task.output_text
        return body if body else "（该任务还没有正文输出）"

    def render_events(self, agent: str, task_id: str) -> str:
        task = self.store.get(task_id)
        if task is None or task.agent != agent:
            return f"（找不到任务 {task_id}）"
        if not task.event_lines:
            return "（该任务还没有事件）"
        return "\n".join(task.event_lines)

    def render_permissions(self, agent: str) -> str:
        pending = self.store.pending_permissions(agent)
        rows = [f"# {agent} 待决权限", ""]
        if not pending:
            rows.append("（当前没有待决权限请求）")
            return "\n".join(rows)
        rows.append("| reqId | 工具 | 剩余秒数 | 来源任务 |")
        rows.append("|---|---|---|---|")
        for item in pending:
            rows.append(
                f"| `{item.request_id}` | {item.tool_title} | {item.remaining_sec:.0f} | {item.task_id or '-'} |"
            )
        rows.append("")
        rows.append(f"裁决：写 faustbot://agents/{agent}/permissions/{{reqId}}.md")
        rows.append("超时未裁决将执行默认动作（默认拒绝），请在剩余秒数内处理。")
        return "\n".join(rows)

    def render_permission(self, agent: str, request_id: str) -> str:
        request = self.store.get_permission(request_id)
        if request is None or request.agent != agent:
            return f"（找不到权限请求 {request_id}；可能已被处理）"
        rows = [
            f"# 权限请求 {request.request_id}",
            "",
            f"- 状态：{request.status}",
            f"- 来源任务：{request.task_id or '-'}",
            f"- 会话：{request.session_id}",
            f"- 工具：{request.tool_title}",
            f"- 工具类型：{request.tool_kind or '(未提供)'}",
            f"- 剩余秒数：{request.remaining_sec:.0f}（超时执行默认动作）",
        ]
        if request.note:
            rows.append(f"- 备注：{request.note}")
        rows.append("")
        rows.append("## 参数摘要")
        rows.append("")
        rows.append(request.summary or "（外部 Agent 未提供参数）")
        rows.append("")
        rows.append("## 可选 option（ACP 原样）")
        rows.append("")
        rows.append("| optionId | 名称 | kind |")
        rows.append("|---|---|---|")
        for item in request.option_rows():
            rows.append(f"| `{item['option_id']}` | {item['name']} | {item['kind']} |")
        rows.append("")
        rows.append("## 怎么裁决")
        rows.append("")
        rows.append('写入内容为 JSON：{"outcome": "allow"|"deny", "scope": "once"|"always", "reason": "可选"}')
        rows.append("")
        rows.append("- allow→按 `allow_*` 匹配 option，deny→按 `reject_*` 匹配；")
        rows.append("- `reason` 只做本地留痕（ACP 应答体没有这个字段，无法回传给外部 Agent）；")
        rows.append("- Agent 未提供 kind 时会取第一个 option，并在回执里如实标注。")
        return "\n".join(rows)

    def render_cancel(self, agent: str) -> str:
        current = self.store.current_task(agent)
        queued = self.store.queue_depth(agent)
        if current is None and not queued:
            return f"# 取消 {agent} 的任务\n\n(当前没有可取消的任务)"
        rows = [f"# 取消 {agent} 的任务", ""]
        if current is not None:
            rows.append(f"- 当前任务：{current.id}（{current.status_label()}）")
        rows.append(f"- 排队中：{queued}")
        rows.append("")
        rows.append("写入本节点即取消（内容被忽略）。")
        return "\n".join(rows)

    def render_close(self, agent: str) -> str:
        runtime = self.registry.get(agent)
        if runtime is None:
            return f"# 关闭 {agent} 进程\n\n(未注册)"
        rows = [
            f"# 关闭 {agent} 进程",
            "",
            f"- 进程：{'运行中 pid=' + str(runtime.bridge.pid) if runtime.running else '未启动'}",
            f"- 会话：{runtime.session_id or '(无)'}",
            f"- 阶段：{runtime.phase}",
            "",
            "写入本节点即优雅关闭（先 session/close 再终止进程，sessionId 会保留供下次续接，内容被忽略）。",
        ]
        return "\n".join(rows)

    # ── write handler ──

    async def submit(self, agent: str, content: Any) -> str:
        runtime = self.registry.get(agent)
        if runtime is None:
            raise EnvelopeError(f"未注册的 Agent `{agent}`")
        if agent not in self._agent_nodes:
            # 懒创建的 Agent（首次提交才出现）也要有节点
            await self.mount_agent_nodes(agent)
        settings = self.registry.settings
        envelope = parse_envelope(
            content,
            default_cwd=runtime.cwd,
            default_notify=settings.notify_default,
            default_timeout=float(runtime.settings.task_timeout_sec),
        )
        task = self.store.submit(agent, envelope)
        self._last_task_id[agent] = task.id
        await self.mount_task_nodes(agent, task.id)
        if task.status == "rejected":
            receipt = (
                f"队列已满 ({self.store.queue_depth(agent)}/{self.store.max_queue})，"
                f"任务 {task.id} 已标记 rejected，未执行。"
            )
        else:
            session_label = task.session_mode if task.session_mode in ("reuse", "new", "close") else task.session_mode
            receipt = (
                f"已提交 {task.id}（session={session_label}, 状态={task.status_label()}, "
                f"队列 {task.queue_depth}/{self.store.max_queue}）\n"
                f"  结果读取：faustbot://agents/{agent}/tasks/{task.id}/result.md\n"
                f"  进度读取：faustbot://agents/{agent}/tasks/{task.id}/events.md"
            )
        if envelope.warnings:
            receipt += "\n  " + "；".join(envelope.warnings)
        self._last_receipt[agent] = receipt
        return receipt

    async def answer_permission(self, agent: str, request_id: str, content: Any) -> str:
        return self.store.answer_permission(agent, request_id, content)

    async def cancel(self, agent: str) -> str:
        ok, message = await self.store.cancel(agent)
        if not ok:
            return message
        return message

    async def close(self, agent: str) -> str:
        runtime = self.registry.get(agent)
        if runtime is None:
            raise PermissionAnswerError(f"未注册的 Agent `{agent}`")
        had_session = runtime.session_id
        if not runtime.running:
            return f"{agent} 进程本就没有在运行（sessionId={had_session or '(无)'}）"
        await runtime.close_session()
        await runtime.stop()
        return (
            f"已关闭 {agent} 进程（sessionId={had_session or '(无)'} 已保留，"
            "下次提交可用 session 指定该 id 续接）"
        )


__all__ = ["AGENTS_ROOT", "PLUGIN_DOC_PATH", "STATUS_LABELS", "VfsSurface"]
