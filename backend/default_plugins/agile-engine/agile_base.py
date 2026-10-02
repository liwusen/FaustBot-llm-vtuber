from typing import Callable, Any, Optional
from faust_backend.plugin_system import PluginContext
from pydantic import BaseModel, Field
from dataclasses import dataclass
from abc import ABC, abstractmethod
from lm import AgileLogManager
from enum import Enum
import inspect
import json
import threading
from pathlib import Path


class AgileStorage:
    """模块级 KV 持久存储(单 JSON 文件:plugin_data/agile-engine/<模块名>.json)。

    并发策略:同步实现 + threading.RLock。Agile 的调用方横跨主事件循环的
    同步/异步 hook 与 interval 独立线程,同步加锁 API 在所有上下文都可
    直接调用(异步 hook 无需 await);单文件极小,锁内完成读改写,
    写盘为 tmp + replace 原子替换,跨线程不会读到半截文件。
    """

    _file_lock = threading.RLock()  # 串行化所有模块的磁盘读写(文件极小,足够)

    def __init__(self, module_name: str, data_dir: Path):
        self.name = str(module_name)
        self.path = Path(data_dir) / f"{self.name}.json"
        self._lock = threading.RLock()  # 本模块缓存与落盘的一致性
        self._cache: Optional[dict] = None

    def __enter__(self):
        """`with agile.storage:` 跨 get/set 持锁,使读改写序列原子化(RLock 可重入)。"""
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._lock.release()
        return False

    def _ensure_loaded(self) -> None:
        if self._cache is None:
            with AgileStorage._file_lock:
                try:
                    self._cache = json.loads(self.path.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    self._cache = {}
                self._cache = dict(self._cache)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            self._ensure_loaded()
            return self._cache.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._ensure_loaded()
            self._cache[key] = value
            with AgileStorage._file_lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(self._cache, ensure_ascii=False), encoding="utf-8")
                tmp.replace(self.path)


class AgileHookType(str, Enum):
    VFS_CONTENT = "vfs_content"
    VFS_EDIT = "vfs_edit"
    VFS_WRITE = "vfs_write"
    INTERVAL_REGISTER = "interval_register"

@dataclass
class AgileHookBase:
    name: str
    description: Optional[str] = None
    func: Optional[Callable[..., Any]] = None
    attr: Optional[Any] = None
    hookType: AgileHookType|None = None
    func_signature: Optional[str] = None

