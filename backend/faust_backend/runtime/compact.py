"""CompactMiddleware — 会话压缩（内置，始终启用）。

设计依据见 `__dev__/compact-refactor-plan.md`。要点：

1. **继承 langchain 官方 `SummarizationMiddleware`**，复用它已被测试过的
   关键逻辑：`_should_summarize`（触发判定）、`_determine_cutoff_index` /
   `_find_safe_cutoff_point`（**不拆散 AIMessage(tool_calls) + ToolMessage 组**）、
   `_ensure_message_ids`、`with_retry` 重试。
   仅覆盖「怎么调摘要」与「怎么写回」这两件本项目有特殊要求的事。

2. **token 一律来自 LLM API**：基线取最后一条 AIMessage 的
   `usage_metadata.total_tokens`（上游真实返回值）。拿不到 API 数据时**不猜**，
   直接跳过本轮压缩。绝不使用 tiktoken 之类的本地推算作为判定依据。

3. **压缩调用复用主上下文前缀以命中缓存**：payload = 当前完整 messages +
   一条尾端追加的指令 HumanMessage。System 与工具 schema 保持逐字节不变，
   工具实现换成 noop mock（schema 相同 → 前缀相同 → 命中前缀缓存），
   并在 prompt 中明令「不要调用工具」。

4. **写回**：用 `RemoveMessage(id=REMOVE_ALL_MESSAGES)` 清空后按目标顺序重排，
   因此可以在「System 之后、保留消息之前」精确落位。主 Agent 的 System 在
   state 里，摘要并入 System；Subagent 的 system prompt 由 create_agent 在调用时
   注入（不在 state 里），此时退化为把摘要作为首条 HumanMessage。

5. **失败不隐瞒也不打断**：重试后仍失败则不改动 checkpoint，本轮继续，
   错误写入日志；随后上游若真的报 context_length_exceeded，照常暴露给用户。
"""

from __future__ import annotations

from typing import Any, Sequence

from langchain.agents.middleware.summarization import SummarizationMiddleware
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from typing_extensions import override

from faust_backend.logger import get_logger

log = get_logger("faust.compact")

# 摘要调用的事件标记：lifecycle 的流式生产者据此把它与主模型输出分流，
# 转成 compact_start / compact_delta / compact_done，避免摘要文本泄进正文气泡。
COMPACT_TAG = "faust_compact_summarizer"

# System 中摘要段的起止标记。下次压缩时据此剥离旧摘要再写入新摘要，
# 保证 System 不会随压缩次数无限增长。
COMPACT_MARKER_START = "<!-- faust:compact-summary:start -->"
COMPACT_MARKER_END = "<!-- faust:compact-summary:end -->"

COMPACT_INSTRUCTION = """停止你当前正在执行的任务，不要再调用任何工具，也不要继续之前的步骤。

请把以上全部对话压缩成一份结构化中文摘要，作为后续继续工作时的上下文。严格使用下面 6 个二级标题，不要增删标题、不要写寒暄、不要输出与摘要无关的内容：

## 目标与进度
用户的核心目标，以及目前推进到哪一步。

## 未完成事项
尚未完成、待验证或待用户确认的事项，逐条列出。

## 关键约束与用户偏好
必须遵守的约束、用户的明确偏好与否决过的方案。

## 结论与失败尝试
已经确认的结论，以及尝试过但失败的做法及其原因（避免重复踩坑）。

## 环境与状态
仍然有效的文件路径、命令、配置项、服务/插件/Skill/MCP 状态。

## 下一步
接下来应当执行的动作。

要求：
- 保留仍然有效的文件路径、命令、配置名，不要改写或省略它们。
- 工具调用折叠为「做了什么、结果如何、有什么影响」，不要罗列原始输出。
- 图片等多模态内容只转述与任务相关的信息。
- 直接输出摘要正文，不要有任何前言或结束语。"""


