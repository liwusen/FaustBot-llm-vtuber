from __future__ import annotations

import asyncio
import fnmatch
import getpass
import inspect
import os
import platform
import sys
import threading
from pathlib import Path
from typing import Any, Awaitable, Callable

import faust_backend.config_loader as conf
from faust_backend.logger import get_logger

log = get_logger('faust.vfs')

SymbolicFunc = Callable[[str], Any] | Callable[[str], Awaitable[Any]]


class AsyncRWLock:
    def __init__(self) -> None:
        self._cond = asyncio.Condition()
        self._readers = 0
        self._writer = False
        self._writers_waiting = 0

    async def acquire_read(self) -> None:
        async with self._cond:
            while self._writer or self._writers_waiting > 0:
                await self._cond.wait()
            self._readers += 1

    async def release_read(self) -> None:
        async with self._cond:
            self._readers = max(0, self._readers - 1)
            if self._readers == 0:
                self._cond.notify_all()

    async def acquire_write(self) -> None:
        async with self._cond:
            self._writers_waiting += 1
            try:
                while self._writer or self._readers > 0:
                    await self._cond.wait()
                self._writer = True
            finally:
                self._writers_waiting = max(0, self._writers_waiting - 1)

    async def release_write(self) -> None:
        async with self._cond:
            self._writer = False
            self._cond.notify_all()

    def read_lock(self):
        lock = self

        class _ReadCtx:
            async def __aenter__(self_inner):
                await lock.acquire_read()
                return self_inner

            async def __aexit__(self_inner, exc_type, exc, tb):
                await lock.release_read()
                return False

        return _ReadCtx()

    def write_lock(self):
        lock = self

        class _WriteCtx:
            async def __aenter__(self_inner):
                await lock.acquire_write()
                return self_inner

            async def __aexit__(self_inner, exc_type, exc, tb):
                await lock.release_write()
                return False

        return _WriteCtx()


class VfsNode:
    def __init__(self, *, name: str, is_directory: bool, content: Any = None, symbolic_func: SymbolicFunc | None = None, should_be_included_in_search: bool = True, writable: bool = True,write_handler:Callable | None = None,edit_handler: Callable | None = None):
        self.name = name
        self.is_directory = is_directory
        self.content = content
        self.children: dict[str, VfsNode] = {}
        self.symbolic_func:SymbolicFunc = symbolic_func#type: ignore
        self.should_be_included_in_search = should_be_included_in_search
        self.writable = writable
        # handler 签名为 (node: VfsNode, content: Any)，支持 sync/async；
        # 存在 handler 时完全接管写入/编辑，VFS 不再自动替换节点内容
        self.write_handler: Callable | None = write_handler
        self.edit_handler: Callable | None = edit_handler

    @property
    def is_symbolic(self) -> bool:
        return self.symbolic_func is not None

    @property
    def has_write_handler(self) -> bool:
        return self.write_handler is not None

    @property
    def has_edit_handler(self) -> bool:
        return self.edit_handler is not None