class AgileContext:
    def __init__(self,ctx:PluginContext,alm:AgileLogManager,agile_name:str,
                 trigger_limiter:Optional[Callable[[],Any]]=None,
                 on_activity:Optional[Callable[[],Any]]=None,
                 storage:Optional[AgileStorage]=None,
                 data_dir:Optional[Path]=None):
        self.ctx = ctx
        self.alm = alm
        self.agile_name = agile_name
        self._trigger_limiter = trigger_limiter
        self._on_activity = on_activity
        self.storage = storage  # AgileStorage:模块级 KV 持久存储(可为 None,如测试桩)
        self.data_dir = Path(data_dir) if data_dir is not None else (
            storage.path.parent if storage is not None else Path.cwd())
        # 随模块卸载一起清理的额外 VFS 节点: [(path, func)]（runner 在 _unregister_hooks 里回收）
        self.transient_vfs: list[tuple[str, Any]] = []

    async def vfs_write(self,path:str,content:Any,description:str=""):
        await self.ctx.vfs_write(path,content,description=description)

    async def vfs_read_text(self,path:str,default:str=""):
        return await self.ctx.vfs_read_text(path,default)

    async def vfs_write_symbolic(self,path:str,func:Callable[...,Any],writable:bool=False,should_be_included_in_search:bool=True,description:str=""):
        await self.ctx.vfs_write_symbolic(path,func,writable=writable,should_be_included_in_search=should_be_included_in_search,description=description)

    async def vfs_set_write_handler(self,path:str,func:Callable[...,Any]):
        await self.ctx.vfs_set_write_handler(path,func)

    async def vfs_set_edit_handler(self,path:str,func:Callable[...,Any]):
        await self.ctx.vfs_set_edit_handler(path,func)

    async def vfs_delete(self,path:str):
        await self.ctx.vfs_delete(path)

    async def event_fire(self,event_name:str,data:Any,recall_description:str="Agent 可读的描述",lifespan:int=7200,priority:str="normal"):
        if self._trigger_limiter is not None:
            self._trigger_limiter()  # 超过每分钟触发上限时抛 RuntimeError
        await self.ctx.trigger_create({
            "id": f"agileEngine::{event_name}",
            "type": "event",
            "event_name": event_name,
            "payload": data,
            "recall_description": recall_description,
            "lifespan": lifespan,
            "priority": priority,
        })
        if self._on_activity is not None:
            self._on_activity()  # 事件成功进入触发器 = 模块活动，打点 last_seen

    async def log(self,level:str,msg:str):
        await self.alm.log(self.agile_name,level,msg,extra={})

    async def linfo(self,msg:str):
        await self.alm.log(self.agile_name,"INFO",msg,extra={})

    async def ldebug(self,msg:str):
        await self.alm.log(self.agile_name,"DEBUG",msg,extra={})

    async def lwarning(self,msg:str):
        await self.alm.log(self.agile_name,"WARNING",msg,extra={})

    async def lerror(self,msg:str):
        await self.alm.log(self.agile_name,"ERROR",msg,extra={})

    async def lcritical(self,msg:str):
        await self.alm.log(self.agile_name,"CRITICAL",msg,extra={})

    async def processor(self,name:str,data:Any,*,config:Optional[dict]=None,
                        timeout:Optional[float]=None,wait_timeout:Optional[float]=None)->Any:
        """调用 Processor（跑在受管子进程里的重计算：VAD / OCR / OMNIJEV）。

        一次调用 = 借出 → 等就绪 → invoke → 归还，引用计数不会泄漏。interval hook 跑在模块
        自己的 loop 线程里，这里内部会切回 backend 主循环，所以直接 await 即可。

        - ``config``：Processor 配置（如 OCR 的 ``{"langs":[...],"gpu":True}``、OMNIJEV 的
          ``{"size":"4B","quant":"4bit"}``）。已有人持有且配置不一致时会抛
          ``ProcessorConfigMismatchError``，不要硬塞不同配置。
        - ``wait_timeout=None``：一直等到就绪。**首次调用可能触发 setup（模型下载/量化，分钟级）**，
          要给上限就传秒数。
        - ``timeout``：单次 invoke 的超时（缺省用 Processor 自己的 INVOKE_TIMEOUT）。

        各 Processor 的入参/出参与成本见 ``skill://agile-engine/processor.md``。
        """
        return await self.ctx.processor_invoke(name,data,config=config,timeout=timeout,wait_timeout=wait_timeout)

    async def processor_status(self,name:str)->dict:
        """某个 Processor 的状态快照（state / refcount / last_error / config），排查用。"""
        return await self.ctx.processor_status(name)

    async def register_transient_vfs(self, path: str, func: Callable[..., Any], description: str = "") -> None:
        """注册一个只读 VFS 节点，随模块卸载/禁用一起被清理（runner 回收 transient_vfs）。"""
        await self.vfs_write_symbolic(path, func, writable=False, description=description)
        self.transient_vfs.append((str(path), func))

    async def limited_ui(self, spec: dict, hooks: Optional[dict] = None):
        """创建一个**受限 UI 操作**会话（看画面 → 小模型决定动作 → 执行），返回决策器。

        限制对象是小模型（OmniJev）：所有护栏（门限、白名单、前台/遮挡校验、上限、审计、
        急停）都在决策器基类里，子类只能提供"决策来源"。

        - ``spec``：会话声明（``decider``/``purpose``/``target``/``states``/``limits``/…），
          字段不合法直接抛错，不静默修正；
        - ``hooks``：只允许 ``{"context": fn, "classify": fn}`` 两个钩子，可 sync/async：
          ``context(frame, *, step_id) -> str | None`` 给画面补充文字（如血量/手牌）；
          ``classify(frame, *, step_id) -> str | None`` 自己判状态（必须是 ``spec["states"]`` 的键）。

        典型用法（interval hook 里）::

            dec = await agile.limited_ui(spec, hooks={"classify": my_classify})
            res = await dec.open()
            if not res.ok:
                await agile.lwarning(f"UI 会话未打开: {res.reason}")
                return
            d = await dec.step("该出牌了")     # decide + inject + 审计一行
            if not d.ok:
                await agile.linfo(d.describe())

        平台：**仅 Windows**，其它平台调用即抛错（不静默降级）。
        详细约定与风险见 ``skill://agile-engine/ui-control.md``。
        """
        import sys as _sys
        if _sys.platform != "win32":
            raise RuntimeError(
                f"limited_ui 仅支持 Windows（当前平台 {_sys.platform}）："
                "键盘/点击注入与客户区抓帧都依赖 Win32 API，不做降级实现")
        from uidrv import create_decider, hub
        from uidrv.blink import FrontendLink
        from uidrv.decider import SessionSpec, SessionState, UIDeps
        from uidrv.input import get_input_backend
        from uidrv.win import ensure_dpi_aware, get_win_backend
        from faust_backend import backend2front as backend2frontend
        from faust_backend.tools.hil import HILChoiceRequest

        ensure_dpi_aware()
        parsed = SessionSpec.parse(spec)

        wrapped: dict[str, Any] = {}
        for hook_name, fn in dict(hooks or {}).items():
            if hook_name not in ("context", "classify"):
                raise ValueError(
                    f"未知的 limited_ui 钩子 {hook_name!r}；只支持 context / classify")
            if not callable(fn):
                raise ValueError(f"limited_ui 钩子 {hook_name} 不是可调用对象")
            wrapped[hook_name] = build_invoker(fn, self)

        module = self.agile_name

        async def ask(payload: dict, config: dict) -> Any:
            return await self.processor("OMNIJEV", payload, config=config or None,
                                        wait_timeout=600.0)

        async def hil(payload: dict, timeout: float) -> str:
            import uuid as _uuid
            return await HILChoiceRequest(
                id=f"limited_ui_{module}_{_uuid.uuid4().hex[:8]}",
                title=payload.get("title", "UI 操作请求"),
                summary=payload.get("summary", ""), buttons=list(payload.get("buttons") or []),
                timeout_seconds=int(timeout), severity=payload.get("severity", "warning"))

        async def escalate(data: dict, recall: str) -> None:
            await self.event_fire("limited_ui::escalate", data, recall_description=recall,
                                  lifespan=7200, priority=parsed.escalate.priority)

        def sink(command: str, payload: dict) -> None:
            backend2frontend.frontendAvatarCommand(command, payload)

        deps = UIDeps(
            win=get_win_backend(),
            input=get_input_backend(),
            frontend=FrontendLink(sink, hub),
            audit_root=self.data_dir,
            processor_ask=ask,
            hil=hil,
            escalate=escalate,
            signal=hub,
        )
        decider = create_decider(parsed.decider, module, parsed, wrapped, deps)
        for path, (fn, description) in decider.vfs_nodes().items():
            await self.register_transient_vfs(path, (lambda _p, f=fn: f()), description)

        # 控制节点：主 Agent 读 ops/* 后用写 faustbot://agile/{module}/control 恢复/停止/改动作表
        def _control_text(_p: str = "") -> str:
            return json.dumps({
                "module": module,
                "state": decider.state.value,
                "steps": decider.step_count,
                "injections": decider.injection_count,
                "paused_reason": decider.pause_reason,
                "closed_reason": decider.closed_reason,
                "help": {"action": "resume | stop | patch_keys", "states": "patch_keys 时传 {状态: {actions: {...}}}"},
            }, ensure_ascii=False, indent=2)

        async def _on_control(_node: Any, content: Any) -> str:
            try:
                data = json.loads(content) if isinstance(content, str) else dict(content or {})
            except Exception as exc:  # noqa: BLE001
                return f"control 需要 JSON: {exc}"
            act = str(data.get("action") or "").strip().lower()
            if act == "resume":
                if decider.state is SessionState.CLOSED:
                    return f"会话已结束（{decider.closed_reason}），无法恢复；请重新 open()"
                await decider.resume()
                return "已恢复注入"
            if act == "stop":
                hub.request_stop(module)
                return "已请求停止（松手 + 暂停）"
            if act == "patch_keys":
                try:
                    decider.patch_states(dict(data.get("states") or {}))
                except Exception as exc:  # noqa: BLE001
                    return f"动作表未更新: {exc}"
                return "动作表已更新"
            return f"未知 action: {act!r}（支持 resume / stop / patch_keys）"

        control_path = f"/agile/{module}/control"
        await self.register_transient_vfs(control_path, _control_text, "UI 操作会话控制（JSON 写入）")
        await self.ctx.vfs_set_write_handler(control_path, _on_control)
        return decider

