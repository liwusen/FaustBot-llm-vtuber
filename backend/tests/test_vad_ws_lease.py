from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _FakeWebSocket:
    """最小 WebSocket 替身：按预设帧序列喂数据，记录发回去的文本。"""

    def __init__(self, frames: list[bytes]) -> None:
        self._frames = list(frames)
        self.sent: list[dict] = []
        self.accepted = False
        self.closed = False

    async def accept(self) -> None:
        self.accepted = True

    async def receive_bytes(self) -> bytes:
        if not self._frames:
            from fastapi import WebSocketDisconnect

            raise WebSocketDisconnect(code=1000)
        return self._frames.pop(0)

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def close(self) -> None:
        self.closed = True


class _FakeHandle:
    def __init__(self, state: str = "ACTIVE") -> None:
        self.state = state
        self.pid = 4242
        self.last_error = None
        self.refcount = 1

    def status(self) -> dict:
        return {
            "name": "VAD",
            "owner": "builtin",
            "state": self.state,
            "phase": None,
            "pid": self.pid,
            "pid_alive": True,
            "last_error": self.last_error,
            "refcount": self.refcount,
            "holders": {"vad_ws:1": 1},
            "queue_depth": 0,
            "idle_seconds": None,
            "uptime_seconds": 3.0,
            "setup_done": True,
            "fingerprint": "1",
            "config": {},
            "invokes_total": 1,
            "invokes_failed": 0,
            "invokes_cancelled": 0,
            "last_invoke_seconds": 0.01,
        }


class _FakeLease:
    def __init__(self, handle: _FakeHandle, results: list) -> None:
        self.name = "VAD"
        self.requirer = "vad_ws:1"
        self.handle = handle
        self._results = list(results)
        self.released = False
        self.ready_calls = 0

    async def wait_until_ready(self, timeout=None) -> None:
        self.ready_calls += 1

    async def invoke(self, data, *, timeout=None):
        item = self._results.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item()
        return item

    async def get_log(self, level=None, limit=None):
        return []

    def release(self) -> None:
        self.released = True


class _FakeManager:
    def __init__(self, lease_factory) -> None:
        self._lease_factory = lease_factory
        self.start_calls = 0
        self.handle_obj = _FakeHandle()

    def handle(self, name: str) -> _FakeHandle:
        return self.handle_obj

    async def startRequire(self, name: str, requirer: str, *, config=None):
        self.start_calls += 1
        return self._lease_factory()


def _frame() -> bytes:
    return np.zeros(512, dtype=np.float32).tobytes()


@pytest.mark.asyncio
async def test_vad_ws_forwards_probability_and_is_speech(monkeypatch):
    import faust_backend.routes.audio as audio_routes

    manager = _FakeManager(lambda: _FakeLease(_FakeHandle(), [{"probability": 0.83, "is_speech": True}]))
    monkeypatch.setattr(audio_routes, "get_processor_manager", lambda: manager)

    ws = _FakeWebSocket([_frame()])
    await audio_routes.speech_vad_ws(ws)

    assert ws.accepted is True and ws.closed is True
    assert ws.sent == [{"probability": 0.83, "is_speech": True}]


@pytest.mark.asyncio
async def test_vad_ws_degrades_with_error_field_and_backs_off(monkeypatch):
    from faust_backend.processors.errors import ProcessorStartError

    import faust_backend.routes.audio as audio_routes

    def _raise():
        raise ProcessorStartError("torch 缺失", last_error="ModuleNotFoundError: torch")

    manager = _FakeManager(_raise)
    monkeypatch.setattr(audio_routes, "get_processor_manager", lambda: manager)

    ws = _FakeWebSocket([_frame(), _frame()])
    await audio_routes.speech_vad_ws(ws)

    assert len(ws.sent) == 2
    for item in ws.sent:
        assert item["is_speech"] is False
        assert item["probability"] == 0.0
        assert "torch 缺失" in item["error"]   # 降级帧必须带 error，前端据此排除 PTT 命中率统计
    assert manager.start_calls == 1            # 后续帧落在退避窗口内，不再拉起子进程


