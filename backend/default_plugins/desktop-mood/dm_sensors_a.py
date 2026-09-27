"""A 组感知源：零依赖的本机瞬时信号（窗口几何、应用停留、鼠标、未保存文档、显卡、编码活动、桌面文件）。

全部只用 ctypes / psutil / 标准库；不联网、不读屏幕内容（窗口标题类字段归黄色级）。
鼠标轨迹采样是异步任务（50ms 一次 GetCursorPos），不需要线程。
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes as wintypes
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from dm_api import SensorContext, SensorResult

try:
    import psutil
except Exception:  # noqa: BLE001
    psutil = None

SOURCES = (
    {'id': 'window_geometry', 'key': 'ENABLE_WINDOW_GEOMETRY', 'tier': 'green', 'default': True,
     'cadence': 10, 'label': '窗口几何', 'note': '活动窗口是否全屏、尺寸位置（用于判断游戏/视频全屏）',
     'fields': ('window_fullscreen', 'window_rect')},
    {'id': 'app_session', 'key': 'ENABLE_APP_SESSION', 'tier': 'green', 'default': True,
     'cadence': 10, 'label': '应用停留', 'note': '当前应用连续停留时长与近期窗口切换频率',
     'fields': ('app_session', 'context_switches_5min')},
    {'id': 'mouse_activity', 'key': 'ENABLE_MOUSE_ACTIVITY', 'tier': 'green', 'default': True,
     'cadence': 10, 'label': '鼠标活动', 'note': '鼠标速度/抖动/是否停在屏幕角落（情绪与“叫我出来”手势）',
     'fields': ('mouse',)},
    {'id': 'window_dirty', 'key': 'ENABLE_WINDOW_DIRTY', 'tier': 'yellow', 'default': True,
     'cadence': 10, 'label': '未保存文档', 'note': '从窗口标题里的 ●/* 标记推测未保存文档数',
     'fields': ('unsaved_docs',)},
    {'id': 'window_dynamic', 'key': 'ENABLE_WINDOW_DYNAMIC', 'tier': 'yellow', 'default': True,
     'cadence': 10, 'label': '画面动态', 'note': '标题在变但鼠标不动 → 用户在“看”而不是“做”',
     'fields': ('window_dynamic',)},
    {'id': 'gpu', 'key': 'ENABLE_GPU', 'tier': 'green', 'default': True,
     'cadence': 30, 'label': '显卡', 'note': 'nvidia-smi 读取占用/显存/温度（无 nvidia-smi 时报告不可用）',
     'fields': ('gpu',)},
    {'id': 'code_activity', 'key': 'ENABLE_CODE_ACTIVITY', 'tier': 'green', 'default': True,
     'cadence': 30, 'label': '编码活动', 'note': '监视目录的最近提交时间/今日提交数/近 5 分钟改动文件数（需配置 CODE_WATCH_DIR）',
     'fields': ('code_activity',)},
    {'id': 'desktop_files', 'key': 'ENABLE_DESKTOP_FILES', 'tier': 'yellow', 'default': True,
     'cadence': 30, 'label': '桌面新文件', 'note': '桌面上最近 30 分钟新增的文件名',
     'fields': ('desktop_new_files',)},
    {'id': 'process_events', 'key': 'ENABLE_PROCESS_EVENTS', 'tier': 'green', 'default': True,
     'cadence': 30, 'label': '进程增减', 'note': '进程启动/退出的变化（游戏开局、编译结束、下载完成）与 CPU 占用前三',
     'fields': ('process_events', 'top_cpu_process')},
)

FIELD_LABELS = {
    'window_fullscreen': '全屏', 'window_rect': '窗口尺寸',
    'app_session': '当前应用停留', 'context_switches_5min': '窗口切换(5分钟)',
    'mouse': '鼠标活动', 'unsaved_docs': '未保存文档', 'window_dynamic': '画面动态',
    'gpu': '显卡', 'code_activity': '编码活动', 'desktop_new_files': '桌面新文件',
    'process_events': '进程增减', 'top_cpu_process': 'CPU 占用前三',
}

MOUSE_SAMPLE_INTERVAL = 0.05
MOUSE_IDLE_STOP_SEC = 90          # 源关闭后采样任务的存活时间
CORNER_MARGIN = 6                 # 光标贴角判定像素
CORNER_HOLD_SEC = 0.8             # 贴角持续多久算一次手势
SWITCH_WINDOW_SEC = 300           # 切换频率统计窗口
DESKTOP_NEW_SEC = 1800
CODE_TOUCH_SEC = 300
DIRTY_MARKERS = ('●', '*', '✱')
SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'dist', 'build', '.next', 'target'}


# ── ctypes 小工具 ─────────────────────────────────────────────

class RECT(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_ulong), ('rcMonitor', RECT), ('rcWork', RECT), ('dwFlags', ctypes.c_ulong)]


def _foreground_rect() -> tuple[int, int, int, int] | None:
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd or user32.IsIconic(hwnd):
            return None
        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        return None


def _monitor_rect() -> tuple[int, int, int, int] | None:
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        if not monitor:
            return None
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        return info.rcMonitor.left, info.rcMonitor.top, info.rcMonitor.right, info.rcMonitor.bottom
    except Exception:
        return None


def is_fullscreen(rect: tuple[int, int, int, int] | None,
                  monitor: tuple[int, int, int, int] | None) -> bool:
    """前台窗口是否铺满所在显示器（浏览器 F11、游戏、视频全屏都命中）。"""
    if rect is None or monitor is None:
        return False
    return (abs(rect[0] - monitor[0]) <= 2 and abs(rect[1] - monitor[1]) <= 2
            and abs(rect[2] - monitor[2]) <= 2 and abs(rect[3] - monitor[3]) <= 2)


def count_dirty_documents(titles: list[str]) -> int:
    """按窗口标题里的未保存标记计数（VS Code 用 ● 前缀，记事本/Sublime 用 * 前缀或后缀）。"""
    count = 0
    for raw in titles:
        title = str(raw or '').strip()
        if not title:
            continue
        head = title.split(' - ')[0].strip()
        if title.startswith(DIRTY_MARKERS) or head.startswith(DIRTY_MARKERS):
            count += 1
        elif head.endswith('*') or title.endswith('*'):
            count += 1
    return count


def _visible_window_titles() -> list[str]:
    titles: list[str] = []
    try:
        user32 = ctypes.windll.user32
        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def _callback(hwnd, _lparam):
            try:
                if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                titles.append(buffer.value)
            except Exception:
                pass
            return True

        user32.EnumWindows(enum_proc(_callback), 0)
    except Exception:
        return titles
    return titles


async def _cursor_pos() -> tuple[int, int] | None:
    try:
        point = wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return None
        return int(point.x), int(point.y)
    except Exception:
        return None


def _screen_size() -> tuple[int, int]:
    try:
        user32 = ctypes.windll.user32
        return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
    except Exception:
        return 1920, 1080


# ── 鼠标轨迹采样（异步任务，不用线程） ───────────────────────

async def _mouse_sampler(state: dict[str, Any]) -> None:
    """每 50ms 采样一次光标，累加距离/方向变化/贴角时长，供 collect 读取后清零。"""
    last_pos: tuple[int, int] | None = None
    last_dir: tuple[int, int] | None = None
    corner: str | None = None
    corner_since = 0.0
    while not state.get('stop'):
        pos = await _cursor_pos()
        now = time.time()
        if pos is not None:
            width, height = state.get('screen') or _screen_size()
            state['screen'] = (width, height)
            if last_pos is not None:
                dx, dy = pos[0] - last_pos[0], pos[1] - last_pos[1]
                state['distance'] = state.get('distance', 0.0) + (dx * dx + dy * dy) ** 0.5
                state['samples'] = state.get('samples', 0) + 1
                direction = (dx > 0) - (dx < 0), (dy > 0) - (dy < 0)
                if last_dir is not None and direction != last_dir and (dx or dy):
                    state['turns'] = state.get('turns', 0) + 1
                last_dir = direction if (dx or dy) else last_dir
            last_pos = pos
            hit = None
            if pos[0] <= CORNER_MARGIN and pos[1] <= CORNER_MARGIN:
                hit = '左上'
            elif pos[0] >= width - CORNER_MARGIN and pos[1] <= CORNER_MARGIN:
                hit = '右上'
            elif pos[0] <= CORNER_MARGIN and pos[1] >= height - CORNER_MARGIN:
                hit = '左下'
            elif pos[0] >= width - CORNER_MARGIN and pos[1] >= height - CORNER_MARGIN:
                hit = '右下'
            if hit != corner:
                corner, corner_since = hit, now
            elif hit is not None and now - corner_since >= CORNER_HOLD_SEC:
                state['corner'] = hit
        if now - float(state.get('last_use') or now) > MOUSE_IDLE_STOP_SEC:
            state['stop'] = True
        await asyncio.sleep(MOUSE_SAMPLE_INTERVAL)


def _mouse_state(sctx: SensorContext) -> dict[str, Any]:
    state = sctx.memory.setdefault('a.mouse', {})
    state['last_use'] = sctx.now
    task = state.get('task')
    if task is None or task.done():
        state['stop'] = False
        state['task'] = asyncio.create_task(_mouse_sampler(state))
    return state


def _read_mouse(state: dict[str, Any]) -> dict[str, Any]:
    samples = int(state.get('samples') or 0)
    distance = float(state.get('distance') or 0.0)
    elapsed = max(MOUSE_SAMPLE_INTERVAL * max(samples, 1), 1.0)
    readout = {
        'velocity_px_s': round(distance / elapsed, 1) if samples else 0.0,
        'jitter': round(float(state.get('turns') or 0) / samples, 3) if samples else 0.0,
        'corner': state.get('corner'),
    }
    state['distance'] = 0.0
    state['samples'] = 0
    state['turns'] = 0
    return readout


# ── 各源采集 ─────────────────────────────────────────────────

def _collect_window_geometry(sctx: SensorContext) -> dict[str, Any]:
    rect, monitor = _foreground_rect(), _monitor_rect()
    return {
        'window_fullscreen': is_fullscreen(rect, monitor),
        'window_rect': None if rect is None else {
            'x': rect[0], 'y': rect[1], 'width': rect[2] - rect[0], 'height': rect[3] - rect[1],
        },
    }


def _collect_app_session(sctx: SensorContext) -> dict[str, Any]:
    # 用本轮正在构建的 context（基础源已采过 window_process），否则首个采样周期只能看到上一轮的空值
    process = sctx.context.get('window_process') or sctx.carry('window_process') or {}
    name = str(process.get('name') or '')
    path = str(process.get('path') or '')
    switches: list[float] = sctx.memory.setdefault('a.switches', [])
    if name and name != sctx.memory.get('a.last_process'):
        sctx.memory['a.last_process'] = name
        sctx.memory['a.session_started'] = sctx.now
        switches.append(sctx.now)
    recent = [ts for ts in switches if sctx.now - ts <= SWITCH_WINDOW_SEC]
    sctx.memory['a.switches'] = recent[-200:]
    started = float(sctx.memory.get('a.session_started') or sctx.now)
    return {
        'app_session': {
            'process': name or None,
            'path': path or None,
            'seconds': max(0, int(sctx.now - started)),
        },
        'context_switches_5min': len(recent),
    }


async def _collect_mouse(sctx: SensorContext) -> dict[str, Any]:
    return {'mouse': _read_mouse(_mouse_state(sctx))}


def _collect_window_dirty(sctx: SensorContext) -> dict[str, Any]:
    return {'unsaved_docs': count_dirty_documents(_visible_window_titles())}


def _collect_window_dynamic(sctx: SensorContext) -> dict[str, Any]:
    title = str(sctx.carry('window_title') or '')
    process = (sctx.carry('window_process') or {}).get('name')
    previous = sctx.memory.get('a.dynamic_last')
    mouse = sctx.carry('mouse') or {}
    quiet_mouse = float(mouse.get('velocity_px_s') or 0.0) < 30.0
    dynamic = bool(previous is not None and previous[0] == process and previous[1] != title and quiet_mouse)
    sctx.memory['a.dynamic_last'] = (process, title)
    return {'window_dynamic': dynamic}


async def _collect_gpu(sctx: SensorContext, executable: str | None) -> tuple[dict[str, Any], str | None]:
    if not executable:
        return {}, '未检测到 nvidia-smi'
    try:
        proc = await asyncio.create_subprocess_exec(
            executable, '--query-gpu=name,utilization.gpu,memory.used,temperature.gpu',
            '--format=csv,noheader,nounits',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8)
    except Exception as exc:  # noqa: BLE001
        return {}, f'nvidia-smi 执行失败: {exc}'
    line = stdout.decode('utf-8', errors='ignore').strip().splitlines()
    if not line:
        return {}, 'nvidia-smi 无输出'
    parts = [item.strip() for item in line[0].split(',')]
    if len(parts) < 4:
        return {}, 'nvidia-smi 输出格式未知'
    def _num(text: str) -> float | None:
        try:
            return float(text)
        except ValueError:
            return None
    return {'gpu': {'name': parts[0], 'util': _num(parts[1]), 'mem_used_mb': _num(parts[2]),
                    'temp_c': _num(parts[3])}}, None


def read_git_activity(directory: Path) -> dict[str, Any] | None:
    """读 .git 元数据（不跑 git 命令）：最近提交时间、今日提交数、近 5 分钟改动文件数。"""
    git_dir = directory / '.git'
    if not git_dir.is_dir():
        return None
    activity: dict[str, Any] = {'dir': str(directory), 'last_commit_age_min': None,
                                'commits_today': None, 'touched_files_5min': None}
    commit_msg = git_dir / 'COMMIT_EDITMSG'
    try:
        if commit_msg.exists():
            activity['last_commit_age_min'] = max(0, int((time.time() - commit_msg.stat().st_mtime) // 60))
    except OSError:
        pass
    head_log = git_dir / 'logs' / 'HEAD'
    try:
        if head_log.exists():
            with head_log.open('rb') as handle:
                handle.seek(max(0, head_log.stat().st_size - 65536))
                tail = handle.read().decode('utf-8', errors='ignore').splitlines()
            today = time.strftime('%Y-%m-%d')
            activity['commits_today'] = sum(1 for line in tail if today in line and '\tcommit' in line)
    except OSError:
        pass
    try:
        deadline = time.time() - CODE_TOUCH_SEC
        touched = 0
        scanned = 0
        for root, dirs, files in os.walk(directory):
            dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
            for name in files:
                scanned += 1
                if scanned > 4000:
                    break
                try:
                    if (Path(root) / name).stat().st_mtime >= deadline:
                        touched += 1
                except OSError:
                    continue
            if scanned > 4000:
                break
        activity['touched_files_5min'] = touched
    except OSError:
        pass
    return activity


IGNORED_PROCESSES = {
    'svchost.exe', 'conhost.exe', 'dllhost.exe', 'runtimebroker.exe', 'taskhostw.exe',
    'searchindexer.exe', 'wmiprvse.exe', 'backgroundtaskhost.exe', 'sihost.exe',
    'ctfmon.exe', 'fontdrvhost.exe', 'spoolsv.exe', 'audiodg.exe', 'crashpad_handler.exe',
}
# 这些不是"用户看得见的程序"，出现在 CPU 榜上只会让人困惑
PSEUDO_PROCESSES = {'system idle process', 'system', 'registry', 'memory compression', 'secure system'}


def scan_processes(cache: dict[int, Any]) -> tuple[set[str], list[dict[str, Any]]]:
    """返回 (当前可执行名集合, CPU 占用前三)。

    cpu_percent 需要两次采样之间才有意义，所以复用同一批 psutil.Process 对象（cache）。
    """
    names: set[str] = set()
    if psutil is None:
        return names, []
    top: list[dict[str, Any]] = []
    seen: set[int] = set()
    for pid in list(cache.keys()):
        process = cache.get(pid)
        try:
            if process is None or not process.is_running():
                cache.pop(pid, None)
                continue
            seen.add(pid)
            name = str(process.name() or '').strip()
            if name:
                names.add(name)
                top.append({'name': name, 'cpu': float(process.cpu_percent(interval=None))})
        except Exception:
            cache.pop(pid, None)
    for process in psutil.process_iter():
        try:
            if process.pid in seen:
                continue
            cache[process.pid] = process
            name = str(process.name() or '').strip()
            if name:
                names.add(name)
        except Exception:
            continue
    for pid, process in list(cache.items()):
        if pid in seen:
            continue
        try:
            name = str(process.name() or '').strip()
            if name:
                top.append({'name': name, 'cpu': float(process.cpu_percent(interval=None))})
        except Exception:
            continue
    top = [item for item in top if str(item['name']).lower() not in PSEUDO_PROCESSES]
    top.sort(key=lambda item: item['cpu'], reverse=True)
    return names, top[:3]


def diff_processes(previous: set[str], current: set[str]) -> tuple[list[str], list[str]]:
    """进程集合差分，过滤掉系统/瞬时辅助进程，只留用户能感知到的增减。"""
    def _interesting(names: set[str]) -> list[str]:
        return sorted(name for name in names if name.lower() not in IGNORED_PROCESSES)
    return _interesting(current - previous), _interesting(previous - current)


def _collect_process_events(sctx: SensorContext) -> dict[str, Any]:
    cache = sctx.memory.setdefault('a.process_cache', {})
    names, top = scan_processes(cache)
    previous = sctx.memory.get('a.processes')
    started: list[str] = []
    stopped: list[str] = []
    if isinstance(previous, set):
        started, stopped = diff_processes(previous, names)
    sctx.memory['a.processes'] = names
    return {
        'process_events': {'started': started, 'stopped': stopped, 'at': int(sctx.now)},
        'top_cpu_process': top,
    }


def _desktop_dirs() -> list[Path]:
    home = Path.home()
    return [path for path in (home / 'Desktop', home / 'OneDrive' / 'Desktop', home / '桌面') if path.is_dir()]


def scan_new_desktop_files(dirs: list[Path], now: float, limit: int = 5) -> list[dict[str, Any]]:
    found: list[tuple[float, dict[str, Any]]] = []
    for directory in dirs:
        try:
            for entry in directory.iterdir():
                if not entry.is_file():
                    continue
                modified = entry.stat().st_mtime
                if now - modified <= DESKTOP_NEW_SEC:
                    found.append((modified, {'name': entry.name, 'age_min': int((now - modified) // 60)}))
        except OSError:
            continue
    found.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in found[:limit]]


# ── 面板显示 ─────────────────────────────────────────────────

def _fmt_seconds_text(seconds: Any) -> str:
    total = int(seconds or 0)
    if total < 60:
        return f'{total} 秒'
    if total < 3600:
        return f'{total // 60} 分 {total % 60} 秒'
    return f'{total // 3600} 小时 {(total % 3600) // 60} 分'


def _fmt_session(value: Any) -> str:
    if not value:
        return '未知'
    name = str(value.get('process') or '未知进程')
    return f'{name} · 已停留 {_fmt_seconds_text(value.get("seconds"))}'


def _fmt_mouse(value: Any) -> str:
    if not value:
        return '未知'
    corner = value.get('corner')
    text = f'{float(value.get("velocity_px_s") or 0):.0f} px/s'
    jitter = float(value.get('jitter') or 0)
    if jitter:
        text += f' · 抖动 {jitter:.2f}'
    if corner:
        text += f' · 停在{corner}角'
    return text


def _fmt_gpu(value: Any) -> str:
    if not value:
        return '未知'
    name = str(value.get('name') or 'GPU')
    parts = [name]
    if value.get('util') is not None:
        parts.append(f'{float(value["util"]):.0f}%')
    if value.get('mem_used_mb') is not None:
        parts.append(f'{float(value["mem_used_mb"]):.0f} MB')
    if value.get('temp_c') is not None:
        parts.append(f'{float(value["temp_c"]):.0f}°C')
    return ' · '.join(parts)


def _fmt_code_activity(value: Any) -> str:
    if not value:
        return '未知'
    parts: list[str] = []
    if value.get('last_commit_age_min') is not None:
        parts.append(f'最近提交 {value["last_commit_age_min"]} 分钟前')
    if value.get('commits_today') is not None:
        parts.append(f'今日 {value["commits_today"]} 次提交')
    if value.get('touched_files_5min') is not None:
        parts.append(f'近 5 分钟改动 {value["touched_files_5min"]} 个文件')
    return ' · '.join(parts) or '无数据'


def _fmt_rect(value: Any) -> str:
    if not value:
        return '未知'
    return f'{value.get("width")}×{value.get("height")} @ ({value.get("x")},{value.get("y")})'


def _fmt_desktop_files(value: Any) -> str:
    if not value:
        return '无新文件'
    return '、'.join(f'{item.get("name")}（{item.get("age_min")} 分钟前）' for item in value[:3])


def _fmt_process_events(value: Any) -> str:
    if not value:
        return '无变化'
    started = value.get('started') or []
    stopped = value.get('stopped') or []
    parts: list[str] = []
    if started:
        parts.append('启动 ' + '、'.join(started[:4]))
    if stopped:
        parts.append('退出 ' + '、'.join(stopped[:4]))
    return '；'.join(parts) or '无变化'


def _fmt_top_cpu(value: Any) -> str:
    if not value:
        return '无数据'
    return '、'.join(f'{item.get("name")} {float(item.get("cpu") or 0):.0f}%' for item in value[:3])


def _fmt_bool_yes(text: str):
    return lambda value: text if value else '否'


FORMATTERS = {
    'window_fullscreen': _fmt_bool_yes('是'),
    'window_rect': _fmt_rect,
    'app_session': _fmt_session,
    'context_switches_5min': lambda value: f'{int(value or 0)} 次',
    'mouse': _fmt_mouse,
    'unsaved_docs': lambda value: f'{int(value or 0)} 个未保存',
    'window_dynamic': _fmt_bool_yes('是'),
    'gpu': _fmt_gpu,
    'code_activity': _fmt_code_activity,
    'process_events': _fmt_process_events,
    'top_cpu_process': _fmt_top_cpu,
    'desktop_new_files': _fmt_desktop_files,
}


# ── 模块入口 ─────────────────────────────────────────────────

async def collect(sctx: SensorContext) -> SensorResult:
    result = SensorResult()
    try:
        if sctx.enabled('window_geometry'):
            result.fields.update(_collect_window_geometry(sctx))
    except Exception as exc:  # noqa: BLE001
        sctx.log.warning('窗口几何采集失败: %s', exc)
        result.status['window_geometry'] = f'采集失败: {exc}'
    try:
        if sctx.enabled('app_session'):
            result.fields.update(_collect_app_session(sctx))
    except Exception as exc:  # noqa: BLE001
        sctx.log.warning('应用停留采集失败: %s', exc)
        result.status['app_session'] = f'采集失败: {exc}'
    try:
        if sctx.enabled('mouse_activity'):
            result.fields.update(await _collect_mouse(sctx))
    except Exception as exc:  # noqa: BLE001
        sctx.log.warning('鼠标活动采集失败: %s', exc)
        result.status['mouse_activity'] = f'采集失败: {exc}'
    try:
        if sctx.enabled('window_dirty'):
            result.fields.update(_collect_window_dirty(sctx))
    except Exception as exc:  # noqa: BLE001
        sctx.log.warning('未保存文档采集失败: %s', exc)
        result.status['window_dirty'] = f'采集失败: {exc}'
    try:
        if sctx.enabled('window_dynamic'):
            result.fields.update(_collect_window_dynamic(sctx))
    except Exception as exc:  # noqa: BLE001
        sctx.log.warning('画面动态采集失败: %s', exc)
        result.status['window_dynamic'] = f'采集失败: {exc}'
    if sctx.enabled('gpu'):
        gpu_fields, gpu_error = await _collect_gpu(sctx, shutil.which('nvidia-smi'))
        result.fields.update(gpu_fields)
        if gpu_error:
            result.status['gpu'] = gpu_error
    if sctx.enabled('code_activity'):
        try:
            directory = str(await sctx.config('CODE_WATCH_DIR', '') or '').strip()
            path = Path(directory) if directory else None
            if path is None or not path.is_dir():
                result.status['code_activity'] = '未配置 CODE_WATCH_DIR'
            else:
                activity = await sctx.to_thread(read_git_activity, path)
                if activity is None:
                    result.status['code_activity'] = '该目录不是 git 仓库'
                else:
                    result.fields['code_activity'] = activity
        except Exception as exc:  # noqa: BLE001
            sctx.log.warning('编码活动采集失败: %s', exc)
            result.status['code_activity'] = f'采集失败: {exc}'
    if sctx.enabled('desktop_files'):
        try:
            result.fields['desktop_new_files'] = await sctx.to_thread(scan_new_desktop_files, _desktop_dirs(), sctx.now)
        except Exception as exc:  # noqa: BLE001
            sctx.log.warning('桌面新文件采集失败: %s', exc)
            result.status['desktop_files'] = f'采集失败: {exc}'
    if sctx.enabled('process_events'):
        try:
            result.fields.update(await sctx.to_thread(_collect_process_events, sctx))
        except Exception as exc:  # noqa: BLE001
            sctx.log.warning('进程增减采集失败: %s', exc)
            result.status['process_events'] = f'采集失败: {exc}'
    return result
