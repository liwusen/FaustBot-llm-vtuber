"""
OutputStore — tool output as addressable resources.

Stores tool execution outputs under stable artifact IDs so they can be
truncated for LLM context and re-read on demand via artifact:// URIs.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_MAX_SUMMARY = 60000  # characters in the summary truncation
DEFAULT_MAX_LINES = 300  # max lines to show in summary before truncation


@dataclass
class Artifact:
    output_id: str
    content: str                          # text content
    tool_name: str
    content_type: str = "text"            # "text" | "image" | "multimodal"
    content_base64: str | None = None     # base64-encoded binary
    mime_type: str | None = None          # e.g. "image/png"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    @property
    def size(self) -> int:
        return len(self.content) + (len(self.content_base64 or "") // 4 * 3)

    @property
    def lines(self) -> int:
        return self.content.count("\n") + (1 if self.content else 0)

    def summary(self, max_chars: int = DEFAULT_MAX_SUMMARY,
                max_lines: int = DEFAULT_MAX_LINES) -> str:
        """Return a truncated preview suitable for LLM context injection."""
        if self.content_type == "multimodal":
            n = self.metadata.get("image_count", 0)
            desc = self.content or "图片输出"
            return f"[{desc} — {n}张图片: artifact://{self.output_id}]"
        if self.content_type == "image":
            return f"[图片: artifact://{self.output_id}]"
        if not self.content:
            return "(空输出)"
        lines_obj = self.content.split("\n")
        if len(lines_obj) <= max_lines and len(self.content) <= max_chars:
            return self.content
        preview_lines = lines_obj[:max_lines]
        preview = "\n".join(preview_lines)
        if len(preview) > max_chars:
            preview = preview[:max_chars] + "…"
        footer = f"\n[完整输出: artifact://{self.output_id}]"
        return preview + footer

    def get(self, offset: int = 0, limit: int | None = None) -> str:
        """Retrieve content with pagination (0-indexed line offset)."""
        if self.content_type in ("image", "multimodal") and self.content_base64:
            import json as _json
            return _json.dumps({
                "kind": "multimodal_tool_result",
                "text": self.content,
                "images": [{"url": f"data:{self.mime_type};base64,{self.content_base64}"}],
            }, ensure_ascii=False)
        if self.content_type == "multimodal":
            import json as _json
            images = self.metadata.get("images", []) or []
            return _json.dumps(images, ensure_ascii=False)
        if not self.content:
            return ""
        lines_obj = self.content.split("\n")
        if limit is None:
            return "\n".join(lines_obj[offset:])
        return "\n".join(lines_obj[offset:offset + limit])

class OutputStore:
    """Thread-safe in-memory store of tool outputs, indexable by artifact ID.

    Callers should use the module-level singleton via get_output_store().
    """

    def __init__(self, storage_path: str | None = None):
        self._artifacts: dict[str, Artifact] = {}
        self._counter: int = 0
        self._storage_path = str(storage_path or "").strip()
        self._load()

    @property
    def storage_path(self) -> str:
        return self._storage_path

    def _load(self) -> None:
        if not self._storage_path or not os.path.exists(self._storage_path):
            return
        try:
            payload = json.loads(Path(self._storage_path).read_text(encoding="utf-8"))
        except Exception:
            return
        self._counter = int(payload.get("counter") or 0)
        self._artifacts = {}
        for item in list(payload.get("artifacts") or []):
            try:
                artifact = Artifact(
                    output_id=str(item.get("output_id") or ""),
                    content=str(item.get("content") or ""),
                    tool_name=str(item.get("tool_name") or ""),
                    content_type=str(item.get("content_type") or "text"),
                    content_base64=item.get("content_base64"),
                    mime_type=item.get("mime_type"),
                    metadata=dict(item.get("metadata") or {}),
                    created_at=float(item.get("created_at") or time.time()),
                )
            except Exception:
                continue
            if artifact.output_id:
                self._artifacts[artifact.output_id] = artifact

    def _save(self) -> None:
        if not self._storage_path:
            return
        payload = {
            "counter": self._counter,
            "artifacts": [
                {
                    "output_id": art.output_id,
                    "content": art.content,
                    "tool_name": art.tool_name,
                    "content_type": art.content_type,
                    "content_base64": art.content_base64,
                    "mime_type": art.mime_type,
                    "metadata": art.metadata,
                    "created_at": art.created_at,
                }
                for art in self._artifacts.values()
            ],
        }
        path = Path(self._storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def put(self, content: str, *, tool_name: str = "",
            metadata: dict[str, Any] | None = None) -> str:
        """Store output and return its artifact ID.

        ID format: {tool_short}_{counter}, e.g. "shell_3"
        """
        short = _short_tool_name(tool_name)
        self._counter += 1
        output_id = f"{short}_{self._counter}"
        self._artifacts[output_id] = Artifact(
            output_id=output_id,
            content=str(content or ""),
            tool_name=tool_name,
            metadata=dict(metadata or {}),
        )
        self._save()
        return output_id

    def put_multimodal(self, payload: dict, tool_name: str = "unknown") -> str:
        """Store a multimodal_tool_result payload as an artifact."""
        short = _short_tool_name(tool_name)
        self._counter += 1
        output_id = f"{short}_{self._counter}"
        images = payload.get("images") or []
        mime = None
        b64 = None
        if images:
            first = images[0]
            url = str(first.get("url") or "")
            if url.startswith("data:"):
                header, b64 = url.split(",", 1)
                mime = header[len("data:"):].removesuffix(";base64")
        text = str(payload.get("text") or "")
        self._artifacts[output_id] = Artifact(
            output_id=output_id,
            content=text,
            tool_name=tool_name,
            content_type="multimodal",
            content_base64=b64,
            mime_type=mime,
            metadata={"image_count": len(images), "images": images},
        )
        self._save()
        return output_id

    def get(self, output_id: str) -> Artifact | None:
        """Retrieve an artifact by ID."""
        return self._artifacts.get(output_id)
    def peek(self, output_id: str) -> dict[str, Any] | None:
        """Return metadata-only view: {output_id, tool_name, size, lines, created_at}."""
        art = self._artifacts.get(output_id)
        if art is None:
            return None
        return {
            "output_id": art.output_id,
            "tool_name": art.tool_name,
            "size": art.size,
            "lines": art.lines,
            "created_at": art.created_at,
        }

    def summary(self, output_id: str) -> str:
        """Get truncated summary string for LLM context."""
        art = self._artifacts.get(output_id)
        if art is None:
            return f"[找不到 artifact: {output_id}]"
        return art.summary()

    def list_ids(self) -> list[str]:
        """Return all artifact IDs (newest last)."""
        return sorted(self._artifacts.keys(), key=lambda k: self._artifacts[k].created_at)

    def clear(self) -> None:
        """Remove all artifacts."""
        self._artifacts.clear()
        self._counter = 0
        self._save()

    def __contains__(self, output_id: str) -> bool:
        return output_id in self._artifacts


def _short_tool_name(tool_name: str) -> str:
    """Project a tool name to a shorthand for artifact IDs."""
    name = str(tool_name or "tool").strip()
    # Strip common suffixes
    for suffix in ("Tool", "_tool", "Func"):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    # Map known tools to short names
    known = {
        "sys_exec": "shell",
        "python_exec": "py",
        "read_text_file": "read",
        "write_text_file": "write",
        "list_directory": "ls",
        "memory_search": "mem",
        "request_human_approval": "hil",
        "show_nimble_window": "nimble",
    }
    return known.get(name.lower(), name.lower()[:12])


# Module-level singleton
_store: OutputStore | None = None


def get_output_store() -> OutputStore:
    global _store
    try:
        from faust_backend.runtime import state
        storage_path = os.path.join(state.AGENT_ROOT, "artifact.json")
    except Exception:
        storage_path = ""
    if _store is None or _store.storage_path != storage_path:
        _store = OutputStore(storage_path=storage_path)
    return _store


def reset_output_store(*, clear_persisted: bool = False) -> None:
    global _store
    if clear_persisted:
        if _store is not None:
            _store.clear()
        else:
            try:
                from faust_backend.runtime import state
                storage_path = os.path.join(state.AGENT_ROOT, "artifact.json")
                if storage_path and os.path.exists(storage_path):
                    os.remove(storage_path)
            except Exception:
                pass
    _store = None
