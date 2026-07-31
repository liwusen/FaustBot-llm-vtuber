"""
桥接模块: 前端通信委托给 frontend/ 包中的 FrontendBridge。
原有消费者无需修改。
"""
import asyncio
from faust_backend.frontend import get_bridge

_bridge = get_bridge()

FrontEndTaskQueue: asyncio.Queue = _bridge.queue
_main_loop: asyncio.AbstractEventLoop | None = None


def set_main_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    global _main_loop
    _main_loop = loop
    _bridge.set_main_loop(loop)


def FrontEndSay(text):
    _bridge.say(text)


def FrontEndPlayMusic(url):
    _bridge.play_music(url)


def FrontEndPlayBG(url):
    _bridge.play_bg(url)


def FrontEndLoadModel(path: str) -> None:
    _bridge.load_model(path)


def FrontEndSetModelScale(scale: float | int) -> None:
    _bridge.set_model_scale(scale)


def FrontEndSetModelPosition(x: float | int, y: float | int) -> None:
    _bridge.set_model_position(x, y)


def FrontEndSetTextChatYFactor(value: float | int) -> None:
    _bridge.set_text_chat_y_factor(value)


def FrontEndSetQuickControllerXOffset(value: float | int) -> None:
    _bridge.set_quick_controller_x_offset(value)


def FrontEndShowNimbleWindow(payload: dict):
    _bridge.show_nimble_window(payload)


def FrontEndCloseNimbleWindow(payload: dict) -> None:
    _bridge.close_nimble_window(payload)


def FrontEndNimbleMessage(payload: dict) -> None:
    _bridge.nimble_message(payload)


def FrontendHIL(context: dict) -> None:
    _bridge.hil_approval(context)


def FrontEndMarkdownBlock(content: str) -> None:
    _bridge.markdown_block(content)


async def popFrontEndTask() -> str:
    return await _bridge.pop_task()


def popFrontEndTask_sync() -> str:
    return _bridge.pop_task_sync()


def hasFrontEndTask() -> bool:
    return _bridge.has_task()


async def frontendGetMotions() -> str:
    return await _bridge.get_motions()


def frontendSetMotion(motion: dict) -> None:
    _bridge.set_motion(motion)


def frontendTriggerVRMGesture(gesture_name: str, duration: float | None = None, auto_reset: bool | None = None) -> None:
    _bridge.trigger_vrm_gesture(gesture_name, duration, auto_reset)


def frontendSetVRMBoneRotation(bone_name: str, axis: str, angle_degrees: float) -> None:
    _bridge.set_vrm_bone_rotation(bone_name, axis, angle_degrees)


def frontendResetVRMPose() -> None:
    _bridge.reset_vrm_pose()


def frontendSetVRMLookAt(x_or_dir, y=None, z=None) -> None:
    _bridge.set_vrm_look_at(x_or_dir, y, z)


def FrontEndReloadPluginAssets() -> None:
    _bridge.reload_plugin_assets()