def _mock_tool(tool: BaseTool) -> BaseTool:
    """构造与真实工具 schema 逐字节一致、但实现为空操作的 mock 工具。

    工具定义位于请求最前端，只要 schema 有一个字节不同，前缀缓存就失效；
    因此这里必须原样保留 name / description / args_schema。
    """
    args_schema = tool.args_schema

    def _noop(**kwargs: Any) -> str:  # noqa: ARG001
        return ""

    if args_schema is not None:
        return StructuredTool.from_function(
            func=_noop,
            name=tool.name,
            description=tool.description,
            args_schema=args_schema,
        )
    return StructuredTool.from_function(
        func=_noop,
        name=tool.name,
        description=tool.description,
    )


def build_mock_tools(tools: Sequence[BaseTool] | None) -> list[BaseTool]:
    """把工具列表整体替换为 schema 相同的 noop mock 工具。"""
    return [_mock_tool(tool) for tool in (tools or [])]


def _strip_summary(content: Any) -> str:
    """剥离 System 内容里上一次写入的摘要段，只留下原始 prompt。"""
    text = content if isinstance(content, str) else str(content or "")
    start = text.find(COMPACT_MARKER_START)
    if start < 0:
        return text.rstrip()
    end = text.find(COMPACT_MARKER_END, start)
    if end < 0:
        return text[:start].rstrip()
    tail = text[end + len(COMPACT_MARKER_END):]
    return (text[:start] + tail).rstrip()


def _wrap_summary(prompt_text: str, summary: str) -> str:
    """把摘要段包进标记后拼到 System prompt 末尾。"""
    return (
        f"{prompt_text.rstrip()}\n\n"
        f"{COMPACT_MARKER_START}\n"
        f"以下是此前对话的压缩摘要（背景资料，不是新的用户请求）：\n\n"
        f"{summary.strip()}\n"
        f"{COMPACT_MARKER_END}"
    )


def _reported_tokens(messages: Sequence[AnyMessage]) -> int:
    """取最近一条 AIMessage 的 API 上报 token 数（无 API 数据时返回 0）。"""
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            usage = message.usage_metadata
            if usage:
                total = usage.get("total_tokens")
                if isinstance(total, int) and total > 0:
                    return total
            return 0
    return 0