class AsyncVirtualFileSystem:
    def __init__(self) -> None:
        self.root_node = VfsNode(name='', is_directory=True)
        self._rwlock = AsyncRWLock()

    @staticmethod
    def normalize_path(path: str) -> str:
        raw = str(path or '').strip().replace('\\', '/')
        if not raw:
            return '/'
        if not raw.startswith('/'):
            raw = '/' + raw
        normalized: list[str] = []
        for part in raw.split('/'):
            if not part or part == '.':
                continue
            if part == '..':
                if normalized:
                    normalized.pop()
                continue
            normalized.append(part)
        return '/' + '/'.join(normalized) if normalized else '/'

    @classmethod
    def get_path_parts(cls, path: str) -> list[str]:
        normalized = cls.normalize_path(path)
        if normalized == '/':
            return []
        return normalized.lstrip('/').split('/')

    def _ensure_dir_path_unlocked(self, parts: list[str]) -> VfsNode:
        current = self.root_node
        for part in parts:
            child = current.children.get(part)
            if child is None:
                child = VfsNode(name=part, is_directory=True)
                current.children[part] = child
            elif not child.is_directory:
                raise ValueError(f'Path segment is not a directory: {part}')
            current = child
        return current

    def _get_node_unlocked(self, path: str) -> VfsNode | None:
        parts = self.get_path_parts(path)
        current = self.root_node
        for part in parts:
            current = current.children.get(part)
            if current is None:
                return None
        return current

    async def mkdir(self, path: str) -> None:
        async with self._rwlock.write_lock():
            self._ensure_dir_path_unlocked(self.get_path_parts(path))

    async def exists(self, path: str) -> bool:
        async with self._rwlock.read_lock():
            return self._get_node_unlocked(path) is not None

    async def is_dir(self, path: str) -> bool:
        async with self._rwlock.read_lock():
            node = self._get_node_unlocked(path)
            return bool(node and node.is_directory)

    async def is_file(self, path: str) -> bool:
        async with self._rwlock.read_lock():
            node = self._get_node_unlocked(path)
            return bool(node and not node.is_directory)

    async def write(self, path: str, content: Any, *, writable: bool = True) -> None:
        parts = self.get_path_parts(path)
        if not parts:
            raise ValueError('Cannot write to root directory')
        async with self._rwlock.write_lock():
            *dirs, file_name = parts
            parent = self._ensure_dir_path_unlocked(dirs)
            existing = parent.children.get(file_name)
            handler = existing.write_handler if existing is not None else None
            if handler is None:
                if existing is not None and existing.is_symbolic and not existing.writable:
                    raise PermissionError(f'Symbolic node is read-only: {self.normalize_path(path)}')
                parent.children[file_name] = VfsNode(name=file_name, is_directory=False, content=content, writable=writable)
                return
        result = handler(existing, content)
        if inspect.isawaitable(result):
            await result

    async def edit(self, path: str, edited_content: Any, *, writable: bool = True) -> None:
        parts = self.get_path_parts(path)
        if not parts:
            raise ValueError('Cannot edit root directory')
        async with self._rwlock.write_lock():
            *dirs, file_name = parts
            parent = self._ensure_dir_path_unlocked(dirs)
            existing = parent.children.get(file_name)
            handler = existing.edit_handler if existing is not None else None
            if handler is None:
                if existing is not None and existing.is_symbolic and not existing.writable:
                    raise PermissionError(f'Symbolic node is read-only: {self.normalize_path(path)}')
                parent.children[file_name] = VfsNode(name=file_name, is_directory=False, content=edited_content, writable=writable)
                return
        result = handler(existing, edited_content)
        if inspect.isawaitable(result):
            await result

    async def write_symbolic(self, path: str, func: SymbolicFunc, *, should_be_included_in_search: bool = True, writable: bool = False) -> None:
        """注册一个 symbol 节点：读取时调用 func(path) 生成内容。

        内容函数 func 支持同步与异步两种形态（返回 awaitable 会被自动等待），
        例如 async def fn(path): ... await 模型调用。异步内容函数执行期间不持有
        读写锁，可安全地嵌套调用其它 VFS 方法。
        writable=True 且未设置 write/edit handler 时，写入会替换为普通内容节点。
        """
        parts = self.get_path_parts(path)
        if not parts:
            raise ValueError('Cannot write symbolic content to root directory')
        async with self._rwlock.write_lock():
            *dirs, file_name = parts
            parent = self._ensure_dir_path_unlocked(dirs)
            parent.children[file_name] = VfsNode(name=file_name, is_directory=False, symbolic_func=func, should_be_included_in_search=should_be_included_in_search, writable=writable)

    async def set_write_handler(self, path: str, func: Callable) -> None:
        parts = self.get_path_parts(path)
        if not parts:
            raise ValueError('Cannot set write handler on root directory')
        async with self._rwlock.write_lock():
            *dirs, file_name = parts
            parent = self._ensure_dir_path_unlocked(dirs)
            node = parent.children.get(file_name)
            if node is None:
                raise FileNotFoundError(f'VFS node not found: {self.normalize_path(path)}')
            node.write_handler = func

    async def set_edit_handler(self, path: str, func: Callable) -> None:
        parts = self.get_path_parts(path)
        if not parts:
            raise ValueError('Cannot set edit handler on root directory')
        async with self._rwlock.write_lock():
            *dirs, file_name = parts
            parent = self._ensure_dir_path_unlocked(dirs)
            node = parent.children.get(file_name)
            if node is None:
                raise FileNotFoundError(f'VFS node not found: {self.normalize_path(path)}')
            node.edit_handler = func

    async def get_node(self, path: str) -> VfsNode | None:
        """返回节点引用（带读锁），供调用方校验节点属性（如卸载时的归属确认）。"""
        async with self._rwlock.read_lock():
            return self._get_node_unlocked(path)

    async def read(self, path: str) -> Any:
        """读取节点内容。symbol 节点会调用内容函数（同步/异步均可）并返回结果。"""
        async with self._rwlock.read_lock():
            node = self._get_node_unlocked(path)
            if node is None or node.is_directory:
                return None
            symbolic_func = node.symbolic_func
            content = node.content
        if symbolic_func is not None:
            value = symbolic_func(self.normalize_path(path))
            if inspect.isawaitable(value):
                return await value
            return value
        return content

    async def delete(self, path: str) -> bool:
        parts = self.get_path_parts(path)
        if not parts:
            raise ValueError('Cannot delete root directory')
        async with self._rwlock.write_lock():
            *dirs, name = parts
            parent = self._get_node_unlocked('/' + '/'.join(dirs)) if dirs else self.root_node
            if parent is None or not parent.is_directory:
                return False
            return parent.children.pop(name, None) is not None

    async def list_dir(self, path: str) -> list[str] | None:
        async with self._rwlock.read_lock():
            node = self._get_node_unlocked(path)
            if node is None or not node.is_directory:
                return None
            return sorted(node.children.keys())

    async def walk(self, path: str = '/') -> list[str]:
        async with self._rwlock.read_lock():
            node = self._get_node_unlocked(path)
            if node is None:
                return []
            results: list[str] = []
            self._walk_recursive(node, self.normalize_path(path), results)
            return results

    def _walk_recursive(self, node: VfsNode, current_path: str, results: list[str]) -> None:
        results.append(current_path)
        if not node.is_directory:
            return
        for child_name in sorted(node.children.keys()):
            child = node.children[child_name]
            child_path = current_path.rstrip('/') + '/' + child_name if current_path != '/' else '/' + child_name
            self._walk_recursive(child, child_path, results)

    async def mount_tree(self, virtual_root: str, real_root: str, *, include_binary: bool = False) -> None:
        root_path = Path(real_root)
        if not root_path.exists() or not root_path.is_dir():
            raise FileNotFoundError(real_root)
        virtual_root_norm = self.normalize_path(virtual_root)
        await self.mkdir(virtual_root_norm)
        for item in root_path.rglob('*'):
            rel = item.relative_to(root_path).as_posix()
            target = virtual_root_norm.rstrip('/') + '/' + rel if virtual_root_norm != '/' else '/' + rel
            if item.is_dir():
                await self.mkdir(target)
                continue
            try:
                content = item.read_text(encoding='utf-8')
            except UnicodeDecodeError:
                if not include_binary:
                    continue
                content = item.read_bytes()
            await self.write(target, content)

    @staticmethod
    def content_to_text(content: Any) -> str | None:
        if content is None:
            return None
        if isinstance(content, str):
            return content
        if isinstance(content, bytes):
            try:
                return content.decode('utf-8')
            except UnicodeDecodeError:
                return None
        return str(content)

    async def search(self, path: str, keyword: str, *, include_symbolic: bool = True) -> list[str]:
        async with self._rwlock.read_lock():
            node = self._get_node_unlocked(path)
            if node is None:
                return []
            candidates: list[tuple[str, VfsNode]] = []
            self._collect_search_candidates(node, self.normalize_path(path), candidates)
        results: list[str] = []
        for current_path, node in candidates:
            if node.is_symbolic:
                if not include_symbolic or not node.should_be_included_in_search:
                    continue
                value = node.symbolic_func(current_path)
                if inspect.isawaitable(value):
                    value = await value
            else:
                value = node.content
            text = self.content_to_text(value)
            if text is not None and str(keyword or '') in text:
                results.append(current_path)
        return results

    def _collect_search_candidates(self, node: VfsNode, current_path: str, results: list[tuple[str, VfsNode]]) -> None:
        if node.is_directory:
            for child_name in sorted(node.children.keys()):
                child = node.children[child_name]
                child_path = current_path.rstrip('/') + '/' + child_name if current_path != '/' else '/' + child_name
                self._collect_search_candidates(child, child_path, results)
            return
        results.append((current_path, node))

    async def glob(self, pattern: str) -> list[str]:
        normalized_pattern = self.normalize_path(pattern)
        return [path for path in await self.walk('/') if fnmatch.fnmatch(path, normalized_pattern)]

    async def read_text(self, path: str, default: str = '') -> str:
        value = await self.read(path)
        text = self.content_to_text(value)
        return text if text is not None else default


