"""虚拟文件系统。

用于承载内部协议文件，并为 search/glob/read 等操作提供统一抽象。
这个实现刻意保持纯内存、无异步、无外部依赖，便于被协议层直接复用。
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable


SymbolicFunc = Callable[[str], Any]


@dataclass
class VfsNode:
    name: str
    is_directory: bool
    content: Any = None
    children: dict[str, "VfsNode"] = field(default_factory=dict)
    symbolic_func: SymbolicFunc | None = None

    @property
    def is_symbolic(self) -> bool:
        return self.symbolic_func is not None


class VirtualFileSystem:
    def __init__(self) -> None:
        self.root_node = VfsNode(name="", is_directory=True)

    @staticmethod
    def _normalize_path(path: str) -> str:
        raw = str(path or "").strip().replace("\\", "/")
        if not raw:
            return "/"
        if not raw.startswith("/"):
            raw = "/" + raw
        normalized: list[str] = []
        for part in raw.split("/"):
            if not part or part == ".":
                continue
            if part == "..":
                if normalized:
                    normalized.pop()
                continue
            normalized.append(part)
        return "/" + "/".join(normalized) if normalized else "/"

    @classmethod
    def _get_path_parts(cls, path: str) -> list[str]:
        normalized = cls._normalize_path(path)
        if normalized == "/":
            return []
        return normalized.lstrip("/").split("/")

    def _ensure_dir_path(self, parts: list[str]) -> VfsNode:
        current = self.root_node
        for part in parts:
            child = current.children.get(part)
            if child is None:
                child = VfsNode(name=part, is_directory=True)
                current.children[part] = child
            elif not child.is_directory:
                raise ValueError(f"Path segment is not a directory: {part}")
            current = child
        return current

    def _get_node(self, path: str) -> VfsNode | None:
        parts = self._get_path_parts(path)
        current = self.root_node
        for part in parts:
            current = current.children.get(part)
            if current is None:
                return None
        return current

    def _require_node(self, path: str) -> VfsNode:
        node = self._get_node(path)
        if node is None:
            raise FileNotFoundError(self._normalize_path(path))
        return node

    def mkdir(self, path: str) -> None:
        self._ensure_dir_path(self._get_path_parts(path))

    def exists(self, path: str) -> bool:
        return self._get_node(path) is not None

    def is_dir(self, path: str) -> bool:
        node = self._get_node(path)
        return bool(node and node.is_directory)

    def is_file(self, path: str) -> bool:
        node = self._get_node(path)
        return bool(node and not node.is_directory)

    def write(self, path: str, content: Any) -> None:
        parts = self._get_path_parts(path)
        if not parts:
            raise ValueError("Cannot write to root directory")
        *dirs, file_name = parts
        parent = self._ensure_dir_path(dirs)
        parent.children[file_name] = VfsNode(name=file_name, is_directory=False, content=content)

    def write_symbolic(self, path: str, func: SymbolicFunc) -> None:
        parts = self._get_path_parts(path)
        if not parts:
            raise ValueError("Cannot write symbolic content to root directory")
        *dirs, file_name = parts
        parent = self._ensure_dir_path(dirs)
        parent.children[file_name] = VfsNode(name=file_name, is_directory=False, symbolic_func=func)

    def read(self, path: str) -> Any:
        node = self._get_node(path)
        if node is None or node.is_directory:
            return None
        if node.symbolic_func is not None:
            return node.symbolic_func(self._normalize_path(path))
        return node.content

    def delete(self, path: str) -> bool:
        parts = self._get_path_parts(path)
        if not parts:
            raise ValueError("Cannot delete root directory")
        *dirs, name = parts
        parent = self._get_node("/" + "/".join(dirs)) if dirs else self.root_node
        if parent is None or not parent.is_directory:
            return False
        return parent.children.pop(name, None) is not None

    def list_dir(self, path: str) -> list[str] | None:
        node = self._get_node(path)
        if node is None or not node.is_directory:
            return None
        return sorted(node.children.keys())

    def walk(self, path: str = "/") -> list[str]:
        node = self._get_node(path)
        if node is None:
            return []
        normalized = self._normalize_path(path)
        results: list[str] = []
        self._walk_recursive(node, normalized, results)
        return results

    def _walk_recursive(self, node: VfsNode, current_path: str, results: list[str]) -> None:
        results.append(current_path)
        if not node.is_directory:
            return
        for child_name in sorted(node.children.keys()):
            child = node.children[child_name]
            child_path = current_path.rstrip("/") + "/" + child_name if current_path != "/" else "/" + child_name
            self._walk_recursive(child, child_path, results)

    def mount_file(self, virtual_path: str, real_path: str) -> None:
        file_path = Path(real_path)
        try:
            content = file_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise OSError(f"Failed to mount real file '{real_path}': {exc}") from exc
        self.write(virtual_path, content)

    def mount_tree(self, virtual_root: str, real_root: str, *, include_binary: bool = False) -> None:
        root_path = Path(real_root)
        if not root_path.exists() or not root_path.is_dir():
            raise FileNotFoundError(real_root)
        virtual_root_norm = self._normalize_path(virtual_root)
        self.mkdir(virtual_root_norm)
        for item in root_path.rglob("*"):
            rel = item.relative_to(root_path).as_posix()
            target = virtual_root_norm.rstrip("/") + "/" + rel if virtual_root_norm != "/" else "/" + rel
            if item.is_dir():
                self.mkdir(target)
                continue
            try:
                content = item.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                if not include_binary:
                    continue
                content = item.read_bytes()
            self.write(target, content)

    def search(self, path: str, keyword: str, include_symbolic: bool = False) -> list[str]:
        node = self._get_node(path)
        if node is None:
            return []
        results: list[str] = []
        self._search_recursive(node, self._normalize_path(path), str(keyword or ""), results, include_symbolic)
        return results

    @staticmethod
    def _content_to_text(content: Any) -> str | None:
        if content is None:
            return None
        if isinstance(content, str):
            return content
        if isinstance(content, bytes):
            try:
                return content.decode("utf-8")
            except UnicodeDecodeError:
                return None
        return str(content)

    def _search_recursive(
        self,
        node: VfsNode,
        current_path: str,
        keyword: str,
        results: list[str],
        include_symbolic: bool,
    ) -> None:
        if node.is_directory:
            for child_name in sorted(node.children.keys()):
                child = node.children[child_name]
                child_path = current_path.rstrip("/") + "/" + child_name if current_path != "/" else "/" + child_name
                self._search_recursive(child, child_path, keyword, results, include_symbolic)
            return

        content: Any
        if node.symbolic_func is not None:
            if not include_symbolic:
                return
            content = node.symbolic_func(current_path)
        else:
            content = node.content
        text = self._content_to_text(content)
        if text is not None and keyword in text:
            results.append(current_path)

    def glob(self, pattern: str) -> list[str]:
        normalized_pattern = self._normalize_path(pattern)
        return [path for path in self.walk("/") if fnmatch.fnmatch(path, normalized_pattern)]

    def read_text(self, path: str, default: str = "") -> str:
        value = self.read(path)
        text = self._content_to_text(value)
        return text if text is not None else default

    def serialize(self) -> dict[str, Any]:
        return self._serialize_node(self.root_node)

    @classmethod
    def _serialize_node(cls, node: VfsNode) -> dict[str, Any]:
        return {
            "name": node.name,
            "is_directory": node.is_directory,
            "content": None if node.is_symbolic or node.is_directory else node.content,
            "children": {name: cls._serialize_node(child) for name, child in sorted(node.children.items())},
            "is_symbolic": node.is_symbolic,
        }

    def deserialize(self, data: dict[str, Any]) -> None:
        self.root_node = self._deserialize_node(data)

    @classmethod
    def _deserialize_node(cls, data: dict[str, Any]) -> VfsNode:
        node = VfsNode(
            name=str(data.get("name") or ""),
            is_directory=bool(data.get("is_directory", False)),
            content=data.get("content"),
        )
        node.children = {
            str(name): cls._deserialize_node(child)
            for name, child in dict(data.get("children") or {}).items()
        }
        return node