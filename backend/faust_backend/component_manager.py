"""组件管理核心模块。

Phase A: GPU 检测、组件状态检测、Torch 版本映射
Phase D: ServiceGuard 守护进程逻辑
"""

from __future__ import annotations

import asyncio
import subprocess
import importlib.metadata
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


import faust_backend.service_manager as service_manager
from faust_backend.logger import get_logger

log = get_logger("faust.component")

BACKEND_DIR = Path(__file__).resolve().parent.parent

# ── Phase A3: Torch 版本映射 ──

TORCH_VARIANTS: dict[str, dict[str, str | None]] = {
    "cu128": {"index_url": "https://download.pytorch.org/whl/cu128", "cuda": "12.8"},
    "cu121": {"index_url": "https://download.pytorch.org/whl/cu121", "cuda": "12.1"},
    "cu130": {"index_url": "https://download.pytorch.org/whl/cu130", "cuda": "13.0"},
    "cpu": {"index_url": "https://download.pytorch.org/whl/cpu", "cuda": None},
}

ALIYUN_MIRROR_TEMPLATE = "https://mirrors.aliyun.com/pytorch-wheels/{variant}/"


# ── Phase A1: GPU 信息检测 ──

def detect_gpu() -> dict[str, Any]:
    """返回 GPU 名称列表和 CUDA 版本。

    尝试顺序：nvidia-smi → torch（如果已安装）→ 回退空列表。
    """
    result: dict[str, Any] = {"gpus": [], "has_nvidia": False}

    try:
        # nvidia-smi — GPU 名称
        name_proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        if name_proc.returncode == 0:
            names = [n.strip() for n in name_proc.stdout.strip().splitlines() if n.strip()]
        else:
            names = []
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        names = []

    try:
        # nvidia-smi — driver + CUDA 版本
        smi_proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version,compute_cap", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        if smi_proc.returncode == 0:
            lines = smi_proc.stdout.strip().splitlines()
            versions = [l.strip().split(", ") for l in lines if l.strip()]
        else:
            versions = []
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        versions = []

    for i, name in enumerate(names):
        gpu: dict[str, Any] = {"name": name}
        if i < len(versions) and len(versions[i]) >= 2:
            gpu["driver_version"] = versions[i][0]
            gpu["cuda_version"] = versions[i][1]
        result["gpus"].append(gpu)

    result["has_nvidia"] = len(names) > 0

    # fallback: torch
    if not result["has_nvidia"]:
        try:
            import torch
            if torch.cuda.is_available():
                result["has_nvidia"] = True
                count = torch.cuda.device_count()
                for i in range(count):
                    result["gpus"].append({
                        "name": torch.cuda.get_device_name(i),
                        "cuda_version": torch.version.cuda,
                    })
        except Exception:
            pass

    return result


# ── Phase A2: 组件状态检测 ──

def detect_components() -> dict[str, Any]:
    """检测 funasr、TTS、Minecraft 桥的安装/运行状态。

    使用 importlib.metadata 检查包版本，避免 import funasr/torch 触发耗时模型注册。
    """
    components: dict[str, Any] = {}

    # funasr — 用 importlib.metadata 避免触发模型注册表
    funasr: dict[str, Any] = {"installed": False, "version": None, "torch_version": None, "torch_variant": None}
    try:
        funasr["version"] = importlib.metadata.version("funasr")
        funasr["installed"] = True
    except importlib.metadata.PackageNotFoundError:
        pass

    # torch — 同样用 importlib.metadata，从版本字符串推断 variant
    try:
        tv = importlib.metadata.version("torch")
        funasr["torch_version"] = tv
        # 版本格式: "2.1.0+cu121" 或 "2.1.0+cpu"
        if "+" in tv:
            suffix = tv.split("+", 1)[1]
            if suffix == "cpu":
                funasr["torch_variant"] = "cpu"
            elif suffix.startswith("cu"):
                ver = suffix[2:]  # "121" from "cu121"
                for key in ("cu130", "cu128", "cu121"):
                    if ver.startswith(key.replace("cu", "")):
                        funasr["torch_variant"] = key
                        break
                if not funasr["torch_variant"]:
                    funasr["torch_variant"] = f"cuda{ver}"
        else:
            funasr["torch_variant"] = "cpu"
    except importlib.metadata.PackageNotFoundError:
        pass

    components["funasr"] = funasr

    # TTS — 检查 tts-hub 目录
    tts: dict[str, Any] = {"installed": False, "path": None, "variant": None}
    tts_hub = BACKEND_DIR / "tts-hub"
    if tts_hub.is_dir():
        for d in tts_hub.iterdir():
            if d.is_dir() and d.name.startswith("GPT-SoVITS-v2pro"):
                tts["installed"] = True
                tts["path"] = str(d.relative_to(BACKEND_DIR))
                if "nvidia50" in d.name:
                    tts["variant"] = "nvidia50"
                else:
                    tts["variant"] = "standard"
                break
    components["tts"] = tts

    # Minecraft 桥
    mc_status = service_manager.service_status("mc_operator")
    components["minecraft_bridge"] = {
        "enabled": False,
        "is_running": mc_status.get("is_running", False),
    }

    return components


# ── Phase D: 守护进程逻辑 ──

SERVICE_GUARD_CONFIG: dict[str, dict[str, int]] = {
    "asr": {
        "max_restarts": 3,
        "cooldown_seconds": 30,
        "port": 1000,
    },
    "tts": {
        "max_restarts": 3,
        "cooldown_seconds": 30,
        "port": 5000,
    },
    "mc_operator": {
        "max_restarts": 3,
        "cooldown_seconds": 30,
        "port": 18901,
    },
}


