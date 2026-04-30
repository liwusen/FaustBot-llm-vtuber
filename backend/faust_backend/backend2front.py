import asyncio
import json
import logging

try:
    import faust_backend.events as events
except ImportError:
    import events
import uuid

logger = logging.getLogger("faust.backend2front")

FrontEndTaskQueue = asyncio.Queue()

# 保存主事件循环引用，用于 run_coroutine_threadsafe
_main_loop: asyncio.AbstractEventLoop | None = None


def set_main_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    """让 backend-main 注册主事件循环，供同步线程推送到队列。"""
    global _main_loop
    _main_loop = loop


async def _push_command_async(command: str, payload=None):
    """Async version of push command for asyncio Queue."""
    if payload is None:
        await FrontEndTaskQueue.put(command)
    elif isinstance(payload, str):
        await FrontEndTaskQueue.put(command + " " + payload)
    else:
        await FrontEndTaskQueue.put(command + " " + json.dumps(payload, ensure_ascii=False))
    events.backend2frontendQueue_event.set()


def _push_command(command: str, payload=None):
    """Push a command to the frontend queue from sync or async context.

    优先使用 asyncio.create_task；若当前无事件循环（如 to_thread 线程），
    则通过 run_coroutine_threadsafe 委托给主事件循环。
    """
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            asyncio.create_task(_push_command_async(command, payload))
            return
    except RuntimeError:
        pass

    # 没有 running loop → 回退到主事件循环
    if _main_loop is not None and _main_loop.is_running():
        asyncio.run_coroutine_threadsafe(_push_command_async(command, payload), _main_loop)
        return

    logger.warning("没有可用的事件循环来投递前端命令，命令已丢弃: %s", command[:80])


def FrontEndSay(text):
    _push_command("SAY", text)


def FrontEndPlayMusic(url):
    _push_command("PLAYMUSIC", url)


def FrontEndPlayBG(url):
    _push_command("PLAYBG", url)


def FrontEndLoadModel(path: str) -> None:
    _push_command("LOAD_MODEL", str(path or ""))


def FrontEndSetModelScale(scale: float | int) -> None:
    _push_command("SET_MODEL_SCALE", str(scale))


def FrontEndSetModelPosition(x: float | int, y: float | int) -> None:
    _push_command("SET_MODEL_POSITION", f"{x} {y}")


def FrontEndSetTextChatYFactor(value: float | int) -> None:
    _push_command("SET_TEXT_CHAT_Y_FACTOR", str(value))


def FrontEndSetQuickControllerXOffset(value: float | int) -> None:
    _push_command("SET_QUICK_CONTROLLER_X_OFFSET", str(value))


def FrontEndShowNimbleWindow(payload: dict):
    """Send a nimble window payload to the frontend.

    payload example:
    {
      "callback_id": "nimble_xxx",
      "title": "安装确认",
      "html": "<div>...</div>",
      "lifespan": 600,
      "expires_at": 1234567890.0,
      "metadata": {...}
    }
    """
    _push_command("NIMBLE_SHOW", payload)


def FrontEndCloseNimbleWindow(payload: dict)->None:
    _push_command("NIMBLE_CLOSE", payload)


async def popFrontEndTask()->str:
    """Async version to properly handle asyncio Queue."""
    try:
        task = await asyncio.wait_for(FrontEndTaskQueue.get(), timeout=0.01)
        return task
    except asyncio.TimeoutError:
        return ""

def popFrontEndTask_sync()->str:
    """Sync wrapper for backward compatibility (deprecated)."""
    try:
        task = FrontEndTaskQueue.get_nowait()
        return task
    except asyncio.QueueEmpty:
        return ""
def FrontendHIL(context:dict)->None:
    """Handles approval requests from the human-in-the-loop system.

    Args:
        text (str): The approval request text.
        Example text:
        {"request_id": "<uuid>", 
        "title": "Do you approve the action to delete all files?",
        "summary": "sudo rm -rf / --no-preserve-root"}
    """
    _push_command("HIL_APPROVAL", context)
def hasFrontEndTask():
    """Check if queue has items (non-blocking)."""
    return not FrontEndTaskQueue.empty()

async def frontendGetMotions()->str:
    """Fetches the motions of the model from the frontend.

    Returns:
        fid: A unique feedback ID that can be used to wait for the motion data to be returned from the frontend.
    """
    _push_command("GET_MOTIONS")
    events.create_feedback_event(fid:="motions-fetch-"+uuid.uuid4().hex)
    return fid
def frontendSetMotion(motion:dict)->None:
    """Sends motion data to the frontend.

    Args:
        motion (dict): A dictionary containing the motion data to be sent to the frontend.
    """
    _push_command("SET_MOTION", motion)

def frontendTriggerVRMGesture(gesture_name: str, duration: float | None = None, auto_reset: bool | None = None) -> None:
    """Triggers a VRM gesture by name.

    Args:
        gesture_name: Gesture name (nod, wave, point, etc.)
        duration: Optional duration in seconds.
        auto_reset: Whether to auto-reset after gesture completes.
    """
    parts = [str(gesture_name)]
    if duration is not None:
        parts.append(str(duration))
        if auto_reset is not None:
            parts.append('true' if auto_reset else 'false')
    _push_command("VRM_GESTURE", " ".join(parts))

def frontendSetVRMBoneRotation(bone_name: str, axis: str, angle_degrees: float) -> None:
    """Sets a single VRM bone rotation.

    Args:
        bone_name: VRM humanoid bone name (e.g. head, rightUpperArm).
        axis: Rotation axis (x, y, or z).
        angle_degrees: Rotation angle in degrees.
    """
    _push_command("VRM_BONE_ROT", f"{bone_name} {axis} {angle_degrees}")

def frontendResetVRMPose() -> None:
    """Resets all VRM bone rotations to rest pose."""
    _push_command("VRM_RESET_POSE")

def frontendSetVRMLookAt(x_or_dir, y=None, z=None) -> None:
    """Sets VRM look-at target.

    Args:
        x_or_dir: X coordinate (world space) or direction string (up/down/left/right).
        y: Y coordinate, omit if using direction string.
        z: Z coordinate, omit if using direction string.
    """
    if y is None and z is None:
        _push_command("VRM_LOOKAT", str(x_or_dir))
    else:
        _push_command("VRM_LOOKAT", f"{x_or_dir} {y} {z}")

async def demo():
    print(fid:=await frontendGetMotions())
    print(events.feedback_event_pool)
if __name__ == "__main__":
    # Example usage
    print(asyncio.run(demo()))