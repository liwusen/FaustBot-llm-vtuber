"""内置 Processor 的注册入口（导入即完成注册）。"""

from __future__ import annotations

from .ocr import OcrProcessor
from .vad import VadProcessor

__all__ = ["OcrProcessor", "VadProcessor"]
