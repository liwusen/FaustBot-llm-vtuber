from __future__ import annotations

import asyncio
import os
from typing import Any

import numpy as np
from faust_backend.logger import get_logger
try:
    import torch
except ModuleNotFoundError:
    torch = None


log = get_logger("faust.vad")

if torch is None:
    log.critical("PyTorch 未安装，VAD 语音检测不可用。")

SAMPLE_RATE = 16000
WINDOW_SIZE = 512
VAD_THRESHOLD = 0.5


class VadUnavailableError(RuntimeError):
    """VAD 依赖（PyTorch）缺失或模型加载失败时抛出，服务保持可用但不做检测。"""


class VadRuntime:
    def __init__(self) -> None:
        self._model: Any = None
        self._active_connections = 0
        self._unavailable_reason: str | None = None
        self._state_lock = asyncio.Lock()

    async def startup(self) -> None:
        async with self._state_lock:
            if self._model is not None:
                return
            try:
                self._model = await asyncio.to_thread(self._load_model)
                self._unavailable_reason = None
            except VadUnavailableError as e:
                self._model = None
                self._unavailable_reason = str(e)
                log.error("VAD 不可用: %s", e)
            except Exception as e:
                self._model = None
                self._unavailable_reason = f"VAD 模型加载失败: {e}"
                log.error("VAD 模型加载失败: %s", e)

    async def shutdown(self) -> None:
        async with self._state_lock:
            self._model = None
            self._active_connections = 0
            self._unavailable_reason = None

    async def connection_opened(self) -> None:
        async with self._state_lock:
            self._active_connections += 1

    async def connection_closed(self) -> None:
        async with self._state_lock:
            if self._active_connections > 0:
                self._active_connections -= 1

    async def infer_frame(self, audio: np.ndarray) -> dict[str, float | bool]:
        if self._unavailable_reason is not None:
            # VAD 依赖缺失：不崩溃，返回降级结果（视为无语音）
            return {
                "is_speech": False,
                "probability": 0.0,
                "error": self._unavailable_reason,
            }
        if self._model is None:
            await self.startup()
        if self._model is None or self._unavailable_reason is not None:
            return {
                "is_speech": False,
                "probability": 0.0,
                "error": self._unavailable_reason or "VAD 模型未加载",
            }
        probability = await asyncio.to_thread(self._infer_sync, audio)
        return {
            "is_speech": probability > VAD_THRESHOLD,
            "probability": float(probability),
        }

    async def status(self) -> dict[str, int | bool]:
        async with self._state_lock:
            return {
                "is_loaded": self._model is not None,
                "is_running": self._active_connections > 0,
                "active_connections": self._active_connections,
                "sample_rate": SAMPLE_RATE,
                "window_size": WINDOW_SIZE,
                "threshold": VAD_THRESHOLD,
                "unavailable_reason": self._unavailable_reason,
            }

    def _load_model(self):
        if torch is None:
            raise VadUnavailableError(
                "PyTorch 未安装，VAD 语音检测不可用。"
                "请运行 setup-runtime.bat --torch cpu 安装 PyTorch 后重试。"
            )
        model_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        torch_hub_dir = os.path.join(model_root, "asr-hub", "model", "torch_hub")
        os.makedirs(torch_hub_dir, exist_ok=True)
        torch.hub.set_dir(torch_hub_dir)

        # Check if model is already cached locally — avoid any network access
        cache_dir = os.path.join(torch_hub_dir, "snakers4_silero-vad_master")
        cached = os.path.isdir(cache_dir) and os.path.isfile(
            os.path.join(cache_dir, "hubconf.py"))

        if cached:
            model, _ = torch.hub.load(
                repo_or_dir=cache_dir,
                model="silero_vad",
                source="local",
            )
        else:
            model, _ = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                force_reload=False,
                trust_repo=True,
                onnx=False,
                skip_validation=False,
            )
        model.to("cpu")
        model.eval()
        return model

    def _infer_sync(self, audio: np.ndarray) -> float:
        if self._model is None:
            raise RuntimeError("VAD model is not loaded")
        frame = np.asarray(audio, dtype=np.float32)
        if frame.ndim != 1 or frame.shape[0] != WINDOW_SIZE:
            raise ValueError(f"unexpected VAD frame shape: {frame.shape}")
        tensor = torch.from_numpy(frame).to("cpu")
        with torch.no_grad():
            probability = self._model(tensor, SAMPLE_RATE).item()
        return float(probability)


vad_runtime = VadRuntime()