class ServiceGuard:
    """一次后端生命周期内，每个服务最多重启 3 次，缓冲 30s。"""

    def __init__(self) -> None:
        self._restart_counts: dict[str, int] = {}
        self._last_attempts: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def start_with_guard(self, service_key: str) -> dict[str, Any]:
        """带守护逻辑的服务启动。返回 service_status dict。"""
        async with self._lock:
            config = SERVICE_GUARD_CONFIG.get(service_key)
            if not config:
                return {"error": f"Unknown service: {service_key}", "is_running": False}

            max_retries: int = config["max_restarts"]
            cooldown: int = config["cooldown_seconds"]

            # 先检查端口是否已监听
            existing = service_manager.service_status(service_key)
            if existing.get("is_running"):
                log.info("服务 %s 已在运行，跳过启动", service_key)
                self._restart_counts.pop(service_key, None)
                return existing

            # 检查重启次数
            attempt = self._restart_counts.get(service_key, 0)
            if attempt >= max_retries:
                # 检查是否已过冷却期（允许尝试外部手动启动）
                last_ts = self._last_attempts.get(service_key, 0.0)
                if time.time() - last_ts < cooldown:
                    return {
                        "error": f"服务 {service_key} 已重试 {attempt} 次，冷却期内不再自动重试",
                        "is_running": False,
                    }
                # 冷却期已过，重置计数
                self._restart_counts[service_key] = 0
                attempt = 0

            log.info("启动服务 %s (attempt %d/%d)", service_key, attempt + 1, max_retries)

            # 调用 service_manager 启动（非阻塞）
            try:
                result = service_manager.start_service(service_key)
            except Exception as e:
                log.error("启动服务 %s 失败: %s", service_key, e)
                result = {"is_running": False, "error": str(e)}

            if result.get("is_running"):
                log.info("服务 %s 启动成功", service_key)
                self._restart_counts.pop(service_key, None)
                self._last_attempts.pop(service_key, None)
                return result

            # 失败或不完全启动状态 — 记录并允许重试
            self._restart_counts[service_key] = attempt + 1
            self._last_attempts[service_key] = time.time()

            if self._restart_counts[service_key] >= max_retries:
                log.error("服务 %s 已重试 %d 次，放弃", service_key, max_retries)
                return {
                    "error": f"服务 {service_key} 启动失败，已重试 {max_retries} 次",
                    "is_running": False,
                }

            log.info("服务 %s 启动失败，冷却 %ds 后重试...", service_key, cooldown)
            await asyncio.sleep(cooldown)
            return await self.start_with_guard(service_key)
    def reset(self, service_key: str) -> None:
        """手动重置计数器（如手动启动成功）。"""
        self._restart_counts.pop(service_key, None)
        self._last_attempts.pop(service_key, None)

    def get_count(self, service_key: str) -> int:
        return self._restart_counts.get(service_key, 0)


# ── 全局实例 ──

_service_guard: ServiceGuard | None = None


def get_service_guard() -> ServiceGuard:
    global _service_guard
    if _service_guard is None:
        _service_guard = ServiceGuard()
    return _service_guard


# ── Phase D3: 自动启动触发器 ──

def get_mc_bridge_enabled() -> bool:
    """读取 MC_BRIDGE_ENABLED 配置值。"""
    try:
        from faust_backend.config_loader import config
        return bool(config.get("MC_BRIDGE_ENABLED", False))
    except Exception:
        return False


async def on_component_installed(component: str, details: dict | None = None) -> None:
    """组件安装完成后的回调。"""
    from faust_backend.config_loader import config
    guard = get_service_guard()
    _ = details

    if component == "funasr":
        if config.get("ASR_MODE", "").lower() == "local":
            await guard.start_with_guard("asr")
    elif component == "tts":
        if config.get("TTS_MODE", "").lower() == "local":
            await guard.start_with_guard("tts")


# ── Phase H: 初始化入口 ──

def init_component_guard() -> None:
    """后端启动时初始化组件守护。"""
    guard = get_service_guard()
    log.info("组件守护已初始化 (mc_operator=%s)", guard.get_count("mc_operator"))


async def check_and_manage_services(old_config: dict, new_config: dict) -> None:
    """配置更新后检查是否需要启停服务。"""
    guard = get_service_guard()

    # ASR_MODE
    if old_config.get("ASR_MODE") != new_config.get("ASR_MODE"):
        mode = (new_config.get("ASR_MODE") or "").lower()
        if mode == "local":
            log.info("Booting ASR service due to config change...")
            await guard.start_with_guard("asr")
        else:
            log.info("Stopping ASR service due to config change...")
            service_manager.stop_service("asr")

    # TTS_MODE
    if old_config.get("TTS_MODE") != new_config.get("TTS_MODE"):
        mode = (new_config.get("TTS_MODE") or "").lower()
        if mode == "local":
            log.info("Booting TTS service due to config change...")
            await guard.start_with_guard("tts")
        else:
            log.info("Stopping TTS service due to config change...")
            service_manager.stop_service("tts")

    # MC_BRIDGE_ENABLED
    if old_config.get("MC_BRIDGE_ENABLED") != new_config.get("MC_BRIDGE_ENABLED"):
        if new_config.get("MC_BRIDGE_ENABLED"):
            log.info("Booting Minecraft Operator service due to config change...")
            await guard.start_with_guard("mc_operator")
        else:
            log.info("Stopping Minecraft Operator service due to config change...")
            service_manager.stop_service("mc_operator")