class CompactMiddleware(SummarizationMiddleware):
    """按 API 上报的 token 用量自动压缩会话上下文。"""

    def __init__(
        self,
        *,
        model: Any,
        compact_model: Any,
        tools: Sequence[BaseTool] | None = None,
        context_length: int,
        threshold_ratio: float = 0.8,
        keep_rounds: int = 3,
    ) -> None:
        """
        Args:
            model: 主模型实例（供父类计算 token 与重试）。
            compact_model: 压缩调用使用的模型（已关闭 thinking）。
            tools: 与主请求一致的工具列表，会被替换成同 schema 的 noop mock。
            context_length: 当前模型的上下文长度（token）。
            threshold_ratio: 触发阈值比例，默认 0.8（留 20% 给指令与摘要输出）。
            keep_rounds: 压缩后保留最近多少轮对话。
        """
        threshold = max(1, int(context_length * float(threshold_ratio)))
        super().__init__(
            model=model,
            # 用绝对 token 阈值而非 fraction：fraction 依赖 model.profile，
            # 而本项目按 provider 配置持有上下文长度，不依赖 profile。
            trigger=("tokens", threshold),
            # keep 仅占位，实际保留策略由 _determine_cutoff_index 按「轮次」决定。
            keep=("messages", max(1, int(keep_rounds))),
            # 压缩调用要复用完整前缀以命中缓存，因此不做任何裁剪。
            trim_tokens_to_summarize=None,
        )
        self.compact_model = compact_model
        self.mock_tools = build_mock_tools(tools)
        self.context_length = int(context_length)
        self.threshold_ratio = float(threshold_ratio)
        self.threshold_tokens = threshold
        self.keep_rounds = max(1, int(keep_rounds))

    # ── 触发与边界 ──

    @override
    def _determine_cutoff_index(self, messages: list[AnyMessage]) -> int:
        """按「轮次」确定切点：保留最近 N 轮，绝不拆散 tool_call 组。

        轮次 = 一条 HumanMessage 及其之后的全部消息（直到下一条 HumanMessage）。
        边界先按轮次取，再交给父类的 `_find_safe_cutoff_point` 做 tool 组保护。
        """
        round_starts = [
            index
            for index, message in enumerate(messages)
            if isinstance(message, HumanMessage)
        ]
        if not round_starts:
            return 0

        # 保留最近 keep_rounds 轮；轮数不足时退化为只保留最后一轮，
        # 保证仍然能压缩出空间（而不是因为轮数不够就完全不压）。
        if len(round_starts) > self.keep_rounds:
            cutoff = round_starts[-self.keep_rounds]
        else:
            cutoff = round_starts[-1]

        # 不能把 System 之前/之中切开：至少留出全部 System 消息。
        system_count = sum(1 for m in messages if isinstance(m, SystemMessage))
        if cutoff <= system_count:
            return 0
        return self._find_safe_cutoff_point(messages, cutoff)

    # ── 压缩调用（复用主前缀以命中缓存） ──

    async def _acompact(self, messages: list[AnyMessage], on_delta=None) -> str:
        """用当前完整上下文 + 尾端指令生成摘要。

        关键：payload 前缀与主请求逐字节一致（同样的 System、同样的工具 schema），
        这样这次调用能命中上游的前缀缓存。

        Args:
            on_delta: 可选回调，收到增量文本时调用。自动压缩路径不传（由
                lifecycle 的流式生产者按 tag 分流）；手动 /compact 路径传入，
                因为它不在 graph 运行中，拿不到 astream_events。
        """
        payload: list[AnyMessage] = [
            *messages,
            HumanMessage(content=COMPACT_INSTRUCTION),
        ]
        runnable = (
            self.compact_model.bind_tools(self.mock_tools)
            if self.mock_tools
            else self.compact_model
        )
        # 瞬时故障在进程内重试（q27：先重试，再降级）
        runnable = runnable.with_retry()
        chunks: list[str] = []
        async for chunk in runnable.astream(
            payload,
            config={
                "tags": [COMPACT_TAG],
                "metadata": {"faust_event": "compact"},
            },
        ):
            text = _content_to_text(getattr(chunk, "content", ""))
            if text:
                chunks.append(text)
                if on_delta is not None:
                    result = on_delta(text)
                    if hasattr(result, "__await__"):
                        await result
        return "".join(chunks).strip()

    # ── 写回 ──

    def _build_replacement(
        self, messages: list[AnyMessage], summary: str
    ) -> list[AnyMessage]:
        """构造压缩后的消息列表：System（并入摘要）+ 保留消息。

        使用 `RemoveMessage(REMOVE_ALL_MESSAGES)` 清空后按目标顺序重排，
        因此摘要可以精确落在「System 之后、保留消息之前」。
        """
        system_messages = [m for m in messages if isinstance(m, SystemMessage)]
        if system_messages:
            head = system_messages[0]
            merged = head.model_copy(
                update={"content": _wrap_summary(_strip_summary(head.content), summary)}
            )
            return [merged, *system_messages[1:]]
        # Subagent：system prompt 由 create_agent 在模型调用时注入，不在 state 中。
        # 此时没有可并入的 System，退化为把摘要作为首条 HumanMessage。
        return [
            HumanMessage(
                content=(
                    "以下是此前对话的压缩摘要（背景资料，不是新的用户请求）：\n\n"
                    f"{summary}"
                ),
                additional_kwargs={"lc_source": "faust_compact"},
            )
        ]

    @override
    async def abefore_model(
        self, state: Any, runtime: Any
    ) -> dict[str, Any] | None:
        messages = state["messages"]
        self._ensure_message_ids(messages)

        # 判定一律基于 API 上报值；没有 API 数据时不猜、不压缩。
        reported = _reported_tokens(messages)
        if reported <= 0:
            return None
        if not self._should_summarize(messages, reported):
            return None

        cutoff_index = self._determine_cutoff_index(messages)
        if cutoff_index <= 0:
            return None

        _to_summarize, preserved = self._partition_messages(messages, cutoff_index)
        try:
            summary = await self._acompact(messages)
        except Exception as exc:  # noqa: BLE001 - 失败降级：不动 checkpoint，本轮继续
            log.error(
                "会话压缩失败（已重试），本次不改动 checkpoint，本轮继续: %s", exc
            )
            return None
        if not summary:
            log.error("会话压缩结果为空，本次不改动 checkpoint")
            return None

        log.info(
            "会话压缩完成: 上报 tokens=%d 阈值=%d 压缩 %d 条、保留 %d 条",
            reported,
            self.threshold_tokens,
            cutoff_index,
            len(preserved),
        )
        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *self._build_replacement(messages, summary),
                *preserved,
            ]
        }

    # ── 手动触发（/compact） ──

    async def force_compact(
        self, messages: list[AnyMessage], on_delta=None
    ) -> dict[str, Any] | None:
        """无视阈值强制压缩一次，返回可交给 aupdate_state 的 state update。

        供 `/compact` 斜杠命令使用：它不在 graph 运行中，因此需要显式把
        更新写回 checkpoint，并自行推送 compact_* 事件。
        """
        if not messages:
            return None
        self._ensure_message_ids(messages)
        cutoff_index = self._determine_cutoff_index(messages)
        if cutoff_index <= 0:
            log.info("会话压缩跳过：没有可压缩的历史轮次")
            return None
        _to_summarize, preserved = self._partition_messages(messages, cutoff_index)
        try:
            summary = await self._acompact(messages, on_delta=on_delta)
        except Exception as exc:  # noqa: BLE001
            log.error("手动会话压缩失败: %s", exc)
            raise
        if not summary:
            raise RuntimeError("对话压缩结果为空")
        log.info(
            "手动会话压缩完成: 压缩 %d 条、保留 %d 条", cutoff_index, len(preserved)
        )
        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *self._build_replacement(messages, summary),
                *preserved,
            ]
        }


