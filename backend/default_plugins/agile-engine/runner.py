"""Agile 模块加载器与运行时管理。

模块文件: ~/.faustbot/agile-modules/{name}.py（= conf.CONFIG_ROOT/agile-modules）。
加载/重载/卸载/启用/禁用由 AgileOperate 工具显式触发。

能力边界（模块只能使用这些）:
- VFS 内容节点 / 写 / 编辑 hook（可逆转：卸载时自动清除）
- interval 定时任务（每模块一个 daemon 线程 + 独立 event loop）
- event_fire 事件（经 trigger 系统唤醒 Agent）
- 日志（ALM）

失败隔离: 单个模块的加载/运行异常不会影响其它模块与插件本身。
"""
import asyncio
import importlib.util
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from lm import AgileLogManager
from agile_base import AgileContext, AgileHookBase, AgileHookType, AgileModule, build_invoker
import faust_backend.config_loader as conf
from faust_backend.plugin_system import PluginContext
from faust_backend.tools.vfs import get_faustbot_vfs

MODULES: dict[str, AgileModule] = {}
AGILE_INSTANCES: dict[str, dict[str, Any]] = {}
LM = AgileLogManager()

MODULES_DIR: Path = Path(conf.CONFIG_ROOT) / "agile-modules"

_CTX: PluginContext | None = None

# 便于测试注入的单调时钟
_monotonic = time.monotonic

# 每个模块每分钟触发 trigger 的默认上限（0 或负数 = 不限制）
DEFAULT_TPM_LIMIT = 60
TPM_WINDOW_SECONDS = 60.0


# ─────────────────────────── 基础 ───────────────────────────

def configure(ctx: PluginContext, modules_dir: Path | None = None) -> None:
    """插件 startup 调用：保存 PluginContext，初始化模块目录，注入 sys.path。"""
    global _CTX
    _CTX = ctx
    if modules_dir is not None:
        global MODULES_DIR
        MODULES_DIR = Path(modules_dir)
    MODULES_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_sys_path()


def _ensure_sys_path() -> None:
    dirname = os.path.dirname(os.path.abspath(__file__))
    if dirname not in sys.path:
        sys.path.insert(0, dirname)


def _module_file(name: str) -> Path:
    return MODULES_DIR / f"{name}.py"


def _module_file_disabled(name: str) -> Path:
    return MODULES_DIR / f"{name}.py.disabled"


