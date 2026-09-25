from __future__ import annotations
from faust_backend.memory.store import GraphStore

_STORES: dict[str, GraphStore] = {}


def get_memory(agent_name: str | None = None, *, refresh: bool = False) -> GraphStore:
    """取指定 Agent 的记忆库实例（默认当前激活 Agent）。

    同一 Agent 在进程内只保留一个实例：主 Agent 工具、Araya 维护运行时与 memory API
    共享同一份 nx 投影，任一方的写入对其他人立即可见。

    `agent_name` 必须显式传给「维护别的 Agent 记忆库」的调用方（如 Araya）：
    只按全局 `conf.AGENT_NAME` 取实例，会让前端切换激活 Agent 时维护目标中途换库。
    `refresh=True` 丢弃全部缓存后重建，拿到磁盘上的最新状态（生命周期重建/切换 Agent 时用）。
    """
    if refresh:
        # 与旧实现一致：只丢弃引用、不 close——可能仍有协程在用旧实例。
        _STORES.clear()

    import faust_backend.config_loader as conf
    name = str(agent_name or conf.AGENT_NAME)
    store = _STORES.get(name)
    if store is None:
        store = GraphStore(name)
        _STORES[name] = store
    return store
