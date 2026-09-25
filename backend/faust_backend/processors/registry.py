"""Processor 类注册表（父/子进程共用）。

父进程登记类名、owner 与来源模块；子进程按 ``loader`` 导入模块后重新登记，
再按名字取出唯一的 Processor 实例。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from .base import Processor
from .errors import ProcessorError, ProcessorNotFoundError

_PROC = TypeVar("_PROC", bound=type[Processor])


@dataclass(frozen=True)
class RegisteredProcessor:
    """一条注册记录。"""

    name: str
    cls: type[Processor]
    owner: str  # "builtin" 或 plugin_id
    loader: str  # 子进程加载用：模块名（builtin）或 .py 绝对路径（插件）
    source_file: str  # 类所在模块的文件路径


_REGISTRY: dict[str, RegisteredProcessor] = {}


def _module_file(cls: type) -> str:
    module = sys.modules.get(getattr(cls, "__module__", "") or "")
    path = getattr(module, "__file__", None)
    return str(Path(path).resolve()) if path else ""


def register_processor(
    cls: _PROC,
    name: str | None = None,
    *,
    owner: str = "builtin",
    loader: str | None = None,
    source_file: str | None = None,
) -> str:
    """登记一个 Processor 类，返回最终名字。名字重复/类不合法立即报错。"""
    if not (isinstance(cls, type) and issubclass(cls, Processor)):
        raise ProcessorError(f"{cls!r} 不是 Processor 子类")

    proc_name = str(name or getattr(cls, "NAME", "") or "").strip()
    if not proc_name:
        raise ProcessorError(f"{cls.__name__} 未提供 Processor 名称（NAME 为空且未显式指定）")
    if cls.start is Processor.start:
        raise ProcessorError(f"{cls.__name__} 未实现 start()")
    if cls.invoke is Processor.invoke:
        raise ProcessorError(f"{cls.__name__} 未实现 invoke()")

    existing = _REGISTRY.get(proc_name)
    if existing is not None:
        raise ProcessorError(
            f"Processor 名字冲突: {proc_name} 已由 {existing.owner} ({existing.cls.__name__}) 注册"
        )

    file_path = str(source_file or _module_file(cls))
    resolved_loader = str(loader or (cls.__module__ if owner == "builtin" else file_path))

    _REGISTRY[proc_name] = RegisteredProcessor(
        name=proc_name,
        cls=cls,
        owner=str(owner),
        loader=resolved_loader,
        source_file=file_path,
    )
    return proc_name


def processor(name: str | None = None):
    """装饰器形式：``@processor("VAD")``。"""

    def _decorator(cls: _PROC) -> _PROC:
        register_processor(cls, name or getattr(cls, "NAME", "") or cls.__name__)
        return cls

    return _decorator


def get_processor(name: str) -> RegisteredProcessor:
    """取注册记录；未注册抛 ``ProcessorNotFoundError``。"""
    entry = _REGISTRY.get(str(name))
    if entry is None:
        raise ProcessorNotFoundError(str(name))
    return entry


def list_processors() -> list[RegisteredProcessor]:
    """全部注册记录（按名字排序）。"""
    return [_REGISTRY[key] for key in sorted(_REGISTRY)]


def registered_names() -> list[str]:
    """全部已注册名字（按名字排序）。"""
    return sorted(_REGISTRY)


def unregister_owner(owner: str) -> list[str]:
    """摘除某 owner 名下的全部注册，返回被摘除的名字。"""
    removed = [name for name, entry in _REGISTRY.items() if entry.owner == owner]
    for name in removed:
        _REGISTRY.pop(name, None)
    return sorted(removed)