def _load_module_file(name: str, file_path: Path) -> Any:
    """importlib 加载模块文件并执行，返回模块对象。"""
    _ensure_sys_path()
    sys_name = f"agile_module_{name}"
    spec = importlib.util.spec_from_file_location(sys_name, str(file_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"无法为 {file_path} 创建 import spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[sys_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(sys_name, None)
        raise
    return module


def _set_error(name: str, exc: BaseException, file_path: Path | None = None) -> None:
    inst = AGILE_INSTANCES.get(name)
    if inst is None:
        inst = {
            "name": name,
            "status": "error",
            "last_error": str(exc),
            "module": None,
            "agile": None,
            "vfs_paths": [],
            "owned_funcs": [],
            "interval_handles": [],
            "mirror_paths": [],
            "sys_name": f"agile_module_{name}",
            "file_path": file_path or _module_file(name),
            "loaded_at": 0.0,
        }
        AGILE_INSTANCES[name] = inst
    else:
        inst["status"] = "error"
        inst["last_error"] = str(exc)


# ─────────────────────────── cacheStrategy ───────────────────────────

def _wrap_cache_strategy(invoker: Callable[..., Any], strategy: str, name: str) -> Callable[..., Any]:
    """按 cacheStrategy 包装内容函数:
    - cache@N: N 秒内重复读取返回缓存内容
    - wait@N: 距上次读取不足 N 秒则等待到满 N 秒再执行
    - error@N: 距上次读取不足 N 秒则返回错误信息
    - nocache: 每次读取都执行
    """
    strategy = str(strategy or "cache@10").strip().lower()
    state: dict[str, Any] = {"last": 0.0, "cached": None, "cached_at": 0.0}

    def _n() -> float:
        try:
            return float(strategy.split("@", 1)[1])
        except Exception:
            return 10.0

    async def wrapper(path: str) -> Any:
        now = time.monotonic()
        if strategy == "nocache":
            return await invoker(path)
        if strategy.startswith("cache@"):
            n = _n()
            if state["cached_at"] and now - state["cached_at"] < n:
                return state["cached"]
            state["cached"] = await invoker(path)
            state["cached_at"] = time.monotonic()
            return state["cached"]
        if strategy.startswith("wait@"):
            n = _n()
            if state["last"] and now - state["last"] < n:
                await asyncio.sleep(n - (now - state["last"]))
            try:
                return await invoker(path)
            finally:
                state["last"] = time.monotonic()
        if strategy.startswith("error@"):
            n = _n()
            elapsed = now - state["last"] if state["last"] else 0.0
            if state["last"] and elapsed < n:
                return f"[agile:{name}] 读取过于频繁: 距上次 {elapsed:.1f}s, 需至少 {n:.0f}s 间隔"
            try:
                return await invoker(path)
            finally:
                state["last"] = time.monotonic()
        return await invoker(path)

    return wrapper


def _wrap_errors(invoker: Callable[..., Any], name: str) -> Callable[..., Any]:
    """错误隔离: hook 异常记入 ALM 并返回错误文本，不向上抛出。"""
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await invoker(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            try:
                await LM.log(name, "ERROR", f"hook 执行异常: {exc}", extra={})
            except Exception:  # noqa: BLE001
                pass
            return f"[agile:{name}] 模块 hook 执行异常: {exc}"
    return wrapper


def _stamp_activity(name: str) -> None:
    """模块活动打点：更新 last_seen（供 status 展示 idle 观测）。

    活动 = vfsContent 读取（缓存命中也算）/ vfs 写 / 编辑 handler 触发 / event_fire 成功。
    interval 轮询、onload/onunload 生命周期不算活动——否则后台轮询会让模块永远新鲜。
    """
    inst = AGILE_INSTANCES.get(name)
    if inst is not None:
        inst["last_seen"] = time.time()


def _wrap_activity(invoker: Callable[..., Any], name: str) -> Callable[..., Any]:
    """活动包装器：每次真实调用（含缓存命中路径）都打点 last_seen。"""
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        _stamp_activity(name)
        return await invoker(*args, **kwargs)
    return wrapper


# ─────────────────────────── interval 任务 ───────────────────────────

class IntervalHandle:
    """每模块 interval 一个 daemon 线程 + 独立 event loop（stop 标志控制停止）。"""

    def __init__(self, name: str, interval: float, invoker: Callable[..., Any]):
        self.name = name
        self.interval = max(1.0, float(interval))
        self.invoker = invoker
        self.stop_flag = threading.Event()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(
            target=self._run_loop, daemon=True, name=f"agile-interval-{name}"
        )
        self.thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_forever()
        finally:
            try:
                self.loop.close()
            except Exception:  # noqa: BLE001
                pass

    def start(self) -> None:
        async def _schedule() -> None:
            while not self.stop_flag.is_set():
                await asyncio.sleep(self.interval)
                try:
                    await self.invoker()
                except asyncio.CancelledError:
                    break
                except Exception as exc:  # noqa: BLE001
                    try:
                        await LM.log(self.name, "ERROR", f"interval 任务异常: {exc}", extra={})
                    except Exception:  # noqa: BLE001
                        pass
        asyncio.run_coroutine_threadsafe(_schedule(), self.loop)

    def stop(self, timeout: float = 2.0) -> None:
        self.stop_flag.set()
        try:
            async def _cancel_all() -> None:
                tasks = [
                    t for t in asyncio.all_tasks(self.loop)
                    if t is not asyncio.current_task()
                ]
                for t in tasks:
                    t.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
            fut = asyncio.run_coroutine_threadsafe(_cancel_all(), self.loop)
            try:
                fut.result(timeout=timeout)
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass
        try:
            self.loop.call_soon_threadsafe(self.loop.stop)
        except Exception:  # noqa: BLE001
            pass
        self.thread.join(timeout=timeout)


# ─────────────────────────── hooks 注册 / 逆向清理 ───────────────────────────

def _norm_vfs_path(path: str) -> str:
    """把模块注册的路径规范化为 VFS 内部路径（兼容 faustbot:// 前缀写法）。"""
    p = str(path or "").strip()
    if p.startswith("faustbot://"):
        p = "/" + p[len("faustbot://"):]
    if not p.startswith("/"):
        p = "/" + p
    return p


async def _register_hooks(agile_module: AgileModule, agile: AgileContext, name: str):
    vfs_paths: list[str] = []
    owned_funcs: list[Any] = []
    interval_handles: list[IntervalHandle] = []
    for hook in agile_module.getHooks().values():
        invoker = build_invoker(hook.func, agile)
        path = _norm_vfs_path(hook.name)
        if hook.hookType is AgileHookType.VFS_CONTENT:
            strategy = (hook.attr or {}).get("cacheStrategy", "cache@10")
            wrapped = _wrap_errors(_wrap_activity(_wrap_cache_strategy(invoker, strategy, name), name), name)
            await agile.vfs_write_symbolic(path, wrapped, writable=False,
                                           should_be_included_in_search=True)
            vfs_paths.append(path)
            owned_funcs.append(wrapped)
        elif hook.hookType is AgileHookType.VFS_WRITE:
            wrapped = _wrap_errors(_wrap_activity(invoker, name), name)
            await agile.vfs_set_write_handler(path, wrapped)
            vfs_paths.append(path)
            owned_funcs.append(wrapped)
        elif hook.hookType is AgileHookType.VFS_EDIT:
            wrapped = _wrap_errors(_wrap_activity(invoker, name), name)
            await agile.vfs_set_edit_handler(path, wrapped)
            vfs_paths.append(path)
            owned_funcs.append(wrapped)
        elif hook.hookType is AgileHookType.INTERVAL_REGISTER:
            interval = int((hook.attr or {}).get("intervalExpr", 60) or 60)
            handle = IntervalHandle(name, interval, _wrap_errors(invoker, name))
            handle.start()
            interval_handles.append(handle)
    return vfs_paths, owned_funcs, interval_handles


async def _unregister_hooks(instance: dict[str, Any]) -> None:
    name = instance["name"]
    # 1. onunload hook
    module = instance.get("module")
    agile = instance.get("agile")
    if module is not None and agile is not None:
        for hook in module.getHooks().values():
            if (hook.attr or {}).get("onunload"):
                try:
                    await _wrap_errors(build_invoker(hook.func, agile), name)()
                except Exception:  # noqa: BLE001
                    pass
    # 2. interval 停止
    for handle in instance.get("interval_handles", []):
        handle.stop()
    # 3. VFS 节点归属校验后删除
    vfs = await get_faustbot_vfs()
    owned = set(instance.get("owned_funcs", []))
    for path in list(instance.get("vfs_paths", [])):
        try:
            node = await vfs.get_node(path)
        except Exception:  # noqa: BLE001
            continue
        if node is None:
            continue
        is_owned = (
            (node.symbolic_func is not None and node.symbolic_func in owned)
            or (node.write_handler is not None and node.write_handler in owned)
            or (node.edit_handler is not None and node.edit_handler in owned)
        )
        if is_owned:
            try:
                await vfs.delete(path)
            except Exception:  # noqa: BLE001
                pass
    # 4. 框架镜像节点删除
    for path in instance.get("mirror_paths", []):
        try:
            await vfs.delete(path)
        except Exception:  # noqa: BLE001
            pass
    # 5. sys.modules 清理（下次 reload 全新 exec）
    sys.modules.pop(instance.get("sys_name", ""), None)


# ─────────────────────────── 框架镜像节点 ───────────────────────────

def _module_source(name: str) -> str:
    for f in (_module_file(name), _module_file_disabled(name)):
        if f.exists():
            try:
                return f.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:  # noqa: BLE001
                return f"(读取失败: {exc})"
    return f"(模块文件不存在: {name})"


async def _register_mirror_nodes(name: str) -> list[str]:
    vfs = await get_faustbot_vfs()
    await vfs.mkdir("/agile")
    await vfs.mkdir("/agile/modules")
    paths = [
        f"/agile/modules/{name}.py",
        f"/agile/{name}/status",
        f"/agile/{name}/log/all",
        f"/agile/{name}/log/errors",
    ]
    await vfs.write_symbolic(f"/agile/modules/{name}.py",
                             lambda _p, n=name: _module_source(n),
                             should_be_included_in_search=False, writable=False)
    await vfs.write_symbolic(f"/agile/{name}/status",
                             lambda _p, n=name: format_module_status(n),
                             should_be_included_in_search=True, writable=False)
    await vfs.write_symbolic(f"/agile/{name}/log/all",
                             lambda _p, n=name: format_module_logs(n, None),
                             should_be_included_in_search=False, writable=False)
    await vfs.write_symbolic(f"/agile/{name}/log/errors",
                             lambda _p, n=name: format_module_logs(n, "ERROR"),
                             should_be_included_in_search=False, writable=False)
    return paths


# ─────────────────────────── 触发限流（TPM） ───────────────────────────

def _limit_file(name: str) -> Path:
    return MODULES_DIR / f"{name}.limit"


def _read_persisted_limit(name: str) -> int | None:
    """读取持久化的每分钟触发上限（无文件/损坏 → None = 用默认值）。"""
    f = _limit_file(name)
    if not f.exists():
        return None
    try:
        return max(0, int(f.read_text(encoding="utf-8").strip() or 0))
    except Exception:  # noqa: BLE001
        return None


def _write_persisted_limit(name: str, limit: int) -> None:
    _limit_file(name).write_text(str(max(0, int(limit))), encoding="utf-8")


def check_trigger_limit(name: str) -> None:
    """模块触发事件前的限流检查（60 秒滑动窗口）。

    超过每分钟上限时抛 RuntimeError（由调用方隔离机制捕获并记入 log/errors）。
    limit <= 0 表示不限制。
    """
    inst = AGILE_INSTANCES.get(name)
    if inst is None:
        return
    limit = int(inst.get("tpm_limit", DEFAULT_TPM_LIMIT) or 0)
    if limit <= 0:
        return
    now = _monotonic()
    lock = inst.get("trigger_lock")
    if lock is None:
        return
    with lock:
        times = [t for t in inst.get("trigger_times", []) if now - t < TPM_WINDOW_SECONDS]
        inst["trigger_times"] = times
        if len(times) >= limit:
            raise RuntimeError(
                f"agile:{name} 触发 trigger 超过每分钟上限 {limit} 次"
                f"（当前 {TPM_WINDOW_SECONDS:.0f}s 窗口内 {len(times)} 次），"
                f"请降低触发频率或用 agileOperate(limit) 调高限制"
            )
        inst["trigger_times"].append(now)


def set_tpm_limit(name: str, limit: int) -> dict[str, Any]:
    """设置模块的每分钟触发上限（0 或负数 = 不限制），并持久化到 {name}.limit。"""
    inst = AGILE_INSTANCES.get(name)
    if inst is None:
        return {"ok": False, "message": f"模块 {name} 未加载，无法修改限制"}
    limit = max(0, int(limit))
    inst["tpm_limit"] = limit
    try:
        _write_persisted_limit(name, limit)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"写入持久化文件失败: {exc}"}
    return {"ok": True, "message": f"模块 {name} 的每分钟触发上限已设为 {limit}（已持久化）"}


# ─────────────────────────── 加载 / 卸载 / 重载 ───────────────────────────

async def _load_module_async(name: str, preset_limit: int | None = None) -> dict[str, Any]:
    f = _module_file(name)
    if not f.exists():
        return {"ok": False, "message": f"模块文件不存在: {f}"}
    if name in AGILE_INSTANCES:
        return {"ok": False, "message": f"模块 {name} 已加载，如需重新加载请用 reload"}
    try:
        module = _load_module_file(name, f)
        agile_module = module.get_agile_module()
        if not isinstance(agile_module, AgileModule):
            raise TypeError(f"{name} 的 get_agile_module() 未返回 AgileModule 实例")
        agile = AgileContext(_CTX, LM, name,
                             trigger_limiter=lambda n=name: check_trigger_limit(n),
                             on_activity=lambda n=name: _stamp_activity(n))
        vfs_paths, owned_funcs, interval_handles = await _register_hooks(agile_module, agile, name)
        mirror_paths = await _register_mirror_nodes(name)
        # 生效上限：reload 传入的 preset_limit > 磁盘持久化值 > 默认值
        if preset_limit is not None:
            effective_limit = max(0, int(preset_limit))
        else:
            persisted = _read_persisted_limit(name)
            effective_limit = DEFAULT_TPM_LIMIT if persisted is None else persisted
        instance = {
            "name": name,
            "status": "loaded",
            "last_error": None,
            "module": agile_module,
            "agile": agile,
            "vfs_paths": vfs_paths,
            "owned_funcs": owned_funcs,
            "interval_handles": interval_handles,
            "mirror_paths": mirror_paths,
            "sys_name": f"agile_module_{name}",
            "file_path": f,
            "loaded_at": time.time(),
            "last_seen": time.time(),
            "tpm_limit": effective_limit,
            "trigger_times": [],
            "trigger_lock": threading.Lock(),
        }
        # 先注册 instance，再执行 onload（onload 内的 event_fire 限流才能生效）
        AGILE_INSTANCES[name] = instance
        MODULES[name] = agile_module
        for hook in agile_module.getHooks().values():
            if (hook.attr or {}).get("onload"):
                await _wrap_errors(build_invoker(hook.func, agile), name)()
        await LM.log(name, "INFO",
                     f"模块加载成功 (vfs节点={len(vfs_paths)}, interval={len(interval_handles)})",
                     extra={})
        return {"ok": True, "message": f"模块 {name} 加载成功"}
    except Exception as exc:  # noqa: BLE001
        _set_error(name, exc, f)
        return {"ok": False, "message": f"加载模块 {name} 失败: {exc}"}


async def load_module(name: str) -> dict[str, Any]:
    name = str(name or "").strip()
    if not name:
        return {"ok": False, "message": "模块名不能为空"}
    return await _load_module_async(name)


async def unload_module(name: str) -> dict[str, Any]:
    name = str(name or "").strip()
    instance = AGILE_INSTANCES.get(name)
    if instance is None:
        return {"ok": False, "message": f"模块 {name} 未加载"}
    try:
        await _unregister_hooks(instance)
    finally:
        AGILE_INSTANCES.pop(name, None)
        MODULES.pop(name, None)
    await LM.log(name, "INFO", "模块已卸载（VFS 节点与任务已逆向清理）", extra={})
    return {"ok": True, "message": f"模块 {name} 已卸载"}


async def reload_module(name: str) -> dict[str, Any]:
    name = str(name or "").strip()
    if name in AGILE_INSTANCES:
        old_limit = AGILE_INSTANCES[name].get("tpm_limit", DEFAULT_TPM_LIMIT)
        result = await unload_module(name)
        if not result.get("ok"):
            return result
        # reload 是热更新代码：把已设置 of 触发上限传给新实例（onload 之前生效）
        return await _load_module_async(name, preset_limit=old_limit)
    return await load_module(name)


# ─────────────────────────── 启用 / 禁用（文件名持久化） ───────────────────────────

async def disable_module(name: str) -> dict[str, Any]:
    name = str(name or "").strip()
    if name in AGILE_INSTANCES:
        result = await unload_module(name)
        if not result.get("ok"):
            return result
    src, dst = _module_file(name), _module_file_disabled(name)
    if src.exists():
        src.rename(dst)
        return {"ok": True, "message": f"模块 {name} 已禁用（{dst.name}）"}
    return {"ok": False, "message": f"模块文件不存在: {src}"}


async def enable_module(name: str) -> dict[str, Any]:
    name = str(name or "").strip()
    src, dst = _module_file_disabled(name), _module_file(name)
    if src.exists():
        src.rename(dst)
    return await load_module(name)


# ─────────────────────────── 状态 / 列表 ───────────────────────────

def list_modules() -> list[dict[str, Any]]:
    if not MODULES_DIR.exists():
        return []
    out: list[dict[str, Any]] = []
    for f in sorted(MODULES_DIR.iterdir()):
        if f.is_file() and f.suffix == ".py" and not f.name.endswith(".disabled"):
            out.append({"name": f.stem, "file": f.name, "disabled": False})
        elif f.is_file() and f.name.endswith(".py.disabled"):
            out.append({"name": f.name[: -len(".py.disabled")], "file": f.name, "disabled": True})
    return out


def format_status_overview() -> str:
    lines = ["# Agile 模块总览", "", f"模块目录: {MODULES_DIR}", ""]
    items = list_modules()
    if not items:
        lines.append("(暂无模块文件)")
    for item in items:
        name = item["name"]
        inst = AGILE_INSTANCES.get(name)
        if item["disabled"]:
            lines.append(f"- {name} [disabled]")
        elif inst is not None:
            tail = f" (错误: {inst['last_error']})" if inst.get("last_error") else ""
            lines.append(f"- {name} [{inst['status']}]{tail}")
        else:
            lines.append(f"- {name} [未加载]")
    return "\n".join(lines)


def format_module_status(name: str) -> str:
    inst = AGILE_INSTANCES.get(name)
    lines = [f"# agile/{name} 状态", ""]
    if inst is None:
        f_disabled = _module_file_disabled(name)
        f_active = _module_file(name)
        if f_disabled.exists():
            lines.append("状态: disabled（文件已重命名 .py.disabled，enable 可恢复）")
        elif not f_active.exists():
            lines.append("状态: 未安装（模块文件不存在）")
        else:
            lines.append("状态: 未加载（用 agileOperate load 加载）")
        return "\n".join(lines)
    lines.append(f"状态: {inst['status']}")
    lines.append(f"加载时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(inst.get('loaded_at', 0)))}")
    last_seen = inst.get("last_seen")
    if last_seen:
        idle_secs = max(0, int(time.time() - last_seen))
        idle_txt = "刚刚" if idle_secs < 60 else (
            f"{idle_secs // 60} 分钟前" if idle_secs < 3600 else f"{idle_secs // 3600} 小时前")
        lines.append(
            f"上次活动: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last_seen))}（{idle_txt}）")
    lines.append(f"文件: {inst.get('file_path')}")
    if inst.get("last_error"):
        lines.append(f"最近错误: {inst['last_error']}")
    module = inst.get("module")
    if module is not None:
        lines.append("")
        lines.append("注册的 hooks:")
        for hook in module.getHooks().values():
            lines.append(f"- {hook.hookType or 'lifecycle'}: {hook.name} {hook.func_signature or ''}")
    lines.append("")
    lines.append(f"VFS 节点: {len(inst.get('vfs_paths', []))}  定时任务: {len(inst.get('interval_handles', []))}")
    limit = int(inst.get("tpm_limit", DEFAULT_TPM_LIMIT) or 0)
    limit_txt = "不限制" if limit <= 0 else f"{limit}/min"
    now = _monotonic()
    window = [t for t in inst.get("trigger_times", []) if now - t < TPM_WINDOW_SECONDS]
    lines.append(f"触发限制: {limit_txt}（当前 {TPM_WINDOW_SECONDS:.0f}s 窗口内已触发 {len(window)} 次）")
    return "\n".join(lines)


async def format_module_logs(name: str, level: str | None = None) -> str:
    logs = await LM.getLog(agile_from=name, level=level)
    if not logs:
        return f"(模块 {name} 暂无日志)"
    return "\n".join(await LM.formatLogs(logs))


async def register_overview_node() -> None:
    """插件 startup 调用：注册 faustbot://agile/status 总览节点。"""
    vfs = await get_faustbot_vfs()
    await vfs.mkdir("/agile")
    await vfs.write_symbolic(
        "/agile/status", lambda _p: format_status_overview(),
        should_be_included_in_search=True, writable=False,
    )
