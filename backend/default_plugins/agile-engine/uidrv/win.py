"""Windows 窗口定位 / 前台 / 客户区矩形 / 逐点归属校验（全部物理像素）。

只读与查询，不做输入注入（见 ``input.py``）。刻意**不使用 pyautogui**：

- 它的导入会作为副作用把进程切成 DPI-aware（``_pyautogui_win.py:17``），依赖导入顺序；
- 它用 ``keybd_event`` 且 scan code 恒为 0，并静默吞掉注入异常（``_pyautogui_win.py:287-401``）。

平台：仅 Windows。非 win32 下 import 本模块是安全的（ctypes 层惰性加载），
但任何真实调用都抛 :class:`NotWindowsError`——与设计 §19「非 Windows 调用即报错」一致。
"""
from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from typing import Any, Iterable

IS_WINDOWS = sys.platform == "win32"

# ── Win32 常量 ────────────────────────────────────────────────
GA_ROOT = 2
SM_CXSCREEN = 0
SM_CYSCREEN = 1
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
MONITOR_DEFAULTTONEAREST = 2
SW_RESTORE = 9
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class UIDrvError(RuntimeError):
    """LimitedUI 运行期错误基类（窗口/注入/眨眼）。"""


class NotWindowsError(UIDrvError):
    pass


class WindowNotFoundError(UIDrvError):
    pass


@dataclass(frozen=True)
class TargetSpec:
    """目标窗口绑定条件：``exe`` 与 ``title_contains`` 必须同时匹配（防标题伪装）。"""

    exe: str
    title_contains: str | None = None
    require_fullscreen: bool = False


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    exe: str
    pid: int
    path: str
    client_rect: tuple[int, int, int, int]  # (left, top, right, bottom) 屏幕物理像素

    def describe(self) -> str:
        l, t, r, b = self.client_rect
        return (f"{self.title} [{self.exe} pid={self.pid}] "
                f"客户区 {l},{t}-{r},{b} ({r - l}x{b - t}) 路径={self.path}")


# ── ctypes 结构 ───────────────────────────────────────────────
if IS_WINDOWS:
    from ctypes import wintypes

    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                    ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
else:  # pragma: no cover - 非 Windows 只保证 import 安全
    RECT = POINT = MONITORINFO = None  # type: ignore[assignment]
    WNDENUMPROC = None  # type: ignore[assignment]


_user32: Any = None
_kernel32: Any = None


def _lib() -> tuple[Any, Any]:
    """惰性加载 user32/kernel32 并配置函数原型（一次）。"""
    if not IS_WINDOWS:
        raise NotWindowsError("LimitedUI 仅支持 Windows（当前平台 %s）" % sys.platform)
    global _user32, _kernel32
    if _user32 is not None:
        return _user32, _kernel32
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u32.GetForegroundWindow.restype = wintypes.HWND
    u32.WindowFromPoint.argtypes = [POINT]
    u32.WindowFromPoint.restype = wintypes.HWND
    u32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    u32.GetAncestor.restype = wintypes.HWND
    u32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    u32.GetClientRect.restype = wintypes.BOOL
    u32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
    u32.ClientToScreen.restype = wintypes.BOOL
    u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u32.IsWindow.argtypes = [wintypes.HWND]
    u32.IsWindowVisible.argtypes = [wintypes.HWND]
    u32.IsIconic.argtypes = [wintypes.HWND]
    u32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    u32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    u32.MonitorFromWindow.restype = wintypes.HANDLE
    u32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
    u32.GetMonitorInfoW.restype = wintypes.BOOL
    u32.SetForegroundWindow.argtypes = [wintypes.HWND]
    u32.SetForegroundWindow.restype = wintypes.BOOL
    u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u32.GetWindowThreadProcessId.restype = wintypes.DWORD
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                               wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _user32, _kernel32 = u32, k32
    return u32, k32


def ensure_dpi_aware() -> None:
    """显式把进程切成 DPI-aware（幂等）。

    否则 ``.runtime`` 进程默认 DPI-unaware，``GetSystemMetrics(SM_CXSCREEN)`` 在 1.5 倍缩放的
    屏幕上会返回 1707×1067（物理 2560×1600），截图/客户区/点击坐标全部错位。
    必须在任何坐标计算之前调用；``win.py`` 导入时与 ``limited_ui()`` 打开会话时都会调一次。
    """
    if not IS_WINDOWS:
        return
    try:
        _lib()
    except UIDrvError:
        return
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001 - 已被清单/其它调用设为 per-monitor 时失败是正常的
        pass


