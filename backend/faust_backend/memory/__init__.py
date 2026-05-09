from __future__ import annotations
from faust_backend.memory.store import GraphStore

_MEMORY: GraphStore | None = None


def get_memory(refresh: bool = False) -> GraphStore:
    global _MEMORY
    if _MEMORY is None or refresh:
        import faust_backend.config_loader as conf
        from faust_backend.memory.store import GraphStore
        _MEMORY = GraphStore(conf.AGENT_NAME)
    elif refresh:
        _MEMORY.refresh(conf.AGENT_NAME)
    return _MEMORY