class AgileModule:
    def __init__(self,name,description:str,version:str="1.0.0"):
        self.name:str = name
        self.hooks:dict[str,AgileHookBase] = {}
        self.description:str = description
        self.version:str = version

    def vfsContentFunc(self,path:str,cacheStrategy:str="cache@10",description:str=""):
        def decorator(func:Callable[...,Any]):
            key = f"{AgileHookType.VFS_CONTENT.value}::{path}"
            self.hooks[key] = AgileHookBase(name=path,func=func,hookType=AgileHookType.VFS_CONTENT,attr={"cacheStrategy":cacheStrategy,"path":path,"description":str(description or "")})
            return func
        return decorator


    def vfsEditHook(self,path:str):
        def decorator(func:Callable[...,Any]):
            key = f"{AgileHookType.VFS_EDIT.value}::{path}"
            self.hooks[key] = AgileHookBase(name=path,func=func,hookType=AgileHookType.VFS_EDIT,attr={"path":path})
            return func
        return decorator

    def vfsWriteHook(self,path:str):
        def decorator(func:Callable[...,Any]):
            key = f"{AgileHookType.VFS_WRITE.value}::{path}"
            self.hooks[key] = AgileHookBase(name=path,func=func,hookType=AgileHookType.VFS_WRITE,attr={"path":path})
            return func
        return decorator

    def registerInterval(self,intervalExpr:int):
        def decorator(func:Callable[...,Any]):
            self.hooks[func.__name__] = AgileHookBase(name=func.__name__,func=func,hookType=AgileHookType.INTERVAL_REGISTER,attr={"intervalExpr":intervalExpr})
            return func
        return decorator

    def onloadHook(self):
        def decorator(func:Callable[...,Any]):
            self.hooks[func.__name__] = AgileHookBase(name=func.__name__,func=func,hookType=None,attr={"onload":True})
            return func
        return decorator

    def onunloadHook(self):
        def decorator(func:Callable[...,Any]):
            self.hooks[func.__name__] = AgileHookBase(name=func.__name__,func=func,hookType=None,attr={"onunload":True})
            return func
        return decorator

    def buildSignature(self):
        for hook in self.hooks.values():
            if hook.func is not None:
                sig = inspect.signature(hook.func)
                hook.func_signature = str(sig)

    def getHooks(self):
        self.buildSignature()
        return self.hooks