_VFS_SINGLETON: AsyncVirtualFileSystem | None = None
_VFS_ASYNC_INIT_LOCK = asyncio.Lock()


def _read_task_section(section_title: str) -> str:
    from faust_backend.runtime import state
    task_path = Path(state.AGENT_ROOT) / 'TASK.md'
    if not task_path.exists():
        return f'[找不到 TASK.md: {task_path}]'
    content = task_path.read_text(encoding='utf-8', errors='replace')
    lines = content.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if line.strip() == section_title.strip():
            start = idx
            break
    if start is None:
        return f'[TASK.md 中找不到章节: {section_title}]'
    end = len(lines)
    for idx in range(start + 1, len(lines)):
        if lines[idx].startswith('## '):
            end = idx
            break
    return '\n'.join(lines[start:end]).strip()


def _subagenting_doc() -> str:
    return '\n'.join([
        '# Subagent 协议与用法',
        '',
        '可用只读资源：',
        '- faustbot://subagents/{name}',
        '- faustbot://subagents/{name}/output',
        '- faustbot://avatoolset',
        '',
        '建议工作流：',
        '1. 先读取 faustbot://subagents/{name} 查看状态、工具组、最近事件。',
        '2. 需要看完整工作输出时，再读取 faustbot://subagents/{name}/output。',
        '3. 若输出过长，优先使用行号选择器，例如 faustbot://subagents/demo/output:1-80。',
        '4. 若要了解可用 Toolset 或 MCP 派生工具组，读取 faustbot://avatoolset。',
    ])


