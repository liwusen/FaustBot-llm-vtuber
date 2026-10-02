"""SendInput 注入层（按键 = 真实 scan code；点击 = 绝对坐标）。

刻意**不用 pyautogui**：

- 它的 ``_mouseDown/_mouseUp`` 会静默吞掉 ``PermissionError/OSError``
  （``_pyautogui_win.py:397-401`` / ``:426-431``）⇒ 目标以管理员运行时点击会无声失效；
- 它的 ``keybd_event`` scan code 恒为 0（``_pyautogui_win.py:287-328``）⇒ 读 scancode 的引擎会忽略。

本层所有失败都上抛 :class:`InputInjectError`（设计 §19：注入报错必须上抛，不学 pyautogui 静默吞）。
"""
from __future__ import annotations

import asyncio
import ctypes
from typing import Any

from .win import IS_WINDOWS, UIDrvError, _lib

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

MAPVK_VK_TO_VSC_EX = 4

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12


class InputInjectError(UIDrvError):
    """SendInput / 键名解析失败。"""


# ── 键名 → 虚拟键码 ───────────────────────────────────────────
_NAMED_VK: dict[str, int] = {
    "space": 0x20, "esc": 0x1B, "escape": 0x1B, "enter": 0x0D, "return": 0x0D,
    "tab": 0x09, "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "shift": VK_SHIFT, "lshift": 0xA0, "rshift": 0xA1,
    "ctrl": VK_CONTROL, "control": VK_CONTROL, "lctrl": 0xA2, "rctrl": 0xA3,
    "alt": VK_MENU, "lalt": 0xA4, "ralt": 0xA5,
    "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C, "apps": 0x5D,
    "capslock": 0x14, "numlock": 0x90, "scrolllock": 0x91,
    "printscreen": 0x2C, "pause": 0x13,
    "minus": 0xBD, "equal": 0xBB, "comma": 0xBC, "period": 0xBE,
    "slash": 0xBF, "backslash": 0xDC, "semicolon": 0xBA, "quote": 0xDE,
    "lbracket": 0xDB, "rbracket": 0xDD, "grave": 0xC0,
    "multiply": 0x6A, "add": 0x6B, "subtract": 0x6D, "decimal": 0x6E, "divide": 0x6F,
}
for _i in range(24):
    _NAMED_VK[f"f{_i + 1}"] = 0x70 + _i
for _i in range(10):
    _NAMED_VK[f"num{_i}"] = 0x60 + _i


def resolve_key(name: str) -> int:
    """键名 → VK 码。支持单字符（字母/数字）与常用命名键。"""
    key = str(name or "").strip()
    if not key:
        raise InputInjectError("按键名为空")
    low = key.lower()
    if low in _NAMED_VK:
        return _NAMED_VK[low]
    if len(key) == 1:
        ch = key.upper()
        if "A" <= ch <= "Z" or "0" <= ch <= "9":
            return ord(ch)
    raise InputInjectError(
        f"无法识别的按键名 {name!r}；请用单字符（'1'/'a'）或命名键（space/esc/enter/up/f5…）"
    )


if IS_WINDOWS:
    from ctypes import wintypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_void_p)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                    ("wParamH", wintypes.WORD)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    class WINDOWSPOINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

else:  # pragma: no cover - 非 Windows 只保证 import 安全
    INPUT = None  # type: ignore[assignment]
    WINDOWSPOINT = None  # type: ignore[assignment]


def _send(inputs: list[Any]) -> None:
    """一次性投递一组输入事件；被系统丢弃（UIPI/无权限）时抛错。"""
    if not inputs:
        return
    u32, _ = _lib()
    arr = (INPUT * len(inputs))(*inputs)
    sent = int(u32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT)))
    if sent != len(inputs):
        err = ctypes.get_last_error()
        raise InputInjectError(
            f"SendInput 只投递了 {sent}/{len(inputs)} 个事件（GetLastError={err}）。"
            "常见原因：目标窗口属于更高完整性级别（管理员）的进程，被 UIPI 拦截。"
        )


def _key_input(vk: int, scan: int, extended: bool, keyup: bool) -> Any:
    flags = KEYEVENTF_SCANCODE
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    if keyup:
        flags |= KEYEVENTF_KEYUP
    return INPUT(type=INPUT_KEYBOARD,
                 ki=KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=None))


def _scan_of(vk: int) -> tuple[int, bool]:
    """VK → (scan code, 是否扩展键)。扩展键的 0xE0 在高字节返回（MAPVK_VK_TO_VSC_EX）。"""
    u32, _ = _lib()
    raw = int(u32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC_EX))
    scan = raw & 0xFF
    extended = ((raw >> 8) & 0xFF) == 0xE0
    if scan == 0:
        raise InputInjectError(f"VK 0x{vk:02X} 没有可用的 scan code，无法用 KEYEVENTF_SCANCODE 注入")
    return scan, extended


