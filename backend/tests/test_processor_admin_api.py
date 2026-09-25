from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faust_backend.processors.errors import ProcessorNotFoundError


class _StubHandle:
    def __init__(self, name: str, state: str = "ACTIVE") -> None:
        self.name = name
        self.state = state

    def status(self) -> dict:
        return {
            "name": self.name,
            "owner": "builtin",
            "state": self.state,
            "refcount": 1,
            "pid": 1234,
            "last_error": None,
        }

    async def get_log(self, level=None, limit=None):
        return [{"level": "ERROR", "message": "示例日志"}]


class _StubManager:
    def __init__(self) -> None:
        self.stopped: list[str] = []
        self.pruned: list[tuple] = []

    def status(self) -> list[dict]:
        return [_StubHandle("VAD").status()]

    def handle(self, name: str) -> _StubHandle:
        if name != "VAD":
            raise ProcessorNotFoundError(name)
        return _StubHandle("VAD")

    async def stop(self, name: str) -> None:
        if name != "VAD":
            raise ProcessorNotFoundError(name)
        self.stopped.append(name)

    async def prune(self, timeout: float = 37.0, whitelist=None):
        from faust_backend.processors.manager import PruneReport

        if whitelist:
            for name in whitelist:
                if name != "VAD":
                    raise ProcessorNotFoundError(name)
        self.pruned.append((timeout, whitelist))
        return PruneReport(timeout=timeout, items=[{"name": "VAD", "action": "stopped", "reason": "idle"}])

    def handle_names(self) -> list[str]:
        return ["VAD"]


@pytest.fixture
def client(monkeypatch):
    import faust_backend.processors.admin_api as admin_api

    stub = _StubManager()
    monkeypatch.setattr(admin_api, "get_processor_manager", lambda: stub)
    monkeypatch.setattr(admin_api.conf, "PROCESSOR_IDLE_TIMEOUT", 300.0)

    app = FastAPI()
    app.include_router(admin_api.router)
    with TestClient(app) as test_client:
        yield test_client, stub


def test_list_processors(client):
    test_client, _stub = client
    payload = test_client.get("/faust/admin/processors").json()
    assert payload["status"] == "ok"
    assert [item["name"] for item in payload["items"]] == ["VAD"]
    assert "logs" not in payload["items"][0]


def test_list_processors_with_log(client):
    test_client, _stub = client
    payload = test_client.get("/faust/admin/processors?include_log=true").json()
    assert payload["items"][0]["logs"] == [{"level": "ERROR", "message": "示例日志"}]


def test_get_single_processor_and_404(client):
    test_client, _stub = client
    payload = test_client.get("/faust/admin/processors/VAD").json()
    assert payload["item"]["name"] == "VAD"
    assert payload["item"]["logs"][0]["message"] == "示例日志"
    assert test_client.get("/faust/admin/processors/NOPE").status_code == 404


def test_stop_endpoint(client):
    test_client, stub = client
    assert test_client.post("/faust/admin/processors/VAD/stop").json()["status"] == "ok"
    assert stub.stopped == ["VAD"]
    assert test_client.post("/faust/admin/processors/NOPE/stop").status_code == 404


def test_prune_endpoint(client):
    test_client, stub = client
    payload = test_client.post("/faust/admin/processors/prune?timeout=0&whitelist=VAD").json()
    assert payload["items"][0]["action"] == "stopped"
    assert stub.pruned == [(0.0, ["VAD"])]

    payload = test_client.post("/faust/admin/processors/prune").json()
    assert payload["timeout"] == 300.0                 # 缺省取配置里的 PROCESSOR_IDLE_TIMEOUT
    assert stub.pruned[-1] == (300.0, None)

    assert test_client.post("/faust/admin/processors/prune?whitelist=NOPE").status_code == 404
