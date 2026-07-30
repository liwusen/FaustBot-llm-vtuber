import json
from datetime import datetime, timedelta
from pathlib import Path

from langchain.tools import tool

from faust_backend.tools._registry import register
import faust_backend.nimble as nimble
import faust_backend.backend2front as backend2frontend
import faust_backend.trigger_manager as trigger_manager
from faust_backend.logger import get_logger
from faust_backend.runtime.uri import (
    parse,
    SCHEME_FAUSTBOT,
    SCHEME_MEMORY,
    SCHEME_SKILL,
)
from faust_backend.tools.vfs import (
    ensure_source_file,
    get_faustbot_vfs,
    refresh_runtime_nodes,
    run_coro_sync,
)

log = get_logger("faust.tools.nimble")


def _resolve_path_html(spec: str) -> str:
    """把 `path:{URI}` 解析为原始 HTML 全文（不走 read 工具的摘要/截断逻辑）。"""
    ref = spec.split(":", 1)[1].strip()
    if not ref:
        raise ValueError("path: 后必须跟一个 URI 或文件路径")
    parsed = parse(ref)

    if parsed.scheme == SCHEME_SKILL:
        from faust_backend.runtime import state

        parts = [p for p in parsed.path.split("/") if p]
        if not parts:
            raise ValueError("skill:// 路径必须包含 skill 名称")
        if any(p in (".", "..") for p in parts):
            raise ValueError("不允许越界访问 skill 目录")
        skill_root = (Path(state.AGENT_ROOT) / "skill.d" / parts[0]).resolve()
        target = (skill_root / Path(*parts[1:])) if len(parts) > 1 else skill_root / "SKILL.md"
        target = target.resolve()
        if not str(target).startswith(str(skill_root)):
            raise ValueError("不允许访问 skill 目录外的文件")
        if not target.is_file():
            raise FileNotFoundError(f"skill 文件不存在: {ref}")
        return target.read_text(encoding="utf-8")

    if parsed.scheme == SCHEME_FAUSTBOT:
        vfs = get_faustbot_vfs(refresh=True)
        run_coro_sync(refresh_runtime_nodes(vfs))
        normalized = "/" + parsed.path
        if parsed.path.startswith("source/"):
            normalized = run_coro_sync(
                ensure_source_file(vfs, parsed.path[len("source/"):])
            )
        content = run_coro_sync(vfs.read_text(normalized, default=""))
        if not content:
            raise FileNotFoundError(f"faustbot 资源不存在或为空: {ref}")
        return content

    if parsed.scheme == SCHEME_MEMORY:
        from faust_backend.memory import get_memory

        result = run_coro_sync(get_memory().file_read(parsed.path))
        return str(result.get("content", ""))

    # 磁盘文件
    from faust_backend.config_loader import PROJECT_ROOT, CONFIG_ROOT, WORKDIR_ROOT

    file_path = Path(parsed.path)
    if not file_path.is_absolute():
        for base in (WORKDIR_ROOT, PROJECT_ROOT, CONFIG_ROOT):
            candidate = Path(base) / parsed.path
            if candidate.is_file():
                file_path = candidate
                break
    if not file_path.is_file():
        raise FileNotFoundError(f"文件不存在: {ref}")
    return file_path.read_text(encoding="utf-8")


