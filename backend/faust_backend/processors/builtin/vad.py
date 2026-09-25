"""VAD（语音活动检测）Processor：silero-vad 跑在独立子进程里。"""

from __future__ import annotations

import gc
from pathlib import Path
from typing import Any

import numpy as np

from ..base import Processor, ProcessorContext
from ..registry import processor

SAMPLE_RATE = 16000
WINDOW_SIZE = 512
VAD_THRESHOLD = 0.5

#: backend/ 目录（本文件位于 backend/faust_backend/processors/builtin/）
BACKEND_ROOT = Path(__file__).resolve().parents[3]
#: torch hub 缓存目录：与迁移前 vad_runtime.py / download_vad.py 完全一致
TORCH_HUB_DIR = BACKEND_ROOT / "asr-hub" / "model" / "torch_hub"
SILERO_CACHE_DIR = TORCH_HUB_DIR / "snakers4_silero-vad_master"

#: 依赖缺失时的提示（与迁移前的文案保持一致）
TORCH_MISSING_MESSAGE = (
    "PyTorch 未安装，VAD 语音检测不可用。"
    "请运行 setup-runtime.bat --torch cpu 安装 PyTorch 后重试。"
)


@processor("VAD")
class VadProcessor(Processor):
    """`{"audio": float32[512]}` → `{"probability": float, "is_speech": bool}`。"""

    NAME = "VAD"
    #: 数据目录=torch hub 目录（保持迁移前的磁盘布局：模型缓存与 setup marker 同处）
    DATA_DIR = TORCH_HUB_DIR
    #: 下载 + 构建 silero 模型
    SETUP_TIMEOUT = 900.0
    START_TIMEOUT = 120.0
    STOP_TIMEOUT = 15.0
    INVOKE_TIMEOUT = 10.0

    def __init__(self) -> None:
        self._torch: Any = None
        self._model: Any = None

    def _import_torch(self):
        try:
            import torch
        except ModuleNotFoundError as exc:
            raise RuntimeError(TORCH_MISSING_MESSAGE) from exc
        return torch

    def setup(self, ctx: ProcessorContext) -> None:
        """无缓存时才联网下载（与 download_vad.ensure_vad_cache 同一实现）。"""
        from faust_backend.download_vad import ensure_vad_cache

        ensure_vad_cache(TORCH_HUB_DIR, log_fn=lambda msg: ctx.log(msg))

    def start(self, ctx: ProcessorContext) -> None:
        """只做本地加载：缓存缺失时明确报错，绝不偷偷联网。"""
        torch = self._import_torch()
        ctx.log(f"torch {torch.__version__}，从 {SILERO_CACHE_DIR} 加载 silero-vad")
        torch.hub.set_dir(str(TORCH_HUB_DIR))
        if not (SILERO_CACHE_DIR.is_dir() and (SILERO_CACHE_DIR / "hubconf.py").is_file()):
            raise RuntimeError(f"silero-vad 缓存缺失: {SILERO_CACHE_DIR}（setup 未成功完成）")
        model, _ = torch.hub.load(repo_or_dir=str(SILERO_CACHE_DIR), model="silero_vad", source="local")
        model.to("cpu")
        model.eval()
        self._torch = torch
        self._model = model

    def invoke(self, ctx: ProcessorContext, data: Any) -> dict[str, Any]:
        if self._model is None or self._torch is None:
            raise RuntimeError("VAD 模型未加载")
        audio = data.get("audio") if isinstance(data, dict) else data
        frame = np.asarray(audio, dtype=np.float32)
        if frame.ndim != 1 or frame.shape[0] != WINDOW_SIZE:
            raise ValueError(f"unexpected VAD frame shape: {frame.shape}")
        tensor = self._torch.from_numpy(frame)
        with self._torch.no_grad():
            probability = float(self._model(tensor, SAMPLE_RATE).item())
        return {"probability": probability, "is_speech": probability > VAD_THRESHOLD}

    def stop(self, ctx: ProcessorContext) -> None:
        self._model = None
        self._torch = None
        gc.collect()