@pytest.mark.asyncio
async def test_vad_ws_self_heals_after_worker_crash(monkeypatch):
    """同一连接里 worker 崩溃后：换新 lease 并继续回帧（自愈）。"""
    import faust_backend.routes.audio as audio_routes

    handle = _FakeHandle(state="ACTIVE")
    leases: list[_FakeLease] = []

    def _factory() -> _FakeLease:
        lease = _FakeLease(handle, [{"probability": 0.4, "is_speech": False}])
        leases.append(lease)
        return lease

    manager = _FakeManager(_factory)
    monkeypatch.setattr(audio_routes, "get_processor_manager", lambda: manager)

    # 第二帧之前把 handle 标记为 STOPPED，模拟 worker 崩溃/被 prune 回收
    class _CrashAfterFirstFrameWs(_FakeWebSocket):
        async def receive_bytes(self) -> bytes:
            if self._frames and len(self._frames) == 1:
                handle.state = "STOPPED"
            return await super().receive_bytes()

    ws = _CrashAfterFirstFrameWs([_frame(), _frame()])
    await audio_routes.speech_vad_ws(ws)

    assert manager.start_calls == 2                                   # 崩溃后重新 require
    assert leases[0].released is True                                 # 旧 lease 已释放
    assert ws.sent == [
        {"probability": 0.4, "is_speech": False},
        {"probability": 0.4, "is_speech": False},
    ]


@pytest.mark.asyncio
async def test_vad_ws_releases_new_lease_when_self_heal_fails(monkeypatch):
    """自愈时新 lease 起不来：新 lease 也必须释放，否则引用计数永久残留。"""
    from faust_backend.processors.errors import ProcessorStartError

    import faust_backend.routes.audio as audio_routes

    handle = _FakeHandle(state="ACTIVE")

    class _NeverReadyLease(_FakeLease):
        async def wait_until_ready(self, timeout=None) -> None:
            raise ProcessorStartError("VAD worker 起不来", last_error="boom")

    leases: list[_FakeLease] = []

    def _factory() -> _FakeLease:
        if not leases:
            lease: _FakeLease = _FakeLease(handle, [{"probability": 0.4, "is_speech": False}])
        else:
            lease = _NeverReadyLease(handle, [])
        leases.append(lease)
        return lease

    manager = _FakeManager(_factory)
    monkeypatch.setattr(audio_routes, "get_processor_manager", lambda: manager)

    class _CrashAfterFirstFrameWs(_FakeWebSocket):
        async def receive_bytes(self) -> bytes:
            if self._frames and len(self._frames) == 1:
                handle.state = "STOPPED"
            return await super().receive_bytes()

    ws = _CrashAfterFirstFrameWs([_frame(), _frame()])
    await audio_routes.speech_vad_ws(ws)

    assert manager.start_calls == 2                 # 崩溃后重新 require
    assert leases[0].released is True               # 旧 lease 已释放
    assert leases[1].released is True               # 新 lease 起不来也必须释放（否则 refcount 永久 >= 1）
    assert "VAD worker 起不来" in (ws.sent[-1].get("error") or "")


@pytest.mark.asyncio
async def test_vad_ws_releases_lease_on_disconnect(monkeypatch):
    import faust_backend.routes.audio as audio_routes

    lease = _FakeLease(_FakeHandle(), [{"probability": 0.1, "is_speech": False}])
    manager = _FakeManager(lambda: lease)
    monkeypatch.setattr(audio_routes, "get_processor_manager", lambda: manager)

    await audio_routes.speech_vad_ws(_FakeWebSocket([]))
    assert lease.released is True


@pytest.mark.asyncio
async def test_vad_status_keeps_legacy_fields(monkeypatch):
    import faust_backend.routes.audio as audio_routes

    manager = _FakeManager(lambda: _FakeLease(_FakeHandle(), []))
    manager.handle_obj.state = "ACTIVE"
    manager.handle_obj.refcount = 2
    manager.handle_obj.last_error = None
    monkeypatch.setattr(audio_routes, "get_processor_manager", lambda: manager)

    payload = await audio_routes.speech_vad_status_get()

    for key in (
        "is_loaded",
        "is_running",
        "active_connections",
        "sample_rate",
        "window_size",
        "threshold",
        "unavailable_reason",
        "state",
        "last_error",
        "pid",
        "refcount",
    ):
        assert key in payload, key
    assert payload["is_loaded"] is True
    assert payload["active_connections"] == 2
    assert (payload["sample_rate"], payload["window_size"], payload["threshold"]) == (16000, 512, 0.5)