def build_invoker(func: Callable[..., Any], agile: AgileContext) -> Callable[..., Any]:
    """构造一个 async 调用器，按模块 hook 函数签名调用。

    - 签名中类型注解为 AgileContext 的参数（任意参数名），自动按名注入 agile 实例；
    - 同步函数直接调用，异步函数自动 await；
    - 调用方按 VFS/系统协议传入参数（内容函数: path；写/编辑 handler: node, content；
      interval/onload/onunload: 无），未声明对应形参的多余参数被忽略；
    - 其余关键字参数按名匹配，**kwargs 兜底。
    """
    sig = inspect.signature(func)
    pos_params = [
        p for p in sig.parameters.values()
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    kw_params = [p for p in sig.parameters.values() if p.kind == inspect.Parameter.KEYWORD_ONLY]
    has_var_pos = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in sig.parameters.values())
    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())

    async def invoke(*vfs_args: Any, **vfs_kwargs: Any) -> Any:
        call_args: list[Any] = []
        call_kwargs: dict[str, Any] = {}
        rest = dict(vfs_kwargs)
        idx = 0
        for p in pos_params:
            if p.annotation is AgileContext:
                call_kwargs[p.name] = agile
            elif idx < len(vfs_args):
                call_args.append(vfs_args[idx])
                idx += 1
            elif p.name in rest:
                call_kwargs[p.name] = rest.pop(p.name)
            elif p.default is not inspect.Parameter.empty:
                continue
            else:
                raise TypeError(f"missing required argument: {p.name!r}")
        if has_var_pos and idx < len(vfs_args):
            call_args.extend(vfs_args[idx:])
        for p in kw_params:
            if p.annotation is AgileContext:
                call_kwargs[p.name] = agile
            elif p.name in rest:
                call_kwargs[p.name] = rest.pop(p.name)
        if has_var_kw:
            call_kwargs.update(rest)
        result = func(*call_args, **call_kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    return invoke