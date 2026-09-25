import json

from langchain.tools import tool

from faust_backend.tools._registry import register
import faust_backend.config_loader as conf
import faust_backend.backend2front as backend2frontend
import faust_backend.vrm_pose_manager as vrm_pose_manager

VRM_EXPRESSIONS = ["neutral", "happy", "angry", "sad", "relaxed", "surprised"]


def _get_model_type() -> str:
    cfg = conf.config or {}
    return str(cfg.get("MODEL_TYPE", "live2d") or "live2d").strip().lower()


@register
@tool
async def listVRMGesturesTool() -> str:
    """
    Description:
        获取 VRM 模型可用的手势名称列表（仅在 VRM 模式下有效）。
    Args:
        None
    Returns:
        str(json): 包含 gesture_names。
    """
    try:
        if _get_model_type() != "vrm":
            return json.dumps({"status": "error", "error": "当前不是 VRM 模式"}, ensure_ascii=False)
        names = ["nod", "shake_head", "bow", "tilt_head", "wave", "point", "thumbs_up", "peace"]
        return json.dumps({"status": "ok", "gesture_names": names, "count": len(names)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


@register
@tool
async def listVRMPosesTool() -> str:
    """
    Description:
        获取 VRM 模型已保存的动作预设名称列表（仅在 VRM 模式下有效）。
    Args:
        None
    Returns:
        str(json): 包含 pose_names。
    """
    try:
        if _get_model_type() != "vrm":
            return json.dumps({"status": "error", "error": "当前不是 VRM 模式"}, ensure_ascii=False)
        names = sorted(vrm_pose_manager.get_vrm_poses().keys())
        return json.dumps({"status": "ok", "pose_names": names, "count": len(names)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


@register
@tool
async def triggerVRMPoseTool(pose_name: str, transition: float | None = None) -> str:
    """
    Description:
        应用 VRM 模型的某个动作预设（仅在 VRM 模式下有效）。预设由用户在编辑器中保存。
        预设是持久姿态，应用后保持直到重置或切换其他动作
    Args:
        pose_name (str): 预设名称，从 listVRMPosesTool 获取，不含空格。
        transition (float, optional): 过渡时长毫秒，默认用预设自带值；0 表示瞬间。
    Returns:
        str(json): 执行状态。
    """
    name = str(pose_name or "").strip()
    if not name or vrm_pose_manager.validate_pose_name(name):
        return json.dumps({"status": "error", "error": "pose_name 不能为空且不能含空格"}, ensure_ascii=False)
    if _get_model_type() != "vrm":
        return json.dumps({"status": "error", "error": "当前不是 VRM 模式"}, ensure_ascii=False)
    if name not in vrm_pose_manager.get_vrm_poses():
        return json.dumps({"status": "error", "error": f"预设不存在: {name}"}, ensure_ascii=False)
    try:
        trans = float(transition) if transition is not None else None
        backend2frontend.frontendTriggerVRMPose(name, trans)
        return json.dumps({"status": "ok", "command": "VRM_POSE", "pose": name, "transition": trans}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


@register
@tool
async def triggerVRMGestureTool(gesture_name: str, duration: float = 1.5, auto_reset: bool = True) -> str:
    """
    Description:
        触发 VRM 模型的手势动作（仅在 VRM 模式下有效）。
    Args:
        gesture_name (str): 手势名称，从 listVRMGesturesTool 获取。
        duration (float): 手势过渡时长，默认 1.5 秒。
        auto_reset (bool): 是否自动恢复原始姿势，默认 True。
    Returns:
        str(json): 执行状态。
    """
    name = str(gesture_name or "").strip().lower()
    if not name:
        return json.dumps({"status": "error", "error": "gesture_name 不能为空"}, ensure_ascii=False)
    if _get_model_type() != "vrm":
        return json.dumps({"status": "error", "error": "当前不是 VRM 模式"}, ensure_ascii=False)
    try:
        dur = max(0.3, float(duration) if duration is not None else 1.5)
        backend2frontend.frontendTriggerVRMGesture(name, dur, auto_reset)
        return json.dumps({"status": "ok", "command": "VRM_GESTURE", "gesture": name, "duration": dur, "auto_reset": auto_reset}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


@register
@tool
async def setVRMLookAtTool(x_or_dir, y=None, z=None) -> str:
    """
    Description:
        设置 VRM 模型的视线目标方向（仅在 VRM 模式下有效）。
        可以指定世界坐标 (x, y, z) 或方向描述字符串。
    Args:
        x_or_dir: 世界 X 坐标（浮点数），或方向描述字符串。
                 方向可选值：up, down, left, right, up_left, up_right, down_left, down_right, front。
        y: 世界 Y 坐标（浮点数），使用方向字符串时留空。
        z: 世界 Z 坐标（浮点数），使用方向字符串时留空。
    Returns:
        str(json): 执行状态。
    """
    try:
        if _get_model_type() != "vrm":
            return json.dumps({"status": "error", "error": "当前不是 VRM 模式"}, ensure_ascii=False)
        if y is None and z is None:
            backend2frontend.frontendSetVRMLookAt(str(x_or_dir))
        else:
            x_val = float(x_or_dir) if x_or_dir is not None else 0
            y_val = float(y) if y is not None else 0
            z_val = float(z) if z is not None else 0
            backend2frontend.frontendSetVRMLookAt(x_val, y_val, z_val)
        return json.dumps({"status": "ok", "command": "VRM_LOOKAT"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)
