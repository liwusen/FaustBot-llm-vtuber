"""OCR Processor：easyocr 跑在独立子进程里。

截图与坐标归一化留在主进程（需要屏幕上下文），这里只负责识别。
"""

from __future__ import annotations

import gc
from pathlib import Path
from typing import Any

import numpy as np

import faust_backend.config_loader as conf

from ..base import Processor, ProcessorContext
from ..registry import processor

DEFAULT_LANGS = ["ch_sim", "en"]
#: 模型目录：不污染用户目录（与插件原来的 default 目录一致）
MODEL_DIR = Path(conf.MODEL_ROOT) / "easyocr"


def normalize_config(config: dict[str, Any]) -> tuple[list[str], bool]:
    """归一 ``{"langs": [...], "gpu": bool}``：缺省 langs=["ch_sim","en"]、gpu=False。"""
    raw_langs = config.get("langs")
    if isinstance(raw_langs, str):
        raw_langs = [item.strip() for item in raw_langs.split(",") if item.strip()]
    langs = [str(item) for item in (raw_langs or DEFAULT_LANGS) if str(item).strip()]
    return (langs or list(DEFAULT_LANGS)), bool(config.get("gpu", False))


@processor("OCR")
class OcrProcessor(Processor):
    """`{"image": uint8[H,W,3|4], "detail": 0|1}` → 文本/置信度/检测框。"""

    NAME = "OCR"
    SETUP_TIMEOUT = 1800.0     # 首次下载模型可能很久
    START_TIMEOUT = 300.0
    STOP_TIMEOUT = 20.0
    INVOKE_TIMEOUT = 120.0

    def __init__(self) -> None:
        self._reader: Any = None
        self._langs: list[str] = list(DEFAULT_LANGS)
        self._gpu = False

    def setup_fingerprint(self, config: dict[str, Any]) -> str:
        """语言变化会重新 setup（模型文件与语言相关）。"""
        langs, _gpu = normalize_config(config)
        return f"{self.SETUP_VERSION}:{'|'.join(sorted(langs))}"

    def _apply_config(self, ctx: ProcessorContext) -> None:
        self._langs, self._gpu = normalize_config(ctx.config)
        ctx.log(f"OCR 配置: langs={self._langs} gpu={self._gpu}")

    def _create_reader(self, ctx: ProcessorContext):
        import easyocr  # 重依赖只在钩子内 import

        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        ctx.log(f"构造 easyocr.Reader（模型目录 {MODEL_DIR}）")
        return easyocr.Reader(
            self._langs,
            gpu=self._gpu,
            model_storage_directory=str(MODEL_DIR),
            user_network_directory=str(MODEL_DIR),
            verbose=False,
        )

    def setup(self, ctx: ProcessorContext) -> None:
        """构造一次 Reader 触发模型下载/校验，随后丢弃。"""
        self._apply_config(ctx)
        reader = self._create_reader(ctx)
        del reader
        gc.collect()

    def start(self, ctx: ProcessorContext) -> None:
        self._apply_config(ctx)
        self._reader = self._create_reader(ctx)

    def invoke(self, ctx: ProcessorContext, data: Any) -> Any:
        if self._reader is None:
            raise RuntimeError("OCR Reader 未加载")
        payload = data if isinstance(data, dict) else {"image": data}
        image = np.asarray(payload.get("image"), dtype=np.uint8)
        if image.ndim != 3 or image.shape[2] not in (3, 4):
            raise ValueError(f"unexpected screenshot shape: {image.shape}")
        if image.shape[2] == 4:
            image = image[:, :, :3]
        detail = int(payload.get("detail", 1))

        raw_items = self._reader.readtext(image, detail=detail)
        if detail <= 0:
            return [str(item) for item in raw_items]

        results: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                continue
            box, text, confidence = item[0], item[1], item[2]
            results.append(
                {
                    "text": str(text),
                    "confidence": float(confidence),
                    "box": [[float(point[0]), float(point[1])] for point in box],
                }
            )
        return results

    def stop(self, ctx: ProcessorContext) -> None:
        self._reader = None
        gc.collect()
        if self._gpu:
            try:
                import torch

                torch.cuda.empty_cache()
            except Exception as exc:  # noqa: BLE001 - 释放失败不影响停止
                ctx.log(f"torch.cuda.empty_cache 失败: {exc}", level="WARNING")
