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
                 storage:Optional[AgileStorage]=None):
        self.ctx = ctx
        self.alm = alm
        self.agile_name = agile_name
        self._trigger_limiter = trigger_limiter
        self._on_activity = on_activity
        self.storage = storage  # AgileStorage:模块级 KV 持久存储(可为 None,如测试桩)

    async def vfs_write(self,path:str,content:Any):
        await self.ctx.vfs_write(path,content)

    async def vfs_read_text(self,path:str,default:str=""):
        return await self.ctx.vfs_read_text(path,default)

    async def vfs_write_symbolic(self,path:str,func:Callable[...,Any],writable:bool=False,should_be_included_in_search:bool=True):
        await self.ctx.vfs_write_symbolic(path,func,writable=writable,should_be_included_in_search=should_be_included_in_search)

    async def vfs_set_write_handler(self,path:str,func:Callable[...,Any]):
        await self.ctx.vfs_set_write_handler(path,func)

    async def vfs_set_edit_handler(self,path:str,func:Callable[...,Any]):
        await self.ctx.vfs_set_edit_handler(path,func)

    async def vfs_delete(self,path:str):
        await self.ctx.vfs_delete(path)

    async def event_fire(self,event_name:str,data:Any,recall_description:str="Agent 可读的描述",lifespan:int=7200):
        if self._trigger_limiter is not None:
            self._trigger_limiter()  # 超过每分钟触发上限时抛 RuntimeError
        await self.ctx.trigger_create({
            "id": f"agileEngine::{event_name}",
            "type": "event",
            "event_name": event_name,
            "payload": data,
            "recall_description": recall_description,
            "lifespan": lifespan,
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

class AgileModule:
    def __init__(self,name,description:str,version:str="1.0.0"):
        self.name:str = name
        self.hooks:dict[str,AgileHookBase] = {}
        self.description:str = description
        self.version:str = version

    def vfsContentFunc(self,path:str,cacheStrategy:str="cache@10"):
        def decorator(func:Callable[...,Any]):
            key = f"{AgileHookType.VFS_CONTENT.value}::{path}"
            self.hooks[key] = AgileHookBase(name=path,func=func,hookType=AgileHookType.VFS_CONTENT,attr={"cacheStrategy":cacheStrategy,"path":path})
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