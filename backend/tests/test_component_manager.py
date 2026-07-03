"""Tests for component management (Phase 08).

Covers: GPU detection, component detection, ServiceGuard, API.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import importlib.metadata
import pytest

# Ensure backend is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Fixtures ──


@pytest.fixture
def reset_guard():
    """Reset ServiceGuard singleton between tests."""
    from faust_backend.component_manager import _service_guard as sg

    _ = sg
    yield
    from faust_backend.component_manager import _service_guard as guard

    if guard is not None:
        guard._restart_counts.clear()
        guard._last_attempts.clear()


# ── Test GPU Detection ──


class TestDetectGpu:
    def test_no_nvidia_smi_fallback_empty(self):
        """nvidia-smi not available -> has_nvidia=False, empty list."""
        from faust_backend.component_manager import detect_gpu

        with patch("subprocess.run", side_effect=FileNotFoundError("no nvidia-smi")):
            result = detect_gpu()

        assert result["has_nvidia"] is False
        assert result["gpus"] == []

    def test_nvidia_smi_returns_gpus(self):
        """nvidia-smi returns GPU info."""
        from faust_backend.component_manager import detect_gpu

        fake_name_proc = MagicMock()
        fake_name_proc.returncode = 0
        fake_name_proc.stdout = "NVIDIA GeForce RTX 4090\n"

        fake_smi_proc = MagicMock()
        fake_smi_proc.returncode = 0
        fake_smi_proc.stdout = "560.94, 12.8\n"

        with patch("subprocess.run", side_effect=[fake_name_proc, fake_smi_proc]):
            result = detect_gpu()

        assert result["has_nvidia"] is True
        assert len(result["gpus"]) == 1
        assert result["gpus"][0]["name"] == "NVIDIA GeForce RTX 4090"
        assert result["gpus"][0]["cuda_version"] == "12.8"
        assert result["gpus"][0]["driver_version"] == "560.94"


# ── Test Component Detection ──


class TestDetectComponents:
    def test_funasr_not_installed(self):
        """funasr not installed, service_manager mocked."""
        from faust_backend.component_manager import detect_components
        from faust_backend import service_manager

        with patch("importlib.metadata.version", side_effect=importlib.metadata.PackageNotFoundError):
            with patch.object(service_manager, "service_status", return_value={"is_running": False}):
                result = detect_components()

        assert result["funasr"]["installed"] is False

    def test_tts_not_installed(self):
        """tts-hub directory missing (mocked service_manager)."""
        from faust_backend.component_manager import detect_components
        from faust_backend import service_manager

        with patch.object(service_manager, "service_status", return_value={"is_running": False}):
            result = detect_components()

        assert result["tts"]["installed"] is False

    def test_minecraft_bridge_status_from_service_manager(self):
        """Minecraft bridge status reflects service_manager."""
        from faust_backend.component_manager import detect_components
        from faust_backend import service_manager

        original = service_manager.service_status

        def fake_status(key):
            if key == "mc_operator":
                return {"is_running": True, "port": 18901}
            return original(key)

        with patch.object(service_manager, "service_status", fake_status):
            result = detect_components()
            assert result["minecraft_bridge"]["is_running"] is True

    def test_minecraft_bridge_not_running(self):
        """Minecraft bridge not running."""
        from faust_backend.component_manager import detect_components
        from faust_backend import service_manager

        with patch.object(service_manager, "service_status", return_value={"is_running": False}):
            result = detect_components()
            assert result["minecraft_bridge"]["is_running"] is False


# ── Test Torch Variants ──


class TestTorchVariants:
    def test_all_variants_have_expected_keys(self):
        from faust_backend.component_manager import TORCH_VARIANTS

        for key, val in TORCH_VARIANTS.items():
            assert "index_url" in val, f"{key} missing index_url"
            assert "cuda" in val or key == "cpu", f"{key} missing cuda"

    def test_aliyun_mirror_template(self):
        from faust_backend.component_manager import ALIYUN_MIRROR_TEMPLATE

        url = ALIYUN_MIRROR_TEMPLATE.format(variant="cu128")
        assert "cu128" in url
        assert url.startswith("https://mirrors.aliyun.com")


# ── Test ComponentTask Model ──


class TestComponentTask:
    def test_to_dict_includes_all_fields(self):
        from faust_backend.component_api import ComponentTask

        task = ComponentTask(
            task_id="test-1",
            component="funasr",
            status="running",
            progress_percent=45.0,
            stage="torch_done",
            log_lines=["line1", "line2"],
        )
        d = task.to_dict()
        assert d["task_id"] == "test-1"
        assert d["component"] == "funasr"
        assert d["status"] == "running"
        assert d["progress_percent"] == 45.0
        assert d["stage"] == "torch_done"
        assert d["log_lines"] == ["line1", "line2"]
        assert d["error"] is None

    def test_log_lines_capped_at_200(self):
        from faust_backend.component_api import ComponentTask

        task = ComponentTask(
            task_id="test-2", component="tts",
            log_lines=[f"line{i}" for i in range(300)],
        )
        d = task.to_dict()
        assert len(d["log_lines"]) <= 200


# ── Test ServiceGuard ──


class TestServiceGuard:
    @pytest.mark.asyncio
    async def test_start_with_guard_already_running(self, reset_guard):
        """If service is already running, skip start."""
        from faust_backend.component_manager import ServiceGuard
        from faust_backend import service_manager

        guard = ServiceGuard()

        with patch.object(service_manager, "service_status", return_value={"is_running": True, "port": 1000}):
            result = await guard.start_with_guard("asr")
            assert result["is_running"] is True

    @pytest.mark.asyncio
    async def test_unknown_service_returns_error(self, reset_guard):
        """Unknown service key returns error."""
        from faust_backend.component_manager import ServiceGuard

        guard = ServiceGuard()
        result = await guard.start_with_guard("nonexistent")
        assert "error" in result
        assert result["is_running"] is False

    @pytest.mark.asyncio
    async def test_reset_clears_count(self, reset_guard):
        """Reset clears restart count."""
        from faust_backend.component_manager import ServiceGuard

        guard = ServiceGuard()
        guard._restart_counts["mc_operator"] = 2
        guard.reset("mc_operator")
        assert guard.get_count("mc_operator") == 0

    def test_get_count_returns_zero_for_new_service(self, reset_guard):
        from faust_backend.component_manager import ServiceGuard

        guard = ServiceGuard()
        assert guard.get_count("mc_operator") == 0


# ── Test Status API (integration light) ──


class TestStatusApi:
    @pytest.mark.asyncio
    async def test_status_endpoint_mocked(self):
        """GET /faust/components/status returns expected structure."""
        from faust_backend.component_api import get_component_status
        from faust_backend import service_manager

        fake_gpu = {"gpus": [], "has_nvidia": False}
        fake_components = {
            "funasr": {"installed": False, "version": None, "torch_version": None, "torch_variant": None},
            "tts": {"installed": False, "path": None, "variant": None},
            "minecraft_bridge": {"enabled": False, "is_running": False},
        }
        fake_service = {"is_running": False}

        with patch("faust_backend.component_api.detect_gpu", return_value=fake_gpu), \
             patch("faust_backend.component_api.detect_components", return_value=fake_components), \
             patch("faust_backend.component_api.get_mc_bridge_enabled", return_value=False), \
             patch.object(service_manager, "service_status", return_value=fake_service):
            response = await get_component_status()

        assert response.gpu == fake_gpu
        assert response.components == fake_components
        assert response.services["asr"] == fake_service
        assert response.services["tts"] == fake_service
        assert response.services["minecraft"] == fake_service


# ── Test install endpoint validations ──


class TestInstallApi:
    @pytest.mark.asyncio
    async def test_install_funasr_creates_task(self):
        """POST /faust/components/install returns task_id."""
        from faust_backend.component_api import start_install
        from pydantic import BaseModel

        class FakeReq(BaseModel):
            component: str = "funasr"
            torch_variant: str | None = "cpu"
            use_aliyun_mirror: bool = False
            tts_variant: str | None = None

        with patch("faust_backend.component_api._run_install") as mock_run:
            response = await start_install(FakeReq())
            assert response.status == "started"
            assert response.task_id is not None
            assert len(response.task_id) > 0


# ── Test admin_config change trigger ──


class TestConfigChangeTrigger:
    @pytest.mark.asyncio
    async def test_asr_mode_changed_to_local_triggers_start(self):
        """ASR_MODE changes to local -> start_with_guard('asr') called."""
        from faust_backend.component_manager import check_and_manage_services

        old = {"ASR_MODE": "cloud"}
        new = {"ASR_MODE": "local"}

        with patch("faust_backend.component_manager.get_service_guard") as mock_guard:
            guard = AsyncMock()
            mock_guard.return_value = guard
            with patch("faust_backend.service_manager.stop_service"):
                await check_and_manage_services(old, new)
                guard.start_with_guard.assert_called_once_with("asr")

    @pytest.mark.asyncio
    async def test_asr_mode_changed_to_cloud_triggers_stop(self):
        """ASR_MODE changes to cloud -> stop_service('asr') called."""
        from faust_backend.component_manager import check_and_manage_services
        from faust_backend import service_manager

        old = {"ASR_MODE": "local"}
        new = {"ASR_MODE": "cloud"}

        with patch.object(service_manager, "stop_service") as mock_stop:
            with patch("faust_backend.component_manager.get_service_guard") as mock_guard:
                guard = AsyncMock()
                mock_guard.return_value = guard
                await check_and_manage_services(old, new)
                mock_stop.assert_called_once_with("asr")

    @pytest.mark.asyncio
    async def test_mc_bridge_enabled_triggers_start(self):
        """MC_BRIDGE_ENABLED true -> start_with_guard('minecraft') called."""
        from faust_backend.component_manager import check_and_manage_services

        old = {"MC_BRIDGE_ENABLED": False}
        new = {"MC_BRIDGE_ENABLED": True}

        with patch("faust_backend.component_manager.get_service_guard") as mock_guard:
            guard = AsyncMock()
            mock_guard.return_value = guard
            with patch("faust_backend.service_manager.stop_service"):
                await check_and_manage_services(old, new)
                guard.start_with_guard.assert_called_once_with("minecraft")

    @pytest.mark.asyncio
    async def test_mc_bridge_disabled_triggers_stop(self):
        """MC_BRIDGE_ENABLED false -> stop_service('minecraft') called."""
        from faust_backend.component_manager import check_and_manage_services
        from faust_backend import service_manager

        old = {"MC_BRIDGE_ENABLED": True}
        new = {"MC_BRIDGE_ENABLED": False}

        with patch.object(service_manager, "stop_service") as mock_stop:
            with patch("faust_backend.component_manager.get_service_guard") as mock_guard:
                guard = AsyncMock()
                mock_guard.return_value = guard
                await check_and_manage_services(old, new)
                mock_stop.assert_called_once_with("mc_operator")


# ── Test download_torch module ──


class TestDownloadTorch:
    def test_uninstall_not_needed_when_torch_matches(self):
        """Torch version matches target -> no uninstall needed."""
        import download_torch

        mock_torch = MagicMock()
        mock_torch.__version__ = "2.5.1+cu128"
        mock_torch.version.cuda = "12.8"
        mock_torch.cuda.is_available.return_value = True

        with patch.dict(sys.modules, {"torch": mock_torch}):
            result = download_torch.uninstall_torch_if_needed("cu128")
            assert result is False

    def test_install_torch_cpu_success_returns_success(self):
        """install_torch_and_funasr returns success when pip succeeds."""
        import download_torch

        mock_funasr = MagicMock()
        mock_funasr.__version__ = "1.0.0"
        mock_torch = MagicMock()

        with patch("download_torch._run_pip_streaming", return_value=0), \
             patch("download_torch.uninstall_torch_if_needed", return_value=False), \
             patch("download_torch.download_asr_models", return_value={"success": True}), \
             patch.dict(sys.modules, {"funasr": mock_funasr, "torch": mock_torch}):
            result = download_torch.install_torch_and_funasr("cpu", use_aliyun_mirror=False)
            assert result["success"] is True