"""
虚拟文件系统

用于让工具的内部协议支持search等操作
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class VfsNode:
    """A node in the virtual file system tree."""
    name: str
    is_directory: bool
    content: Any = None  # 现在可以是任意对象
    children: dict[str, VfsNode] = field(default_factory=dict)
    symbolic_func: Callable[[str], str] | None = None  # 动态文件生成器，返回字符串


class VirtualFileSystem:
    """A virtual file system that supports both memory and disk storage."""

    def __init__(self) -> None:
        self.root_node: VfsNode = VfsNode(name="", is_directory=True)

    @staticmethod
    def _normalize_path(path: str) -> str:
        path = path.strip().replace('\\', '/')
        if not path:
            return '/'
        if not path.startswith('/'):
            path = '/' + path
        parts = [p for p in path.split('/') if p]
        if not parts:
            return '/'
        return '/' + '/'.join(parts)

    @staticmethod
    def _get_path_parts(path: str) -> list[str]:
        norm = VirtualFileSystem._normalize_path(path)
        if norm == '/':
            return []
        return norm.split('/')[1:]

    def _ensure_dir_path(self, node: VfsNode, parts: list[str]) -> VfsNode:
        current = node
        for part in parts:
            if part not in current.children:
                current.children[part] = VfsNode(name=part, is_directory=True)
            current = current.children[part]
        return current

    def write(self, path: str, content: Any) -> None:
        """写入文件，content 可以是任意对象。"""
        parts = self._get_path_parts(path)
        if not parts:
            raise ValueError("Cannot write to root directory")
        *dirs, file_name = parts
        parent = self._ensure_dir_path(self.root_node, dirs)
        parent.children[file_name] = VfsNode(name=file_name, is_directory=False, content=content)

    def write_symbolic(self, path: str, func: Callable[[str], str]) -> None:
        """写入符号文件，其内容由 func 动态生成（字符串）。"""
        parts = self._get_path_parts(path)
        if not parts:
            raise ValueError("Cannot write to root directory")
        *dirs, file_name = parts
        parent = self._ensure_dir_path(self.root_node, dirs)
        parent.children[file_name] = VfsNode(name=file_name, is_directory=False, symbolic_func=func)

    def read(self, path: str) -> Any:
        """读取文件内容。返回存储的原始对象，或符号文件生成的字符串。"""
        parts = self._get_path_parts(path)
        current = self.root_node
        for part in parts:
            if part not in current.children:
                return None
            current = current.children[part]
        if current.is_directory:
            return None
        if current.symbolic_func is not None:
            norm_path = self._normalize_path(path)
            return current.symbolic_func(norm_path)
        return current.content

    def list_dir(self, path: str) -> list[str] | None:
        parts = self._get_path_parts(path)
        current = self.root_node
        for part in parts:
            if part not in current.children:
                return None
            current = current.children[part]
        if not current.is_directory:
            return None
        return list(current.children.keys())


    def mount_file(self, virtual_path: str, real_path: str) -> None:
        """将真实文本文件挂载到虚拟文件系统。"""
        try:
            with open(real_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except OSError as e:
            raise OSError(f"Failed to mount real file '{real_path}': {e}") from e
        self.write(virtual_path, content)


    def search(self, path: str, keyword: str, include_symbolic: bool = False) -> list[str]:
        """搜索指定路径下内容包含关键词的文件。
        
        注意：搜索仅对内容是字符串的文件有效，非字符串内容将被忽略。
        """
        norm_path = self._normalize_path(path)
        parts = self._get_path_parts(path)
        current = self.root_node
        for part in parts:
            if part not in current.children:
                return []
            current = current.children[part]

        results: list[str] = []
        self._search_recursive(current, norm_path, keyword, results, include_symbolic)
        return results

    def _search_recursive(
        self,
        node: VfsNode,
        current_path: str,
        keyword: str,
        results: list[str],
        include_symbolic: bool,
    ) -> None:
        if not node.is_directory:
            content: Any = None
            if node.symbolic_func is not None:
                if include_symbolic:
                    content = node.symbolic_func(current_path)
            else:
                content = node.content

            # 仅对字符串内容进行关键词匹配
            if isinstance(content, str) and keyword in content:
                results.append(current_path)
        else:
            for child_name, child_node in node.children.items():
                child_path = f'{current_path}/{child_name}' if current_path != '/' else f'/{child_name}'
                self._search_recursive(child_node, child_path, keyword, results, include_symbolic)

    def glob(self, pattern: str) -> list[str]:
        results: list[str] = []
        normalized_pattern = pattern.strip().replace('\\', '/')
        if not normalized_pattern.startswith('/'):
            normalized_pattern = '/' + normalized_pattern
        self._glob_recursive(self.root_node, '/', normalized_pattern, results)
        return results

    def _glob_recursive(self, node: VfsNode, current_path: str, pattern: str, results: list[str]) -> None:
        if fnmatch.fnmatch(current_path, pattern):
            results.append(current_path)
        if node.is_directory:
            for child_name, child_node in node.children.items():
                child_path = f'{current_path}/{child_name}' if current_path != '/' else f'/{child_name}'
                self._glob_recursive(child_node, child_path, pattern, results)
    
    def serialize(self) -> dict:
        """将虚拟文件系统序列化为字典形式，便于存储或传输。"""
        return self._serialize_node(self.root_node)
    
    @staticmethod
    def _serialize_node(node: VfsNode) -> dict:
        serialized = {
            "name": node.name,
            "is_directory": node.is_directory,
            "content": node.content if not node.is_directory else None,
            "children": {name: VirtualFileSystem._serialize_node(child) for name, child in node.children.items()},
            "symbolic_func": None  # 符号函数无法序列化
        }
        return serialized
    
    def deserialize(self, data: dict) -> None:
        """从字典形式反序列化虚拟文件系统。"""
        self.root_node = self._deserialize_node(data)
    
    @staticmethod
    def _deserialize_node(data: dict) -> VfsNode:
        node = VfsNode(
            name=data["name"],
            is_directory=data["is_directory"],
            content=data.get("content"),
            children={name: VirtualFileSystem._deserialize_node(child) for name, child in data.get("children", {}).items()},
            symbolic_func=None  # 符号函数无法反序列化
        )
        return node