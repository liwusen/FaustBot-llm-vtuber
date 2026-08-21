import json
from pathlib import Path

from langchain.tools import tool

from faust_backend.tools._registry import register
import faust_backend.config_loader as conf
import faust_backend.backend2front as backend2frontend
import faust_backend.vrm_pose_manager as vrm_pose_manager
from faust_backend.logger import get_logger

log = get_logger("faust.tools.animation")

VRM_EXPRESSIONS = ["neutral", "happy", "angry", "sad", "relaxed", "surprised"]


def _resolve_live2d_model_path() -> Path:
    cfg = conf.config or {}
    model_rel = str(cfg.get("LIVE2D_MODEL_PATH", "2D/hiyori_pro_zh/hiyori_pro_t11.model3.json") or "").strip()
    model_path = Path(model_rel)
    if model_path.is_absolute():
        return model_path
    frontend_root = Path(conf.PROJECT_ROOT).parent / "frontend"
    live2d_root = Path(conf.LIVE2D_MODEL_ROOT)
    candidates = [
        live2d_root / model_rel.replace("2D/", "", 1),
        frontend_root / model_rel,
        frontend_root / "models" / model_rel,
        Path(conf.CONFIG_ROOT) / model_rel,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _read_model_motion_names(model_path: Path) -> list[str]:
    if not model_path.exists() or not model_path.is_file():
        return []
    try:
        data = json.loads(model_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    motions = (((data or {}).get("FileReferences") or {}).get("Motions") or {})
    if not isinstance(motions, dict):
        return []
    return sorted([str(k) for k in motions.keys() if str(k).strip()])


def _read_model_expression_names(model_path: Path) -> list[str]:
    if not model_path.exists() or not model_path.is_file():
        return []
    try:
        data = json.loads(model_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    expressions = (((data or {}).get("FileReferences") or {}).get("Expressions") or [])
    if not isinstance(expressions, list):
        return []
    return sorted(
        [
            str(item.get("Name") or "").strip()
            for item in expressions
            if isinstance(item, dict) and str(item.get("Name") or "").strip()
        ]
    )


def _get_model_type() -> str:
    cfg = conf.config or {}
    return str(cfg.get("MODEL_TYPE", "live2d") or "live2d").strip().lower()


@register
@tool
async def listAvailableMotionsTool() -> str:
    """
    Description:
        获取当前模型可用的 Motion / Expression 名称列表。
        Live2D 模式列表来源于 model3.json（Motions 与 Expressions）；
        VRM 模式返回标准表情预设。
        触发方式：在正常输出文本中包含 <{MotionName}> 触发动作；
        包含 <{EXPRESSION:ExpressionName}> 触发表情（Expression）。
    Args:
        None
    Returns:
        str(json): 包含 model_type、model_path、motion_count、motions、
                   expressions、expression_tokens（EXPRESSION:XXXX 形式）。
    """
    try:
        model_type = _get_model_type()
        if model_type == "vrm":
            payload = {
                "status": "ok",
                "model_type": "vrm",
                "motion_count": len(VRM_EXPRESSIONS),
                "expressions": VRM_EXPRESSIONS,
                "expression_tokens": [f"EXPRESSION:{name}" for name in VRM_EXPRESSIONS],
                "note": "VRM standard expression presets. Trigger one by including <{ExpressionName}> or <{EXPRESSION:ExpressionName}> in the assistant output.",
            }
            log.info("VRM expressions: %s", VRM_EXPRESSIONS)
            return json.dumps(payload, ensure_ascii=False)
        model_path = _resolve_live2d_model_path()
        motions = _read_model_motion_names(model_path)
        expressions = _read_model_expression_names(model_path)
        payload = {
            "status": "ok",
            "model_type": "live2d",
            "model_path": str(model_path),
            "motion_count": len(motions),
            "motions": motions,
            "expressions": expressions,
            "expression_tokens": [f"EXPRESSION:{name}" for name in expressions],
            "note": "包含 <{MotionName}> 触发动作；包含 <{EXPRESSION:ExpressionName}> 触发表情。",
        }
        log.info("model=%s count=%d expressions=%d", model_path, len(motions), len(expressions))
        return json.dumps(payload, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)




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