def _avatoolset_doc() -> str:
    from faust_backend.runtime import state
    manager = state.subagent_manager
    if manager is None:
        return '(subagent manager 未初始化)'
    return '# Available Toolsets For Subagents\n\n' + manager.format_available_toolsets()


def _ava_subagent_models_doc() -> str:
    # [R8] provider 未加载/文件损坏时返回友好提示，不让 VFS 读取崩溃
    try:
        from faust_backend.runtime import state
        providers = state.get_model_providers()
    except Exception as exc:  # noqa: BLE001
        return f"# Available Subagent Models\n\n(provider 配置不可用: {exc})"
    lines = ["# Available Subagent Models", ""]
    if not providers.subagent_models:
        lines.append("(未配置 Subagent 专用模型，默认使用 main_model)")
        lines.append(f"main_model: {providers.main_model or '(未配置)'}")
        return "\n".join(lines)
    lines.append("subagent_models（白名单，第一个为默认）:")
    for spec in providers.subagent_models:
        lines.append(f"- {spec}")
    lines.append("")
    lines.append("main_model: " + (providers.main_model or "(未配置)"))
    lines.append("")
    lines.append("创建 Subagent 时用 newSubagent(..., model='provider::model') 指定模型。")
    return "\n".join(lines)


def _pc_info_doc() -> str:
    return '\n'.join([
        '# pc_info',
        f'username: {getpass.getuser()}',
        f'platform: {platform.platform()}',
        f'python: {sys.version.split()[0]}',
        f'cwd: {os.getcwd()}',
    ])