@register
@tool
def showNimbleWindowTool(html: str, title: str = "灵动交互", recall_text: str = "用户仍在处理这个灵动窗口，请查看用户是否已完成操作。", reminder_interval_seconds: int = 120, lifespan: int = 1800, metadata_json: str = "{}", persistent: bool = False, persistent_id: str = "") -> str:
    """
    Description:
        非阻塞地创建一个"灵动交互"窗口，并显示在前端虚拟形象旁边。

        这是处理复杂任务确认、表单填写、选项确认、小游戏对弈等场景的核心工具。
        调用后不会阻塞当前对话，也不会等待用户立即完成操作。它会：
        1. 在前端显示一个独立的 HTML 窗口（窗口以小组件形式注册，可被用户拖动/缩放）；
        2. 自动绑定 reminder trigger（周期性提醒你关注该窗口）和 expire trigger（到期自动关闭）；
        3. 在 VFS 注册该窗口的双向通信节点（见下方"双向通信"）。

        HTML 来源（两种方式）：
        - 直接传入 HTML 字符串；
        - 传入 `path:{URI}` 从文件加载，例如：
          `path:skill://nimble-window/tictactoe.html`（skill 内置模板）、
          `path:faustbot://source/frontend/xxx.html`、`path:memory://notes/x`、`path:D:/tmp/a.html`。

        双向通信（核心机制，全部走 console）：
        - faustbot://nimble/{callback_id}/summary — 窗口概览（工具自动生成，你可用 write 覆写）；
        - faustbot://nimble/{callback_id}/console — 终端式对话记录，格式为：
            Frontend>{"result":"hello"}
            You>{"command":"do-something"}
          前端 sendMessage 的消息会追加 `Frontend>` 行；
          你用 write 工具写入该路径，内容会追加为 `You>` 行并实时发给窗口内 JS 处理；
        - faustbot://nimble/{callback_id}/code-readonly — 窗口 HTML 源码（只读）。

        窗口内 HTML 可用的 JS API（每个窗口独立注入 `nimble` 对象，脚本内直接用）：
        - `nimble.sendMessage(createEventTrigger, payload)` — 向后端发消息。
          createEventTrigger=true 时会用 trigger 唤醒你；false 时只记录到 console 不唤醒。
        - `nimble.setMessageHandler(func)` — 设置消息回调，func(payload) 接收你写入 console 的消息。
        - `nimble.resize(width, height)` / `nimble.setFullscreen(enabled)` / `nimble.getConfig()`。

        你可以通过向 console 写入保留命令来控制窗口（前端运行时拦截，不会进 messageHandler）：
        - `{"type":"command","command":"close-window","args":{}}` — 关闭窗口；
        - `{"type":"command","command":"set-scale","args":{"scale":1.2}}` — 设置缩放；
        - `{"type":"command","command":"set-coord","args":{"x":0.5,"y":0.5}}` — 设置屏幕坐标（0~1）。

        HTML 元素上添加 class="nimble-pass-through" 可让该区域在点击穿透模式下不阻挡桌面操作，
        适合全屏叠加（setFullscreen(true)）中的背景装饰、文字显示等非交互区域。

        设置 persistent=true 可以创建持久化窗口（含 console 历史），程序重启后自动恢复。
        持久化窗口需要 persistent_id（唯一标识），callback_id 固定为 persistent_{persistent_id}。

        现成的游戏模板与协议示例见 read("skill://nimble-window/SKILL.md")。

        注意：
        - 这是非阻塞工具，调用后你不应假设用户已经给出答案；
        - 前端消息会通过 trigger 在后续唤醒你，届时先读 console 了解完整上下文再回复。

    Args:
        html (str): 要展示的 HTML 内容，或 `path:{URI}` 形式的内容来源。
        title (str): 窗口标题。
        recall_text (str): reminder trigger 唤醒你时附带的提示信息。
        reminder_interval_seconds (int): 提醒周期秒数，默认 120 秒。
        lifespan (int): 窗口生命周期（秒）。
        metadata_json (str): 额外元数据 JSON 字符串。
        persistent (bool): 是否为持久化窗口（重启后自动恢复）。
        persistent_id (str): 持久化窗口唯一标识（persistent=True 时必填）。
    Returns:
        str: 创建结果说明，包含 callback_id 和 VFS 通信路径。
    """
    try:
        if str(html or "").strip().startswith("path:"):
            html = _resolve_path_html(str(html).strip())
        metadata = json.loads(metadata_json) if metadata_json else {}
        if persistent:
            if not persistent_id:
                return "错误：persistent=True 时必须提供 persistent_id。"
            callback_id = f"persistent_{persistent_id}"
            lifespan = max(lifespan, 31536000)
        else:
            callback_id = nimble.build_callback_id()
        session = nimble.create_nimble_session(
            callback_id,
            title=title,
            html=html,
            recall_text=recall_text,
            reminder_interval_seconds=reminder_interval_seconds,
            lifespan=lifespan,
            metadata=metadata,
            persistent=persistent,
            persistent_id=persistent_id,
        )

        trigger_manager.append_trigger({
            "id": session["reminder_trigger_id"],
            "type": "nimble-reminder",
            "callback_id": callback_id,
            "interval_seconds": reminder_interval_seconds,
            "recall_description": recall_text,
            "lifespan": lifespan,
        })
        if not persistent:
            trigger_manager.append_trigger({
                "id": session["expire_trigger_id"],
                "type": "nimble-expire",
                "callback_id": callback_id,
                "target": (datetime.now() + timedelta(seconds=lifespan)).isoformat(),
                "recall_description": f"灵动窗口 {callback_id} 已过期。",
                "lifespan": lifespan,
            })
        else:
            nimble.save_persistent_session(session)
        run_coro_sync(nimble.register_session_vfs_nodes(callback_id))
        backend2frontend.FrontEndShowNimbleWindow(nimble.export_window_payload(callback_id))
        return (
            f"灵动交互窗口已创建，callback_id={callback_id}。\n"
            f"通信节点: faustbot://nimble/{callback_id}/{{summary,console,code-readonly}}。\n"
            f"该窗口为非阻塞式，前端消息会通过 trigger 唤醒你，用 write 工具写 console 即可回复。"
        )
    except Exception as e:
        return f"创建灵动交互窗口失败: {str(e)}"


@register
@tool
def closeNimbleWindowTool(callback_id: str, reason: str = "closed_by_agent") -> str:
    """
    Description:
        主动关闭一个已存在的灵动交互窗口，并清理其关联的 trigger 与 VFS 通信节点
        （faustbot://nimble/{callback_id}/ 目录会被删除）。
        当你确认这个窗口已不再需要，或者任务已经结束、用户已取消时，应调用此工具清理资源。
    Args:
        callback_id (str): 需要关闭的灵动窗口 callback_id。
        reason (str): 关闭原因。
    Returns:
        str: 关闭结果。
    """
    try:
        session = nimble.finalize_close(callback_id, reason=reason)
        if not session:
            return f"未找到 callback_id={callback_id} 对应的灵动窗口。"
        return f"灵动窗口已关闭，callback_id={callback_id}"
    except Exception as e:
        return f"关闭灵动窗口失败: {str(e)}"