class InputBackend:
    """注入接口。测试用 FakeInput 替换；``key_tap`` 由本类实现（按住 hold_ms 再抬起）。"""

    def __init__(self, sleep: Any = None) -> None:
        self._sleep = sleep or asyncio.sleep

    # ── 子类实现 ──
    def key_down(self, key: str) -> None:  # pragma: no cover - 接口
        raise NotImplementedError

    def key_up(self, key: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def click(self, x: int, y: int) -> None:  # pragma: no cover
        raise NotImplementedError

    def release_all(self) -> list[str]:  # pragma: no cover
        raise NotImplementedError

    def pressed(self) -> list[str]:  # pragma: no cover
        raise NotImplementedError

    def cursor_pos(self) -> tuple[int, int]:  # pragma: no cover - 接口
        raise NotImplementedError

    async def key_tap(self, key: str, hold_ms: int = 30) -> None:
        """按下 → 停 hold_ms → 抬起。**抬起一定发生在 finally 里**：
        模块卸载时 interval 任务会被取消，若在 hold 中途被打断，物理键会卡住。"""
        self.key_down(key)
        try:
            if hold_ms > 0:
                await self._sleep(hold_ms / 1000.0)
        finally:
            self.key_up(key)


class SendInputBackend(InputBackend):
    """真实注入：按键走 scan code，点击走绝对坐标（虚屏归一化到 0–65535）。"""

    def __init__(self, sleep: Any = None, screen_rect: Any = None) -> None:
        super().__init__(sleep)
        self._screen_rect = screen_rect  # callable -> (x, y, w, h)；测试可注入
        self._down: dict[str, int] = {}   # key → vk（记录"我们按住的键"，供急停松手）

    def key_down(self, key: str) -> None:
        vk = resolve_key(key)
        scan, ext = _scan_of(vk)
        _send([_key_input(vk, scan, ext, keyup=False)])
        self._down[key] = vk

    def key_up(self, key: str) -> None:
        vk = self._down.pop(key, None)
        if vk is None:
            vk = resolve_key(key)
        scan, ext = _scan_of(vk)
        _send([_key_input(vk, scan, ext, keyup=True)])

    def _screen(self) -> tuple[int, int, int, int]:
        if self._screen_rect is not None:
            return self._screen_rect()
        from .win import get_win_backend
        return get_win_backend().virtual_screen()

    def click(self, x: int, y: int) -> None:
        vx, vy, vw, vh = self._screen()
        if vw <= 1 or vh <= 1:
            raise InputInjectError(f"虚屏尺寸异常: {vw}x{vh}，拒绝点击")
        nx = max(0, min(65535, int(round((int(x) - vx) * 65535 / (vw - 1)))))
        ny = max(0, min(65535, int(round((int(y) - vy) * 65535 / (vh - 1)))))
        move = INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(
            dx=nx, dy=ny, mouseData=0,
            dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
            time=0, dwExtraInfo=None))
        down = INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(
            dx=0, dy=0, mouseData=0, dwFlags=MOUSEEVENTF_LEFTDOWN, time=0, dwExtraInfo=None))
        up = INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(
            dx=0, dy=0, mouseData=0, dwFlags=MOUSEEVENTF_LEFTUP, time=0, dwExtraInfo=None))
        _send([move, down, up])

    def pressed(self) -> list[str]:
        return list(self._down)

    def cursor_pos(self) -> tuple[int, int]:
        return cursor_pos()

    def release_all(self) -> list[str]:
        """把本次按下的键逐个抬起（键位枚举顺序）；返回已抬起的键名。"""
        released = list(self._down)
        for key in released:
            vk = self._down.pop(key)
            try:
                scan, ext = _scan_of(vk)
                _send([_key_input(vk, scan, ext, keyup=True)])
            except InputInjectError:  # 松手失败不应掩盖真正的错误
                pass
        return released


def cursor_pos() -> tuple[int, int]:
    """当前光标屏幕物理像素位置（角落急停用）。"""
    u32, _ = _lib()
    pt = WINDOWSPOINT()
    if not u32.GetCursorPos(ctypes.byref(pt)):
        raise InputInjectError(f"GetCursorPos 失败 (err={ctypes.get_last_error()})")
    return int(pt.x), int(pt.y)


if IS_WINDOWS:
    _u32, _ = _lib()
    _u32.GetCursorPos.argtypes = [ctypes.POINTER(WINDOWSPOINT)]
    _u32.GetCursorPos.restype = ctypes.c_int


_backend: InputBackend | None = None


def get_input_backend() -> InputBackend:
    """真实注入后端单例（测试请自行构造假后端）。"""
    global _backend
    if _backend is None:
        _backend = SendInputBackend()
    return _backend
