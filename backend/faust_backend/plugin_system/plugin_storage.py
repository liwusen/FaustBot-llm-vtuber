"""插件 KV 持久存储，带生命周期作用域。

- GLOBAL: 始终保留（跨 Agent 共享，存 <plugin_data_dir>/storage/global.json）
- SESSION: 每个 Agent 一份（存 <plugin_data_dir>/storage/session_<agent>.json），
  会话 clear/compact 时由 chat.py 调用 reset_all_plugin_sessions() 重置为
  register_defaults 注册的默认值。

并发策略与 AgileStorage 相同：同步实现 + RLock + tmp+replace 原子写，
同步/异步 hook 中都可安全调用；文件极小，锁内完成读改写。
"""
import json
import threading
from pathlib import Path
from typing import Any, Optional

_SCOPES = ("global", "session")


class PluginStorage:
    _file_lock = threading.RLock()  # 串行化所有插件存储的磁盘读写（文件极小，足够）

    def __init__(self, plugin_id: str, data_dir: Path, agent_name: str):
        self.plugin_id = str(plugin_id)
        self._dir = Path(data_dir) / "storage"
        self._paths = {
            "global": self._dir / "global.json",
            "session": self._dir / f"session_{agent_name}.json",
        }
        self._agent_name = str(agent_name)
        self._lock = threading.RLock()
        self._caches: dict[str, Optional[dict]] = {"global": None, "session": None}
        self._session_defaults: dict[str, Any] = {}

    def _path(self, scope: str) -> Path:
        if scope not in _SCOPES:
            raise ValueError(f"未知存储作用域: {scope!r}（可选 global/session）")
        return self._paths[scope]

    def _ensure_loaded(self, scope: str) -> dict:
        self._path(scope)  # 先校验作用域
        if self._caches[scope] is None:
            with PluginStorage._file_lock:
                try:
                    self._caches[scope] = dict(json.loads(self._path(scope).read_text(encoding="utf-8")))
                except FileNotFoundError:
                    self._caches[scope] = {}
        return self._caches[scope]

    def _flush(self, scope: str) -> None:
        with PluginStorage._file_lock:
            self._path(scope).parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path(scope).with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._caches[scope], ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._path(scope))

    def register_defaults(self, defaults: dict[str, Any]) -> None:
        """注册 SESSION 作用域键的默认值（clear/compact 重置目标）。"""
        with self._lock:
            self._session_defaults = dict(defaults or {})

    def get(self, scope: str, key: str, default: Any = None) -> Any:
        with self._lock:
            cache = self._ensure_loaded(scope)
            return cache.get(key, default)

    def set(self, scope: str, key: str, value: Any) -> None:
        with self._lock:
            cache = self._ensure_loaded(scope)
            cache[key] = value
            self._flush(scope)

    def delete(self, scope: str, key: str) -> None:
        with self._lock:
            cache = self._ensure_loaded(scope)
            cache.pop(key, None)
            self._flush(scope)

    def reset_session(self) -> None:
        """SESSION 作用域重置为注册默认值（clear/compact 时调用）。"""
        with self._lock:
            with PluginStorage._file_lock:
                try:
                    self._path("session").unlink(missing_ok=True)
                except OSError:
                    pass
            self._caches["session"] = None
            if self._session_defaults:
                cache = self._ensure_loaded("session")
                cache.update(json.loads(json.dumps(self._session_defaults)))  # 深拷贝
                self._flush("session")
