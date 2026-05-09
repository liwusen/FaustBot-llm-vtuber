import json
from datetime import datetime, timedelta

from langchain.tools import tool

from faust_backend.tools._registry import register
import faust_backend.nimble as nimble
import faust_backend.backend2front as backend2frontend
import faust_backend.trigger_manager as trigger_manager
from faust_backend.logger import get_logger

log = get_logger("faust.tools.nimble")


@register
@tool
def showNimbleWindowTool(html: str, title: str = "灵动交互", recall_text: str = "用户仍在处理这个灵动窗口，请查看用户是否已完成操作。", reminder_interval_seconds: int = 120, lifespan: int = 1800, metadata_json: str = "{}", persistent: bool = False, persistent_id: str = "") -> str:
    """
    Description:
        非阻塞地创建一个"灵动交互"窗口，并显示在前端虚拟形象旁边。

        这是处理复杂任务确认、表单填写、选项确认、安装参数收集等场景的核心工具。
        调用后不会阻塞当前对话，也不会等待用户立即完成操作。
        相反，它会：
        1. 在前端显示一个独立的 HTML 窗口；
        2. 自动绑定一个 reminder trigger，周期性提醒你关注该窗口；
        3. 自动绑定一个 result trigger，当用户提交时再次唤醒你；
        4. 自动绑定一个 expire trigger，窗口生命周期结束时自动关闭；
        5. 当窗口被用户关闭或提交后，其关联 trigger 会一并删除。

        你应当在如下情况使用它：
        - 需要用户选择多个选项；
        - 需要用户填写文本/路径/参数；
        - 需要用户确认安装、危险操作、批量操作细节；
        - 纯语音交互效率低、歧义大、确认轮次过多时。
        - 其他需要更丰富的交互方式的任何场景

        前端窗口中的 HTML 可以包含自定义 UI 元素，例如：按钮、复选框、输入框、选择器等。
        你写入的 HTML 中可以直接调用前端注入的 JavaScript API：

        - `window.nimble.submit(data, closeWindow=true)` — 向后端提交结果。closeWindow 默认为 true，设为 false 时不关闭窗口，可连续提交。
        - `window.nimble.close(reason)` — 关闭当前窗口，并清理绑定 trigger。
        - `window.nimble.resize(width, height)` — 调整窗口尺寸（如 "400px"）。
        - `window.nimble.move(x, y)` — 移动窗口位置（如 "100px"）。
        - `window.nimble.setDraggable(enabled)` — 启用/禁用拖拽（true/false）。
        - `window.nimble.setFullscreen(enabled)` — 切换全屏透明模式（true/false）。全屏时窗口铺满屏幕、背景透明，适合叠加显示。
        - `window.nimble.getConfig()` — 获取窗口当前状态（尺寸、位置等）。

        这些 API 会自动关联当前窗口的 callback_id，因此你不需要手动拼接 callback_id。

        你可以通过在 HTML 元素上添加 class="nimble-pass-through" 来让该区域在点击穿透模式下不阻挡桌面操作。
        适合全屏叠加（setFullscreen(true)）中的背景装饰、文字显示等非交互区域。

        设置 persistent=true 可以创建持久化窗口，即使程序重启也会自动恢复。
        持久化窗口需要 persistent_id（唯一标识），后续可用该 ID 引用。

        一个常见示例：
        ```html
        <div style="padding:12px; color:#fff;">
          <h3>安装确认</h3>
          <label>安装路径 <input id="installPath" value="D:/Apps/Test" /></label>
          <label><input id="desktopShortcut" type="checkbox" checked /> 创建桌面快捷方式</label>
          <div style="margin-top:12px; display:flex; gap:8px;">
            <button onclick="window.nimble.submit({ action: 'confirm', installPath: document.getElementById('installPath').value, desktopShortcut: document.getElementById('desktopShortcut').checked })">确认</button>
            <button onclick="window.nimble.close('cancelled')">取消</button>
          </div>
        </div>
        ```

        注意：
        - 这是非阻塞工具。调用后你不应假设用户已经给出答案；
        - 真正的结果会通过 trigger 在后续再次唤醒你；
        - 你的后续逻辑应等待由 result/reminder/expire 触发的新上下文，而不是在当前轮强行继续索要结果。

    Args:
        html (str): 要展示在前端窗口中的 HTML 内容。
        title (str): 窗口标题。
        recall_text (str): reminder trigger 唤醒你时附带的提示信息。
        reminder_interval_seconds (int): 提醒周期秒数，默认 120 秒。
        lifespan (int): 窗口生命周期（秒）。
        metadata_json (str): 额外元数据 JSON 字符串。
        persistent (bool): 是否为持久化窗口（重启后自动恢复）。
        persistent_id (str): 持久化窗口唯一标识（persistent=True 时必填）。
    Returns:
        str: 创建结果说明，包含 callback_id。
    """
    try:
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
            "id": session["result_trigger_id"],
            "type": "event",
            "event_name": "nimble_result",
            "callback_id": callback_id,
            "recall_description": f"灵动窗口 {callback_id} 收到了用户提交结果。",
            "lifespan": lifespan,
        })
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
        backend2frontend.FrontEndShowNimbleWindow(nimble.export_window_payload(callback_id))
        return f"灵动交互窗口已创建，callback_id={callback_id}。该窗口为非阻塞式，结果会在后续 trigger 唤醒时返回。"
    except Exception as e:
        return f"创建灵动交互窗口失败: {str(e)}"


@register
@tool
def closeNimbleWindowTool(callback_id: str, reason: str = "closed_by_agent") -> str:
    """
    Description:
        主动关闭一个已存在的灵动交互窗口，并清理其关联的 result/reminder/expire trigger。
        当你确认这个窗口已不再需要，或者任务已经结束、用户已取消时，应调用此工具清理资源。
    Args:
        callback_id (str): 需要关闭的灵动窗口 callback_id。
        reason (str): 关闭原因。
    Returns:
        str: 关闭结果。
    """
    try:
        session = nimble.close_nimble_session(callback_id, reason=reason)
        if not session:
            return f"未找到 callback_id={callback_id} 对应的灵动窗口。"
        trigger_manager.delete_trigger(session["result_trigger_id"])
        trigger_manager.delete_trigger(session["reminder_trigger_id"])
        trigger_manager.delete_trigger(session["expire_trigger_id"])
        backend2frontend.FrontEndCloseNimbleWindow({"callback_id": callback_id, "reason": reason})
        nimble.cleanup_nimble_session(callback_id)
        return f"灵动窗口已关闭，callback_id={callback_id}"
    except Exception as e:
        return f"关闭灵动窗口失败: {str(e)}"
