"""OmniJevDecider：把问题打包成**一次** Processor invoke（设计 §5/§9）。

关键约束（附录 B 实测）：状态问与动作问必须在同一次 invoke 里，因为同一帧的第二次调用不会
复用 backbone 状态（1.21s + 1.14s vs 1.32s）。因此 ``_ask_batch`` 覆写成单次 invoke；
``_ask``（单问）退化为一次 invoke，仅供测试/单问场景使用。

选项文本：``{"key": 内部键, "text": 呈现文本}``。点击动作的呈现文本是模块写的 ``meaning``，
box 只作我方载荷（设计 §13：``region [x1,y1,x2,y2]`` 对小模型没有可读性）。
"""
from __future__ import annotations

from typing import Any

from .decider import Answer, AskItem, Frame, LimitedUIDecider, UIOption, UIDrvError


class OmniJevDecider(LimitedUIDecider):
    """决策来源 = OMNIJEV Processor（受管子进程里的 4B-4bit 视觉决策模型）。"""

    def __init__(self, module: str, spec: Any, hooks: dict[str, Any] | None, deps: Any) -> None:
        super().__init__(module, spec, hooks, deps)
        if deps.processor_ask is None:
            raise UIDrvError("OmniJevDecider 需要 deps.processor_ask（OmniJev 调用器）")

    async def _ask(self, question: str, options: list[UIOption], *, frame: Frame) -> Answer:
        item = AskItem(qid="q", question=question, options=tuple(options))
        return (await self._ask_batch([item], frame=frame))["q"]

    async def _ask_batch(self, items: list[AskItem], *, frame: Frame) -> dict[str, Answer]:
        """一次 invoke 打包所有问（状态 + 动作）。"""
        if frame.image is None:
            raise UIDrvError("帧没有图像内容，无法提问")
        questions: dict[str, Any] = {}
        for item in items:
            if not item.options:
                raise UIDrvError(f"问题 {item.qid} 没有任何可选项")
            questions[item.qid] = {
                "type": item.qtype,
                "instructions": item.question,
                "options": [{"key": o.key, "text": o.label} for o in item.options],
            }
        payload = {"frames": [frame.image], "questions": questions}
        ask = self.deps.processor_ask
        assert ask is not None
        result = await ask(payload, dict(self.spec.processor))
        if not isinstance(result, dict):
            raise UIDrvError(f"OMNIJEV 返回了非 dict 结果: {type(result).__name__}")
        answers = result.get("answers")
        if not isinstance(answers, dict):
            raise UIDrvError(f"OMNIJEV 结果缺少 answers 字段: {list(result)[:6]}")
        total = result.get("latency_s")
        out: dict[str, Answer] = {}
        for item in items:
            out[item.qid] = _answer_from(answers.get(item.qid), total)
        return out


def _answer_from(raw: Any, total_latency: Any) -> Answer:
    """OmniJev choice 答案 → Answer（形状见 vendor/mso/infer.py:_finish）。"""
    if not isinstance(raw, dict):
        return Answer(key=None, valid=False, abstain=1.0)
    probs = raw.get("probabilities")
    probs = {str(k): float(v) for k, v in probs.items()} if isinstance(probs, dict) else {}
    key = raw.get("choice")
    if key is None and raw.get("score") is not None:
        key = raw.get("score")
    return Answer(
        key=None if key is None else str(key),
        probabilities=probs,
        confidence=float(raw.get("confidence") or 0.0),
        abstain=float(raw.get("abstain") or 0.0),
        noul=(float(raw["noul"]) if raw.get("noul") is not None else None),
        valid=bool(raw.get("valid", True)),
        latency_s=float(raw.get("latency_s") or 0.0),
        latency_total_s=(float(raw["latency_total_s"]) if raw.get("latency_total_s") is not None
                         else (float(total_latency) if isinstance(total_latency, (int, float)) else None)),
    )