if IS_WINDOWS:  # 导入即对齐 DPI，不依赖 pyautogui 的导入顺序
    ensure_dpi_aware()


# ── 后端实现 ──────────────────────────────────────────────────
class WinBackend:
    """窗口层接口（测试用 FakeWin 替换，见 tests/test_limited_ui.py）。"""

    def resolve(self, target: TargetSpec) -> WindowInfo:  # pragma: no cover - 接口
        raise NotImplementedError

    def client_rect(self, hwnd: int) -> tuple[int, int, int, int]:  # pragma: no cover
        raise NotImplementedError

    def foreground_hwnd(self) -> int:  # pragma: no cover
        raise NotImplementedError

    def root_window_at(self, x: int, y: int) -> int:  # pragma: no cover
        raise NotImplementedError

    def is_window(self, hwnd: int) -> bool:  # pragma: no cover
        raise NotImplementedError

    def is_minimized(self, hwnd: int) -> bool:  # pragma: no cover
        raise NotImplementedError

    def set_foreground(self, hwnd: int) -> bool:  # pragma: no cover
        raise NotImplementedError

    def screen_size(self) -> tuple[int, int]:  # pragma: no cover
        raise NotImplementedError

    def monitor_rect(self, hwnd: int) -> tuple[int, int, int, int]:  # pragma: no cover
        raise NotImplementedError


