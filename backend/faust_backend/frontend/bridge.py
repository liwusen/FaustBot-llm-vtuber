import asyncio
import json
import logging
import uuid

from faust_backend.events import get_bus

logger = logging.getLogger("faust.frontend.bridge")


class FrontendBridge:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._bus = get_bus()

    def set_main_loop(self, loop: asyncio.AbstractEventLoop | None) -> None:
        self._main_loop = loop

    async def _push_async(self, command: str, payload=None):
        if payload is None:
            await self.queue.put(command)
        elif isinstance(payload, str):
            await self.queue.put(command + " " + payload)
        else:
            await self.queue.put(command + " " + json.dumps(payload, ensure_ascii=False))
        self._bus.backend2frontendQueue_event.set()

    def _push(self, command: str, payload=None):
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                asyncio.create_task(self._push_async(command, payload))
                return
        except RuntimeError:
            pass

        if self._main_loop is not None and self._main_loop.is_running():
            asyncio.run_coroutine_threadsafe(self._push_async(command, payload), self._main_loop)
            return

        logger.warning("No event loop available, command dropped: %s", command[:80])

    def say(self, text: str):
        self._push("SAY", text)

    def play_music(self, url: str):
        self._push("PLAYMUSIC", url)

    def play_bg(self, url: str):
        self._push("PLAYBG", url)

    def sing(self, payload: dict) -> None:
        self._push("SING", payload)

    def sing_stop(self) -> None:
        self._push("SINGSTOP")


    def load_model(self, path: str) -> None:
        self._push("LOAD_MODEL", str(path or ""))

    def set_model_scale(self, scale: float | int) -> None:
        self._push("SET_MODEL_SCALE", str(scale))

    def set_model_position(self, x: float | int, y: float | int) -> None:
        self._push("SET_MODEL_POSITION", f"{x} {y}")

    def set_text_chat_y_factor(self, value: float | int) -> None:
        self._push("SET_TEXT_CHAT_Y_FACTOR", str(value))

    def set_quick_controller_x_offset(self, value: float | int) -> None:
        self._push("SET_QUICK_CONTROLLER_X_OFFSET", str(value))

    def show_nimble_window(self, payload: dict):
        self._push("NIMBLE_SHOW", payload)

    def close_nimble_window(self, payload: dict) -> None:
        self._push("NIMBLE_CLOSE", payload)

    def nimble_message(self, payload: dict) -> None:
        # console write handler 可能运行在 run_coro_sync 的临时事件循环里，
        # 该循环随即关闭，create_task 会被丢弃，因此优先投递到主循环。
        if self._main_loop is not None and self._main_loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._push_async("NIMBLE_MESSAGE", payload), self._main_loop
            )
            return
        self._push("NIMBLE_MESSAGE", payload)

    def hil_approval(self, context: dict) -> None:
        self._push("HIL_APPROVAL", context)

    def markdown_block(self, content: str) -> None:
        self._push("MD_BLOCK", {"content": str(content or "")})

    async def pop_task(self) -> str:
        try:
            task = await asyncio.wait_for(self.queue.get(), timeout=0.01)
            return task
        except asyncio.TimeoutError:
            return ""

    def pop_task_sync(self) -> str:
        try:
            task = self.queue.get_nowait()
            return task
        except asyncio.QueueEmpty:
            return ""

    def has_task(self) -> bool:
        return not self.queue.empty()

    async def get_motions(self) -> str:
        self._push("GET_MOTIONS")
        self._bus.create_feedback_event(fid := "motions-fetch-" + uuid.uuid4().hex)
        return fid

    def set_motion(self, motion: dict) -> None:
        self._push("SET_MOTION", motion)

    def trigger_vrm_gesture(self, gesture_name: str, duration: float | None = None, auto_reset: bool | None = None) -> None:
        parts = [str(gesture_name)]
        if duration is not None:
            parts.append(str(duration))
            if auto_reset is not None:
                parts.append('true' if auto_reset else 'false')
        self._push("VRM_GESTURE", " ".join(parts))

    def trigger_vrm_pose(self, pose_name: str, transition: float | None = None) -> None:
        parts = [str(pose_name)]
        if transition is not None:
            parts.append(str(transition))
        self._push("VRM_POSE", " ".join(parts))

    def set_vrm_bone_rotation(self, bone_name: str, axis: str, angle_degrees: float) -> None:
        self._push("VRM_BONE_ROT", f"{bone_name} {axis} {angle_degrees}")

    def reset_vrm_pose(self) -> None:
        self._push("VRM_RESET_POSE")

    def set_vrm_look_at(self, x_or_dir, y=None, z=None) -> None:
        if y is None and z is None:
            self._push("VRM_LOOKAT", str(x_or_dir))
        else:
            self._push("VRM_LOOKAT", f"{x_or_dir} {y} {z}")

    def reload_plugin_assets(self) -> None:
        self._push("RELOAD_PLUGIN_ASSETS")