def _content_to_text(content: Any) -> str:
    """把模型分片 content 归一化成纯文本（与 runtime.state 保持一致）。"""
    from faust_backend.runtime import state as runtime_state

    return runtime_state.message_content_to_text(content)


async def build_compact_middleware(
    *,
    model: Any,
    tools: Sequence[BaseTool] | None,
    model_spec: str | None,
) -> CompactMiddleware | None:
    """按模型 spec 构建 CompactMiddleware；无法构建时返回 None 并记录原因。

    压缩调用使用**关闭 thinking** 的模型（q21）：reasoning 参数不属于被缓存的
    前缀（前缀 = System + tools schema + messages），因此关掉思考不破缓存，
    同时避免思考 token 吃掉留给摘要输出的余量。

    构建失败（如 provider 配置缺失）时返回 None，而不是抛错——auto compact
    是保命机制，不应因为自身构建失败就阻断整个 Agent 启动。
    """
    import faust_backend.config_loader as conf
    from faust_backend.provider import (
        build_ReasoningChatOpenAI_from_spec,
        get_context_length,
    )
    from faust_backend.runtime import state as runtime_state

    if not model_spec:
        log.warning("未配置模型 spec，auto compact 未启用")
        return None
    providers = runtime_state.get_model_providers()
    context_length = get_context_length(providers, model_spec)
    try:
        compact_model = await build_ReasoningChatOpenAI_from_spec(
            providers, spec=model_spec, intensity=None
        )
    except Exception as exc:  # noqa: BLE001
        log.error("构建压缩模型失败，auto compact 未启用: %s", exc)
        return None
    return CompactMiddleware(
        model=model,
        compact_model=compact_model,
        tools=tools,
        context_length=context_length,
        threshold_ratio=conf.COMPACT_THRESHOLD_RATIO,
        keep_rounds=conf.COMPACT_KEEP_ROUNDS,
    )