async def _ensure_core_structure(vfs: AsyncVirtualFileSystem) -> None:
    await vfs.mkdir('/plugins')
    await vfs.mkdir('/subagents')
    await vfs.write_symbolic('/tool_use.md', lambda _path: _read_task_section('## 核心工具速查'))
    await vfs.write_symbolic('/mc.md', lambda _path: _read_task_section('## Minecraft 操作系统说明'))
    await vfs.write_symbolic('/subagenting.md', lambda _path: _subagenting_doc())
    await vfs.write_symbolic('/avatoolset', lambda _path: _avatoolset_doc())
    await vfs.write_symbolic('/ava_subagent_models', lambda _path: _ava_subagent_models_doc())
    await vfs.write_symbolic('/pc_info', lambda _path: _pc_info_doc())

    async def index_doc(_path: str) -> str:
        await refresh_runtime_nodes(vfs)
        items = await vfs.list_dir('/') or []
        lines = ['# faustbot:// 索引', '', '可读取资源：']
        for item in items:
            child_path = '/' + item if item else '/'
            is_dir = await vfs.is_dir(child_path)
            lines.append(f"- faustbot://{item}{'/' if is_dir else ''}")
        lines += ['', '源码阅读：使用 read("sourceCode://{PATH}")（目录自动列出内容）。']
        lines += ['', '建议：优先读取 faustbot://index.md、faustbot://plugins/、faustbot://subagents/。']
        return '\n'.join(lines)

    await vfs.write_symbolic('/index.md', index_doc)


async def refresh_runtime_nodes(vfs: AsyncVirtualFileSystem | None = None) -> AsyncVirtualFileSystem:
    target = vfs or await get_faustbot_vfs()
    await target.mkdir('/subagents')
    existing = await target.list_dir('/subagents') or []
    for name in existing:
        await target.delete('/subagents/' + name)
    from faust_backend.runtime import state
    manager = state.subagent_manager
    if manager is not None:
        statuses = manager.list_statuses()
        for item in statuses:
            name = str(item.get('name') or '').strip()
            if not name:
                continue
            await target.mkdir('/subagents/' + name)
            await target.write_symbolic('/subagents/' + name + '/overview.md', lambda _path, agent_name=name: manager.format_subagent_overview(agent_name))
            await target.write_symbolic('/subagents/' + name + '/output.md', lambda _path, agent_name=name: manager.format_subagent_output(agent_name))
            await target.write_symbolic('/subagents/' + name + '/finalResult.md', lambda _path, agent_name=name: manager.format_subagent_final_result(agent_name))
    return target


async def get_faustbot_vfs(refresh: bool = False) -> AsyncVirtualFileSystem:
    global _VFS_SINGLETON
    if _VFS_SINGLETON is None:
        async with _VFS_ASYNC_INIT_LOCK:
            if _VFS_SINGLETON is None:
                _VFS_SINGLETON = AsyncVirtualFileSystem()
                await _ensure_core_structure(_VFS_SINGLETON)
    if refresh:
        await refresh_runtime_nodes(_VFS_SINGLETON)
    return _VFS_SINGLETON
