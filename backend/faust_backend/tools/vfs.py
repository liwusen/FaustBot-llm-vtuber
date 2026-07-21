"""Virtual filesystem public API.

The async implementation lives in _faust_vfs_runtime.py. This module keeps the
stable import path while exposing the async VFS, sync compatibility helpers,
and the faustbot:// singleton accessor.
"""

from ._faust_vfs_runtime import (
    AsyncRWLock,
    AsyncVirtualFileSystem,
    VfsNode,
    ensure_source_file,
    get_faustbot_vfs,
    refresh_runtime_nodes,
    run_coro_sync,
)

VirtualFileSystem = AsyncVirtualFileSystem

__all__ = [
    "AsyncRWLock",
    "AsyncVirtualFileSystem",
    "ensure_source_file",
    "VfsNode",
    "VirtualFileSystem",
    "get_faustbot_vfs",
    "refresh_runtime_nodes",
    "run_coro_sync",
]