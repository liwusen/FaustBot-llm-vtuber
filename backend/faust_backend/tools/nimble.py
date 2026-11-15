import json
from datetime import datetime, timedelta
from pathlib import Path

from langchain.tools import tool

from faust_backend.tools._registry import register
import faust_backend.integrations.nimble as nimble
import faust_backend.core.frontend.api as backend2frontend
import faust_backend.agent.trigger as trigger_manager
from faust_backend.core.log import get_logger
from faust_backend.core.uri import (
    parse,
    SCHEME_FAUSTBOT,
    SCHEME_MEMORY,
    SCHEME_SKILL,
    SCHEME_SOURCE_CODE,
)
from faust_backend.tools.vfs import (
    get_faustbot_vfs,
    refresh_runtime_nodes,
)

log = get_logger("faust.tools.nimble")


async def _resolve_path_html(spec: str) -> str:
    """把 `path:{URI}` 解析为原始 HTML 全文（不走 read 工具的摘要/截断逻辑）。"""
    ref = spec.split(":", 1)[1].strip()
    if not ref:
        raise ValueError("path: 后必须跟一个 URI 或文件路径")
    parsed = parse(ref)

    if parsed.scheme == SCHEME_SKILL:
        from faust_backend.agent.runtime import state

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

    if parsed.scheme == SCHEME_SOURCE_CODE:
        import faust_backend.core.config as conf

        repo_root = Path(conf.PROJECT_ROOT).parent
        parts = [p for p in parsed.path.split("/") if p]
        if any(p in (".", "..") for p in parts):
            raise ValueError("不允许越界访问源码目录")
        target = (repo_root / Path(*parts)).resolve()
        if not str(target).startswith(str(repo_root.resolve())):
            raise ValueError("不允许访问源码根目录外的文件")
        if not target.is_file():
            raise FileNotFoundError(f"源码文件不存在: {ref}")
        return target.read_text(encoding="utf-8", errors="replace")

    if parsed.scheme == SCHEME_FAUSTBOT:
        vfs = await get_faustbot_vfs(refresh=True)
        await refresh_runtime_nodes(vfs)
        normalized = "/" + parsed.path
        content = await vfs.read_text(normalized, default="")
        if not content:
            raise FileNotFoundError(f"faustbot 资源不存在或为空: {ref}")
        return content

    if parsed.scheme == SCHEME_MEMORY:
        from faust_backend.memory import get_memory

        result = await get_memory().file_read(parsed.path)
        return str(result.get("content", ""))

    # 磁盘文件
    from faust_backend.core.config import PROJECT_ROOT, CONFIG_ROOT, WORKDIR_ROOT

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
async def showNimbleWindowTool(html: str, title: str = "灵动交互", recall_text: str = "用户仍在处理这个灵动窗口，请查看用户是否已完成操作。", lifespan: int = 1800, metadata_json: str = "{}", persistent: bool = False, persistent_id: str = "") -> str:
    r"""非阻塞地创建一个 HTML 交互窗口（显示在虚拟形象旁），用于确认、表单、小游戏。

    html 可直接传字符串，或用 `path:{URI}` 加载；内置模板：
    `path:skill://nimble-window/tictactoe.html`（三子棋）/ `gomoku.html` / `wordle.html`（你出题）。

    窗口通过 VFS 双向通信，全部走 console：
    - `faustbot://nimble/{callback_id}/console` — 追加式记录。前端发来的消息是 `Frontend>` 行；
      你 write 该路径即成为 `You>` 行并实时送达页面。
    - `/summary` 窗口概览（可 write 覆写）、`/code-readonly` 窗口源码。
    保留命令（写 console 生效）：`close-window` / `set-scale{scale}` / `set-coord{x,y}`。

    你自己关掉的窗口不会再唤醒你；用户点 × 关闭会唤醒一次。
    persistent=true 时窗口重启后自动恢复，callback_id 固定为 `persistent_{persistent_id}`。

    完整协议、JS API、模板与配色：read("skill://nimble-window/SKILL.md")

    Args:
        html (str): HTML 内容，或 `path:{URI}` 来源。
        title (str): 窗口标题。
        recall_text (str): 窗口用途，用于概览与过期提示。
        lifespan (int): 生命周期（秒），默认 1800。
        persistent (bool) / persistent_id (str): 持久化窗口及其唯一标识（persistent=True 时必填）。
    """
    try:
        if str(html or "").strip().startswith("path:"):
            html = await _resolve_path_html(str(html).strip())
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
            lifespan=lifespan,
            metadata=metadata,
            persistent=persistent,
            persistent_id=persistent_id,
        )

        if not persistent:
            await trigger_manager.append_trigger({
                "id": session["expire_trigger_id"],
                "type": "nimble-expire",
                "callback_id": callback_id,
                "target": (datetime.now() + timedelta(seconds=lifespan)).isoformat(),
                "recall_description": f"灵动窗口 {callback_id} 已过期。",
                "lifespan": lifespan,
            })
        else:
            nimble.save_persistent_session(session)
        await nimble.register_session_vfs_nodes(callback_id)
        backend2frontend.FrontEndShowNimbleWindow(nimble.export_window_payload(callback_id) or {})
        return (
            f"灵动交互窗口已创建，callback_id={callback_id}。\n"
            f"通信节点: faustbot://nimble/{callback_id}/{{summary,console,code-readonly}}。\n"
            f"该窗口为非阻塞式，前端消息会通过 trigger 唤醒你，用 write 工具写 console 即可回复。"
        )
    except Exception as e:
        return f"创建灵动交互窗口失败: {str(e)}"


@register
@tool
async def closeNimbleWindowTool(callback_id: str, reason: str = "closed_by_agent") -> str:
    r"""关闭一个灵动窗口，并清理它的 trigger 与 VFS 节点（faustbot://nimble/{callback_id}/ 会消失）。

    这是"你自己要关"的窗口：不会再有 trigger 唤醒你，所以该对用户说的话要先说完
    （用户手动点 × 关闭则会唤醒你一次）。

    Args:
        callback_id (str): 要关闭的窗口 callback_id。
        reason (str): 关闭原因。
    """
    try:
        session = await nimble.finalize_close(callback_id, reason=reason)
        if not session:
            return f"未找到 callback_id={callback_id} 对应的灵动窗口。"
        return f"灵动窗口已关闭，callback_id={callback_id}"
    except Exception as e:
        return f"关闭灵动窗口失败: {str(e)}"
