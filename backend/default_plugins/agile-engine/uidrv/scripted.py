"""ScriptedDecider：预设答案的决策器（单测用，绝不触发真模型 / 真桌面）。

答案形式（按调用顺序消费，可混合）：

- ``"space"``               → 直接选这个选项键，confidence=1.0；
- ``{"choice": "space", "confidence": 0.9, "abstain": 0.05, "valid": True}`` → 原样（形状同 OmniJev）；
- ``{"space": 0.6, "wait": 0.3}`` → 概率表（自动算 confidence / abstain）；
- ``None`` → abstain（等价于 ``{"choice": None, "abstain": 1.0}``）；
- ``callable(question, options, frame) -> 上述任意``。
"""
from __future__ import annotations

from typing import Any, Optional

from .decider import Answer, Frame, LimitedUIDecider, SessionSpec, UIDeps, UIOption

import numpy as np


def synthetic_frame(width: int = 640, height: int = 360, seed: int = 0) -> Any:
    """确定性假画面（非全黑、非零方差、≥64x64），供 ScriptedDecider 默认抓帧。"""
    ys = np.arange(height, dtype="uint16")[:, None]
    xs = np.arange(width, dtype="uint16")[None, :]
    base = ((ys * 3 + xs * 5 + seed * 37) % 251).astype("uint8")
    return np.repeat(base[:, :, None], 3, axis=2)


class ScriptedDecider(LimitedUIDecider):
    """预设答案（不触发真模型）。"""

    def __init__(self, module: str, spec: SessionSpec, hooks: dict[str, Any] | None,
                 deps: UIDeps, answers: Optional[list[Any]] = None) -> None:
        super().__init__(module, spec, hooks, deps)
        self._answers: list[Any] = list(answers or [])
        self.asked: list[tuple[str, tuple[str, ...]]] = []
        self.frame_seed = 0

    async def capture(self) -> Frame:
        rect = self.deps.win.client_rect(self._target_hwnd()) if self.window is not None else (0, 0, 640, 360)
        w = max(64, min(640, rect[2] - rect[0]))
        h = max(64, min(360, rect[3] - rect[1]))
        return self._make_frame(synthetic_frame(w, h, self.frame_seed), rect)

    def set_frame_seed(self, seed: int) -> None:
        """换一个假画面（默认恒定，避免"注入前陈旧检测"误判）。"""
        self.frame_seed = int(seed)

    async def _ask(self, question: str, options: list[UIOption], *, frame: Frame) -> Answer:
        self.asked.append((question, tuple(o.key for o in options)))
        raw = self._answers.pop(0) if self._answers else None
        if callable(raw):
            raw = raw(question, options, frame)
        return _to_answer(raw, options)


def _to_answer(raw: Any, options: list[UIOption]) -> Answer:
    keys = [o.key for o in options]
    if raw is None:
        return Answer(key=None, probabilities={}, confidence=0.0, abstain=1.0)
    if isinstance(raw, Answer):
        return raw
    if isinstance(raw, str):
        key = raw if raw in keys else next((o.key for o in options if o.label == raw), raw)
        return Answer(key=key, probabilities={k: (1.0 if k == key else 0.0) for k in keys},
                      confidence=1.0, abstain=0.0)
    if isinstance(raw, dict):
        if "choice" in raw or "key" in raw:
            key = raw.get("choice", raw.get("key"))
            probs = dict(raw.get("probabilities") or {})
            return Answer(key=key, probabilities=probs,
                          confidence=float(raw.get("confidence", 1.0)),
                          abstain=float(raw.get("abstain", 0.0)),
                          noul=raw.get("noul"),
                          valid=bool(raw.get("valid", True)),
                          latency_s=float(raw.get("latency_s", 0.0)))
        probs = {str(k): float(v) for k, v in raw.items()}
        total = sum(probs.values())
        abstain = max(0.0, 1.0 - total)
        top = max(probs, key=lambda k: probs[k]) if probs else None
        n = len(probs) + 1
        top_p = probs.get(top or "", 0.0)
        conf = (n * top_p - 1.0) / (n - 1) if n > 1 else 1.0
        return Answer(key=top, probabilities=probs, confidence=max(0.0, min(1.0, conf)),
                      abstain=abstain)
    raise TypeError(f"ScriptedDecider 不认识的答案形式: {raw!r}")
