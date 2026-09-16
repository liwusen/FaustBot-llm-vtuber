"""并发写与事务原子性（旧实现全量写盘时会产生 *.tmp 竞态）。"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import pytest  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    import faust_backend.config_loader as conf
    import faust_backend.memory.store as store_mod

    monkeypatch.setattr(conf, "CONFIG_ROOT", str(tmp_path))
    monkeypatch.setattr(conf, "AGENT_NAME", "test_agent")
    gs = store_mod.GraphStore("test_agent")

    async def _noop_embed_index(chunk_items):
        return None

    monkeypatch.setattr(gs, "_embed_and_index", _noop_embed_index)
    yield gs
    gs.close()


def test_concurrent_writes_keep_all_rows(store):
    def worker(prefix: str) -> None:
        for i in range(50):
            asyncio.run(store.file_write(f"/conc/{prefix}_{i}.md", f"body {i}"))

    threads = [threading.Thread(target=worker, args=(p,)) for p in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    rows = store.db.all("SELECT path FROM nodes WHERE path LIKE '/conc/%'")
    assert len(rows) == 100
    assert not list((store.store_dir / "index").glob("*.tmp"))


def test_rename_db_state_unchanged_on_failure(store, monkeypatch):
    asyncio.run(store.file_write("/atomic/a.md", "x", description="d"))
    asyncio.run(store.file_write("/atomic/sub/b.md", "y"))

    before_nodes = {(r["id"], r["parent_id"]) for r in store.db.all("SELECT id, parent_id FROM nodes")}
    before_chunks = [(r["node_id"], r["chunk_id"]) for r in store.db.all("SELECT node_id, chunk_id FROM chunks ORDER BY chunk_id")]

    class Boom(RuntimeError):
        pass

    real_transaction = store.db.transaction
    state = {"fail": False}

    def flaky_transaction():
        if state["fail"]:
            raise Boom("injected")
        return real_transaction()

    monkeypatch.setattr(store.db, "transaction", flaky_transaction)
    state["fail"] = True
    with pytest.raises(Boom):
        asyncio.run(store.file_rename("/atomic", "atomic_renamed"))

    after_nodes = {(r["id"], r["parent_id"]) for r in store.db.all("SELECT id, parent_id FROM nodes")}
    after_chunks = [(r["node_id"], r["chunk_id"]) for r in store.db.all("SELECT node_id, chunk_id FROM chunks ORDER BY chunk_id")]
    assert after_nodes == before_nodes
    assert after_chunks == before_chunks


def test_write_critical_section_serialized_across_event_loops(store, monkeypatch):
    """两条线程各自持有一个事件循环并发写：写临界区必须跨循环互斥。

    回归 `asyncio.Lock`：它绑定创建它的循环，跨循环争用会抛
    "is bound to a different event loop"；而临界区内的 `chunks.vdb` 落盘是
    读改写，跨循环重叠会丢更新。
    """
    state = {"active": 0, "peak": 0}
    guard = threading.Lock()

    async def overlapping_embed(chunk_items):
        with guard:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            await asyncio.sleep(0.02)
        finally:
            with guard:
                state["active"] -= 1

    monkeypatch.setattr(store, "_embed_and_index", overlapping_embed)

    def worker(prefix: str) -> None:
        for i in range(10):
            asyncio.run(store.file_write(f"/serial/{prefix}_{i}.md", f"body {i}"))

    threads = [threading.Thread(target=worker, args=(p,)) for p in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert state["peak"] == 1
    rows = store.db.all("SELECT path FROM nodes WHERE path LIKE '/serial/%'")
    assert len(rows) == 20


def test_cancelled_waiter_does_not_wedge_write_lock(store, monkeypatch):
    """等待写锁的写入被取消后，锁必须交还给其他写入者（不能卡死）。"""
    entered = threading.Event()
    release = threading.Event()

    async def blocked_embed(chunk_items):
        entered.set()
        await asyncio.to_thread(release.wait, 10.0)

    monkeypatch.setattr(store, "_embed_and_index", blocked_embed)

    holder = threading.Thread(target=lambda: asyncio.run(store.file_write("/lock/hold.md", "h")))
    holder.start()
    try:
        assert entered.wait(10.0)

        with pytest.raises(TimeoutError):
            asyncio.run(asyncio.wait_for(store.file_write("/lock/wait.md", "w"), 0.3))
    finally:
        release.set()
    holder.join(10.0)
    assert not holder.is_alive()

    asyncio.run(asyncio.wait_for(store.file_write("/lock/after.md", "a"), 10.0))
    assert store.db.one("SELECT count(*) AS c FROM nodes WHERE path='/lock/after.md'")["c"] == 1


def test_cancelled_waiter_after_handoff_returns_lock():
    """回归：持有权已交出、等待者尚未恢复时的取消，不能永久丢锁。

    `_handoff_locked` 弹出 waiter B 的 future 并排程唤起之后，B 的 `await fut` 在
    回调跑起来之前被取消：future 既不在 `_waiters`（已弹出）也没有结果（被取消），
    旧实现 `fut.done() and not fut.cancelled()` 与 `fut in self._waiters` 两个分支
    都不进，锁从此没人持有，后续写入全部挂死。
    """
    import faust_backend.memory.store as store_mod

    async def scenario():
        lock = store_mod._CrossLoopLock()
        await lock.__aenter__()  # 主协程持锁
        waiter = asyncio.ensure_future(lock.__aenter__())
        await asyncio.sleep(0)  # B 入队并挂在 future 上
        lock._handoff_locked()  # 持有权交给 B（回调已排程，B 还没恢复）
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        # 另一个等待者必须能拿到锁；拿不到就会超时（而不是挂死）
        await asyncio.wait_for(lock.__aenter__(), 1.0)
        await lock.__aexit__()

    asyncio.run(scenario())


def test_concurrent_new_ancestor_creation_never_links_missing_parent(store, monkeypatch):
    """回归：祖先创建必须在写锁内，且 `_add_node` 先写 SQL 再改 nx。

    否则线程 B 会从 nx 看到线程 A 尚未落库的父目录而跳过插入，紧接着
    `_link_parent` 的 `UPDATE ... SET parent_id=<不存在的父>` 撞
    `FOREIGN KEY constraint failed`。这里用线程定向延迟把 `_add_node` 的
    「nx 已改 / SQL 未提交」窗口放大成确定性可复现（没有延迟时这个窗口只有微秒级，
    并发压测碰不到）。
    """
    real_transaction = store.db.transaction
    entered = threading.Event()

    def delayed_transaction():
        if threading.current_thread().name == "slow" and not entered.is_set():
            entered.set()
            time.sleep(0.3)
        return real_transaction()

    monkeypatch.setattr(store.db, "transaction", delayed_transaction)

    errors: list[BaseException] = []

    def worker(tag: str) -> None:
        try:
            for i in range(5):
                asyncio.run(store.file_write(f"/race/{tag}_{i}.md", "body", index=False))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    slow = threading.Thread(target=worker, args=("a",), name="slow")
    fast = threading.Thread(target=worker, args=("b",), name="fast")
    slow.start()
    assert entered.wait(5.0), "slow 线程没有进入第一次事务"
    fast.start()
    for t in (fast, slow):
        t.join(20.0)
        assert not t.is_alive()

    assert errors == []
    assert store.db.one("SELECT count(*) AS c FROM nodes WHERE path LIKE '/race/%'")["c"] == 10
    assert store.db.one(
        "SELECT count(*) AS c FROM nodes c LEFT JOIN nodes p ON c.parent_id=p.id"
        " WHERE c.parent_id IS NOT NULL AND p.id IS NULL")["c"] == 0