class Win32Backend(WinBackend):
    """真实实现：全部走 ctypes，坐标一律物理像素。"""

    def _win_title(self, hwnd: int) -> str:
        u32, _ = _lib()
        n = int(u32.GetWindowTextLengthW(hwnd))
        if n <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(n + 1)
        u32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def _process_image(self, pid: int) -> str:
        _, k32 = _lib()
        if not pid:
            return ""
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return ""
        try:
            size = wintypes.DWORD(32768)
            buf = ctypes.create_unicode_buffer(size.value)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return buf.value
            return ""
        finally:
            k32.CloseHandle(h)

    def _pid_of(self, hwnd: int) -> int:
        u32, _ = _lib()
        pid = wintypes.DWORD(0)
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def iter_windows(self) -> Iterable[tuple[int, str, str, int, str]]:
        """枚举所有可见顶层窗口 → (hwnd, title, exe, pid, path)。"""
        u32, _ = _lib()
        out: list[tuple[int, str, str, int, str]] = []

        def _cb(hwnd: int, _lparam: int) -> bool:
            if not u32.IsWindowVisible(hwnd):
                return True
            title = self._win_title(hwnd)
            if not title:
                return True
            pid = self._pid_of(hwnd)
            path = self._process_image(pid)
            out.append((int(hwnd), title, os.path.basename(path).lower(), pid, path))
            return True

        u32.EnumWindows(WNDENUMPROC(_cb), 0)
        return out

    @staticmethod
    def _norm_exe(value: str) -> str:
        v = str(value or "").strip().lower()
        if v and not v.endswith(".exe"):
            v += ".exe"
        return v

    def resolve(self, target: TargetSpec) -> WindowInfo:
        want_exe = self._norm_exe(target.exe)
        if not want_exe:
            raise WindowNotFoundError("目标窗口未指定 exe")
        want_title = (target.title_contains or "").strip().lower()
        fg = self.foreground_hwnd()
        matches: list[tuple[int, str, str, int, str]] = []
        for hwnd, title, exe, pid, path in self.iter_windows():
            if exe != want_exe:
                continue
            if want_title and want_title not in title.lower():
                continue
            matches.append((hwnd, title, exe, pid, path))
        if not matches:
            raise WindowNotFoundError(
                f"找不到目标窗口 (exe={target.exe}, title_contains={target.title_contains!r})；"
                "窗口可能未启动、已被最小化到托盘或标题不含关键词"
            )
        # 选择顺序：前台的那个 > 第一个（EnumWindows 按 z-order，最上层优先）
        chosen = next((m for m in matches if m[0] == fg), matches[0])
        hwnd, title, exe, pid, path = chosen
        info = WindowInfo(hwnd=hwnd, title=title, exe=exe, pid=pid, path=path,
                          client_rect=self.client_rect(hwnd))
        if target.require_fullscreen and not self.covers_monitor(info.client_rect, hwnd):
            raise WindowNotFoundError(
                f"目标窗口 {title!r} 未覆盖所在显示器（require_fullscreen=True）：客户区 {info.client_rect}"
            )
        return info

    def client_rect(self, hwnd: int) -> tuple[int, int, int, int]:
        u32, _ = _lib()
        rc = RECT()
        if not u32.GetClientRect(hwnd, ctypes.byref(rc)):
            raise WindowNotFoundError(f"GetClientRect 失败 (hwnd={hwnd}, err={ctypes.get_last_error()})")
        origin = POINT(0, 0)
        if not u32.ClientToScreen(hwnd, ctypes.byref(origin)):
            raise WindowNotFoundError(f"ClientToScreen 失败 (hwnd={hwnd}, err={ctypes.get_last_error()})")
        w, h = rc.right - rc.left, rc.bottom - rc.top
        return (int(origin.x), int(origin.y), int(origin.x + w), int(origin.y + h))

    def foreground_hwnd(self) -> int:
        u32, _ = _lib()
        return int(u32.GetForegroundWindow() or 0)

    def root_window_at(self, x: int, y: int) -> int:
        u32, _ = _lib()
        hwnd = u32.WindowFromPoint(POINT(int(x), int(y)))
        if not hwnd:
            return 0
        return int(u32.GetAncestor(hwnd, GA_ROOT) or 0)

    def is_window(self, hwnd: int) -> bool:
        u32, _ = _lib()
        return bool(u32.IsWindow(hwnd))

    def is_minimized(self, hwnd: int) -> bool:
        u32, _ = _lib()
        return bool(u32.IsIconic(hwnd))

    def set_foreground(self, hwnd: int) -> bool:
        """把窗口切到前台。

        ``SetForegroundWindow`` 在"调用方不是前台进程"时会被系统直接拒绝（静默返回 False），
        所以失败后按文档允许的手段再试一次：AttachThreadInput 绑定前台线程 + BringWindowToTop +
        SetFocus。仍然失败就返回 False（调用方据此提示用户自己点一下，不假装成功）。
        """
        u32, _ = _lib()
        if self.is_minimized(hwnd):
            u32.ShowWindow(hwnd, SW_RESTORE)
        if u32.SetForegroundWindow(hwnd):
            return True
        fg = int(u32.GetForegroundWindow() or 0)
        fg_thread = int(u32.GetWindowThreadProcessId(fg, None)) if fg else 0
        target_thread = int(u32.GetWindowThreadProcessId(hwnd, None))
        attached = False
        try:
            if fg_thread and target_thread and fg_thread != target_thread:
                attached = bool(u32.AttachThreadInput(fg_thread, target_thread, True))
            u32.BringWindowToTop(hwnd)
            u32.SetFocus(hwnd)
            if u32.SetForegroundWindow(hwnd):
                return True
        finally:
            if attached:
                try:
                    u32.AttachThreadInput(fg_thread, target_thread, False)
                except Exception:  # noqa: BLE001
                    pass
        return self.foreground_hwnd() == int(hwnd)

    def screen_size(self) -> tuple[int, int]:
        u32, _ = _lib()
        return int(u32.GetSystemMetrics(SM_CXSCREEN)), int(u32.GetSystemMetrics(SM_CYSCREEN))

    def virtual_screen(self) -> tuple[int, int, int, int]:
        """虚屏原点与尺寸（多显示器）→ (x, y, w, h)。"""
        u32, _ = _lib()
        return (int(u32.GetSystemMetrics(SM_XVIRTUALSCREEN)),
                int(u32.GetSystemMetrics(SM_YVIRTUALSCREEN)),
                int(u32.GetSystemMetrics(SM_CXVIRTUALSCREEN)),
                int(u32.GetSystemMetrics(SM_CYVIRTUALSCREEN)))

    def monitor_rect(self, hwnd: int) -> tuple[int, int, int, int]:
        u32, _ = _lib()
        mon = u32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        if not mon:
            raise WindowNotFoundError(f"MonitorFromWindow 失败 (hwnd={hwnd})")
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if not u32.GetMonitorInfoW(mon, ctypes.byref(mi)):
            raise WindowNotFoundError(f"GetMonitorInfoW 失败 (hwnd={hwnd})")
        return (int(mi.rcMonitor.left), int(mi.rcMonitor.top),
                int(mi.rcMonitor.right), int(mi.rcMonitor.bottom))

    def covers_monitor(self, rect: tuple[int, int, int, int], hwnd: int, tol: int = 2) -> bool:
        ml, mt, mr, mb = self.monitor_rect(hwnd)
        l, t, r, b = rect
        return l <= ml + tol and t <= mt + tol and r >= mr - tol and b >= mb - tol


_backend: WinBackend | None = None


def get_win_backend() -> WinBackend:
    """真实窗口后端单例（测试请自行构造假后端，不要走这里）。"""
    global _backend
    if _backend is None:
        _backend = Win32Backend()
    return _backend
