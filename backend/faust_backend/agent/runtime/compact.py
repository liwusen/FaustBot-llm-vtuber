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

6. **压缩请求自己修复 tool_calls**：压缩调用直接打 provider、不经过中间件链，
   因此 payload 构造前先过一遍 `repair_dangling_tool_calls`。checkpoint 里
   允许存在悬空的 `AIMessage(tool_calls)`（工具执行被中断、压缩前遗留），
   主模型调用由 `ToolCallRepairMiddleware` 兜住，压缩调用若不自修复就会
   收到 400 invalid_request_error，导致压缩永远失败。
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

from faust_backend.core.message import (
    message_content_to_text,
)
from faust_backend.llm.formats import forced_no_tool_choice
from faust_backend.core.log import get_logger
from faust_backend.agent.runtime.turn import usage as usage_mod
from faust_backend.agent.runtime.middleware.tool_call_repair import repair_dangling_tool_calls

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


def _reported_tokens(messages: Sequence[AnyMessage], api_format: str = "openai_chat") -> int:
    """取最近一条**带 API 用量**的 AIMessage 的真实上下文占用（无数据时返回 0）。

    不能"遇到第一条 AIMessage 就定论"：该条可能没有 `usage_metadata`
    （本地构造的消息、插件注入的消息等）。那样 `_should_summarize` 会永远判定为
    "未超阈值"，自动压缩静默失效，会话一路涨到必须手动 /compact —— 而手动压缩
    要对整段历史跑一次完整模型调用，正是最贵的那条路径。

    口径来自 `runtime.usage`：Anthropic 的缓存读写必须计入输入（否则 prompt 缓存
    命中率高的长会话永远不触发压缩），OpenAI/Gemini 的 cached 是输入子集
    （重复相加会虚高）。依然不本地估算：找不到真实上报值就返回 0。
    """
    return usage_mod.context_tokens(messages, api_format)


def _is_compact_summary(message: Any) -> bool:
    """判断消息是否为压缩写回的摘要（它是固定头部，不算一轮对话）。

    没有 SystemMessage 时（Subagent、以及本项目主 Agent 的 state 里也没有
    System），`_build_replacement` 会把摘要写成首条 HumanMessage。若把它当成
    一轮，`_determine_cutoff_index` 就永远算得出"可压缩的轮次"，
    导致压缩后每回合都再压一次（每次都是一整轮模型调用）。
    """
    kwargs = getattr(message, "additional_kwargs", None) or {}
    return str(kwargs.get("lc_source") or "") == "faust_compact"


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
        api_format: str = "openai_chat",
        server_tool_specs: Sequence[dict[str, Any]] | None = None,
        summary_max_tokens: int = 0,
    ) -> None:
        """
        Args:
            model: 主模型实例（供父类计算 token 与重试）。
            compact_model: 压缩调用使用的模型（已关闭 thinking）。
            tools: 与主请求一致的工具列表，会被替换成同 schema 的 noop mock。
            context_length: 当前模型的上下文长度（token）。
            threshold_ratio: 触发阈值比例，默认 0.8（留 20% 给指令与摘要输出）。
            keep_rounds: 压缩后保留最近多少轮对话。
            api_format: 主模型的 API 格式（决定用量口径与工具参数写法）。
            server_tool_specs: 主请求里启用的服务端工具规格；压缩请求必须带上
                同样的 specs，否则 tools 前缀与主请求不一致，前缀缓存整段失效。
            summary_max_tokens: 压缩模型自身的输出上限（Anthropic 构造时已设定）。
                用于判定"摘要是否被这个上限截断"。
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
        self.api_format = str(api_format or "openai_chat")
        self.server_tool_specs = [dict(spec) for spec in (server_tool_specs or [])]
        self.summary_max_tokens = int(summary_max_tokens or 0)
        # 输出上限曾导致摘要被截断时置位：本次进程内不再施加成本上限。
        # 目的是自愈——否则每回合都会用过小的上限压缩失败、白烧一整轮调用。
        self._cost_cap_bypassed = False
        self._tool_choice_warned = False
        self._warned_no_usage = False

    # ── 触发与边界 ──

    @override
    def _determine_cutoff_index(self, messages: list[AnyMessage]) -> int:
        """按「轮次」确定切点：保留最近 N 轮，绝不拆散 tool_call 组。

        轮次 = 一条 HumanMessage 及其之后的全部消息（直到下一条 HumanMessage）。
        压缩写回的摘要消息不算轮次——否则"摘要 + 保留 N 轮"永远比 keep_rounds
        多一轮，切点恒 > 0，压缩后每回合都会再压一次。
        边界先按轮次取，再交给父类的 `_find_safe_cutoff_point` 做 tool 组保护。
        """
        round_starts = [
            index
            for index, message in enumerate(messages)
            if isinstance(message, HumanMessage) and not _is_compact_summary(message)
        ]
        if not round_starts:
            return 0

        # 保留最近 keep_rounds 轮；轮数不足时退化为只保留最后一轮，
        # 保证仍然能压缩出空间（而不是因为轮数不够就完全不压）。
        if len(round_starts) > self.keep_rounds:
            cutoff = round_starts[-self.keep_rounds]
        else:
            cutoff = round_starts[-1]

        # 丢弃段里至少要有一条**真实轮次**，否则只是把已有摘要重新摘要一遍：
        # 白花一整轮压缩调用，而且状态不会真正变小。
        if not any(
            isinstance(m, HumanMessage) and not _is_compact_summary(m)
            for m in messages[:cutoff]
        ):
            return 0

        # 不能把 System 之前/之中切开：至少留出全部 System 消息。
        system_count = sum(1 for m in messages if isinstance(m, SystemMessage))
        if cutoff <= system_count:
            return 0
        return self._find_safe_cutoff_point(messages, cutoff)

    # ── 压缩调用（复用主前缀以命中缓存） ──

    async def _acompact(self, messages: list[AnyMessage], on_delta=None) -> str:
        """用**当前完整上下文 + 尾端指令**生成摘要。

        关键：payload 前缀必须与主请求逐字节一致（同样的 System、同样的工具
        schema），这样这次调用才能命中上游的前缀缓存。因此调用方传进来的是
        完整 `messages`（= 主请求 messages 本身），**不要**改成只传将要丢弃的
        段落：那样 payload 不再以对话起点开头，前缀缓存会整段失效，被丢弃段
        之后的全部历史都按全价计费。

        只有在用户显式打开 `COMPACT_COST_CONTROL_ENABLED`（默认关闭）时才会
        牺牲这一点换成本：按 `COMPACT_MAX_INPUT_TOKENS` 裁掉最旧消息、按
        `COMPACT_MAX_OUTPUT_TOKENS` 限制摘要输出。

        压缩调用不经过中间件链，因此这里自己修复悬空的 tool_calls；修复放在
        追加指令**之前**，使序列结尾的悬空走「注入占位」而不是被判为打断作废。

        Args:
            on_delta: 可选回调，收到增量文本时调用。自动压缩路径不传（由
                lifecycle 的流式生产者按 tag 分流）；手动 /compact 路径传入，
                因为它不在 graph 运行中，拿不到 astream_events。
        """
        if not messages:
            return ""

        import faust_backend.core.config as conf

        cost_control = self._cost_control_active()
        if cost_control:
            messages = self._apply_input_cap(list(messages))
        payload: list[AnyMessage] = [
            *repair_dangling_tool_calls(list(messages)),
            HumanMessage(content=COMPACT_INSTRUCTION),
        ]
        # 工具前缀必须与主请求一致（客户端工具的 mock + 同样的服务端工具 specs），
        # 否则 tools 定义不同 → 前缀缓存整段失效。`tool_choice` 用于阻止摘要
        # 调用真的去执行服务端搜索。
        tool_specs: list[Any] = [*self.mock_tools, *self.server_tool_specs]
        runnable = self._bind_compact_tools(self.compact_model, tool_specs)
        # 摘要输出上限：结构化摘要不需要很长。仅在成本控制开启时施加。
        # 自定义模型/测试替身可能不支持 bind，失败时退化为不设上限而不是让压缩失败。
        max_out = conf.COMPACT_MAX_OUTPUT_TOKENS if cost_control else 0
        if max_out > 0:
            try:
                runnable = runnable.bind(max_tokens=max_out)
            except Exception as exc:  # noqa: BLE001
                log.debug("压缩模型不支持 bind(max_tokens)，跳过输出上限: %s", exc)
                max_out = 0
        # 实际生效的输出上限：成本上限 > 构造时的上限（Anthropic 必须显式给）
        effective_max = max_out or self.summary_max_tokens
        # 瞬时故障在进程内重试（q27：先重试，再降级）
        runnable = runnable.with_retry()
        chunks: list[str] = []
        usage: Any = None
        finish_reason: Any = None
        async for chunk in runnable.astream(
            payload,
            config={
                "tags": [COMPACT_TAG],
                "metadata": {"faust_event": "compact"},
            },
        ):
            reported = getattr(chunk, "usage_metadata", None)
            if reported:
                usage = reported
            meta = getattr(chunk, "response_metadata", None) or {}
            # OpenAI 用 finish_reason，Anthropic 用 stop_reason（max_tokens）
            if meta.get("finish_reason"):
                finish_reason = meta["finish_reason"]
            elif meta.get("stop_reason"):
                finish_reason = meta["stop_reason"]
            text = _content_to_text(getattr(chunk, "content", ""))
            if text:
                chunks.append(text)
                if on_delta is not None:
                    result = on_delta(text)
                    if hasattr(result, "__await__"):
                        await result
        self._log_usage(usage, self.api_format)
        summary = "".join(chunks).strip()
        if self._is_truncated(effective_max, finish_reason, usage):
            # 摘要被截断 → 写回去就等于永久丢掉摘要没覆盖到的历史（原文会被
            # _build_replacement 删掉）。这里按「压缩失败」处理：不写回 checkpoint，
            # 把原因抛给调用方（自动压缩降级为不压缩；手动 /compact 显示失败卡片）。
            # 同时放弃成本上限自愈，避免下一回合再用过小的上限白烧一次调用。
            self._cost_cap_bypassed = max_out > 0
            raise RuntimeError(
                f"摘要被输出上限 {effective_max} 截断"
                f"（finish_reason={finish_reason}，api_format={self.api_format}），"
                "本次不写回 checkpoint 以免丢失摘要未覆盖的历史"
                + (
                    "；已自动放弃 COMPACT_MAX_OUTPUT_TOKENS，请调大该值或关闭 "
                    "COMPACT_COST_CONTROL_ENABLED"
                    if max_out > 0
                    else "；该上限来自模型自身 max_tokens，请调大模型的"
                    " model_max_output_tokens"
                )
            )
        return summary

    def _bind_compact_tools(self, model: Any, tool_specs: list[Any]) -> Any:
        """把工具绑定到压缩模型，并禁止本次调用真正使用工具。

        `tool_choice` 的写法**随格式不同**，写错会静默变成"必须调用某个工具"：

        - OpenAI Chat / Responses：字符串 `"none"`
        - Anthropic：必须传 dict `{"type": "none"}`；传字符串 `"none"` 会被
          解释成「调用名为 none 的工具」（SDK 的字符串只支持 any/auto/工具名）
        - Gemini：字符串 `"none"`

        绑定失败（测试替身、网关不支持工具）时逐级降级：先去掉 tool_choice，
        再去掉服务端 specs，最后不绑工具——压缩本身不能因此失败。
        """
        if not tool_specs:
            return model
        choice = forced_no_tool_choice(self.api_format)
        try:
            return model.bind_tools(tool_specs, tool_choice=choice)
        except Exception as exc:  # noqa: BLE001
            if not self._tool_choice_warned:
                self._tool_choice_warned = True
                log.warning(
                    "压缩请求无法绑定 tool_choice=%r（%s），改用无 tool_choice 的工具前缀："
                    "摘要阶段可能调用工具，前缀缓存仍保持一致",
                    choice,
                    exc,
                )
        try:
            return model.bind_tools(tool_specs)
        except Exception as exc:  # noqa: BLE001
            if self.server_tool_specs:
                log.warning(
                    "压缩请求无法绑定服务端工具规格（%s），本次省略："
                    "前缀缓存可能不完整",
                    exc,
                )
        if self.mock_tools:
            return model.bind_tools(self.mock_tools)
        return model

    def _cost_control_active(self) -> bool:
        """成本控制（输入/输出上限）当前是否生效。

        由 `COMPACT_COST_CONTROL_ENABLED` 决定，默认关闭；一旦发生过截断，
        本进程内不再施加，保证不会每回合重复付费。
        """
        import faust_backend.core.config as conf

        if self._cost_cap_bypassed:
            return False
        return bool(conf.COMPACT_COST_CONTROL_ENABLED)

    @staticmethod
    def _is_truncated(max_out: int, finish_reason: Any, usage: Any) -> bool:
        """摘要输出是否被上限截断。

        各格式的上限信号不同：OpenAI 用 `finish_reason == "length"`，
        Anthropic 用 `stop_reason == "max_tokens"`。部分 provider 都不返回，
        退化为比较上报的 output_tokens 是否已触顶。三者都拿不到时无法判断，
        按未截断处理（不额外制造假失败）。
        """
        if max_out <= 0:
            return False
        reason = str(finish_reason or "").strip().lower()
        if reason in ("length", "max_tokens"):
            return True
        if isinstance(usage, dict):
            output = usage.get("output_tokens")
            if isinstance(output, int) and output >= max_out:
                return True
        return False

    @staticmethod
    def _log_usage(usage: Any, api_format: str = "openai_chat") -> None:
        """记录压缩调用真实用量，让压缩成本可审计。

        `cache_read` 是判断"前缀缓存有没有命中"的唯一依据：它若远小于
        `input_tokens`，说明前缀没对齐，整段历史都在按全价计费。
        Anthropic 另外会上报 `cache_creation`（本回合写入缓存的量），
        把它单独打印出来，才能区分"写缓存"和"读缓存"。
        """
        if not usage:
            log.info("压缩调用用量: 提供商未返回 usage_metadata（无法核算成本）")
            return
        details = usage.get("input_token_details") or {}
        output_details = usage.get("output_token_details") or {}
        log.info(
            "压缩调用用量(%s): input=%s output=%s cache_read=%s cache_creation=%s "
            "reasoning=%s total=%s",
            api_format,
            usage.get("input_tokens"),
            usage.get("output_tokens"),
            details.get("cache_read"),
            details.get("cache_creation"),
            output_details.get("reasoning"),
            usage.get("total_tokens"),
        )

    @staticmethod
    def _apply_input_cap(messages: list[AnyMessage]) -> list[AnyMessage]:
        """粗粒度输入上限（仅在成本控制开启且 `COMPACT_MAX_INPUT_TOKENS` 非 0 时生效）。

        超限时从**最旧**的消息开始丢弃。这只是成本安全阀，不是触发判据——触发
        一律用 API 上报值。两个必须知道的代价：

        1. **会打破前缀缓存**：payload 不再以对话起点开头，于是只有 System 与
           工具定义还能命中缓存，被丢弃段之后的全部历史都按全价计费。它减少的是
           token 总量，不代表单价更低。
        2. **会缩小摘要覆盖范围**：被丢掉的最旧消息不会进入摘要（摘要看不到它们），
           但那段历史照样会被 _build_replacement 删除 → 永久丢失。cap 过小时还会
           丢进本轮本来要保留的尾部轮次。

        所以默认 0（不限制），且必须配合 COMPACT_COST_CONTROL_ENABLED 一起打开。
        """
        import faust_backend.core.config as conf

        cap = conf.COMPACT_MAX_INPUT_TOKENS
        if cap <= 0:
            return messages

        # 粗估：中文环境约 2.5 字符/token（与 COMPACT_MAX_OUTPUT_TOKENS 同一口径）
        budget_chars = int(cap * 2.5)
        total = 0
        kept: list[AnyMessage] = []
        for message in reversed(messages):
            content = message.content if isinstance(message.content, str) else str(message.content)
            size = len(content or "")
            if total + size > budget_chars and kept:
                break
            total += size
            kept.append(message)
        kept.reverse()
        if len(kept) != len(messages):
            log.warning(
                "压缩输入超过 COMPACT_MAX_INPUT_TOKENS=%d（约 %d 字符），"
                "已丢弃最旧的 %d 条消息：摘要覆盖范围变小（这部分历史将永久丢失），"
                "且 payload 不再与主请求前缀一致（前缀缓存只剩 System 与工具定义可用）",
                cap,
                budget_chars,
                len(messages) - len(kept),
            )
        return kept

    # ── 写回 ──

    def _build_replacement(
        self, messages: list[AnyMessage], summary: str
    ) -> list[AnyMessage]:
        """构造压缩后的消息列表：唯一 System（并入摘要）+ 保留消息。

        使用 `RemoveMessage(REMOVE_ALL_MESSAGES)` 清空后按目标顺序重排，
        因此摘要可以精确落在「System 之后、保留消息之前」。

        **多条 SystemMessage 必须合并成一条**：Gemini 只认第一条 system
        instruction，其余会被丢弃；Anthropic 也要求 system 是单独参数。
        合并时保留原有顺序与内容（各自剥掉上一轮的摘要段），摘要追加在末尾。
        """
        system_messages = [m for m in messages if isinstance(m, SystemMessage)]
        if system_messages:
            head = system_messages[0]
            if len(system_messages) == 1:
                merged_text = _strip_summary(head.content)
            else:
                merged_text = "\n\n".join(
                    text
                    for text in (
                        _strip_summary(message.content) for message in system_messages
                    )
                    if text
                )
            merged = head.model_copy(
                update={"content": _wrap_summary(merged_text, summary)}
            )
            return [merged]
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
        reported = _reported_tokens(messages, self.api_format)
        if reported <= 0:
            if not self._warned_no_usage and any(
                isinstance(message, AIMessage) for message in messages
            ):
                self._warned_no_usage = True
                log.warning(
                    "模型未上报 token 用量（api_format=%s）：自动压缩不会触发。"
                    "若该网关需要 stream_options.include_usage，请检查 Provider 的"
                    " stream_usage 设置；否则请手动 /compact",
                    self.api_format,
                )
            return None
        if not self._should_summarize(messages, reported):
            return None

        cutoff_index = self._determine_cutoff_index(messages)
        if cutoff_index <= 0:
            return None

        _to_summarize, preserved = self._partition_messages(messages, cutoff_index)
        try:
            # 传完整 messages：payload 前缀与主请求逐字节一致（含同样的 tool_call
            # 自修复），这是命中上游前缀缓存的前提，不要改成只传被丢弃的段落。
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
            # 与自动压缩一致：传完整 messages 以复用主请求前缀（缓存前提）
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
    from faust_backend.agent.runtime import state as runtime_state

    return message_content_to_text(content)


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

    `purpose="compact"` 让 builder 给需要显式输出上限的格式（Anthropic）设置
    合适的摘要上限，而不是沿用 SDK 默认的 1024（会把摘要截断成半句话）。

    构建失败（如 provider 配置缺失）时返回 None，而不是抛错——auto compact
    是保命机制，不应因为自身构建失败就阻断整个 Agent 启动。
    """
    import faust_backend.core.config as conf
    from faust_backend.llm.formats import build_chat_model_from_spec
    from faust_backend.llm.provider import get_context_length, get_model_api_format
    from faust_backend.agent.runtime import state as runtime_state
    from faust_backend.agent.runtime.middleware.server_tools import resolve_server_tool_specs

    if not model_spec:
        log.warning("未配置模型 spec，auto compact 未启用")
        return None
    providers = runtime_state.get_model_providers()
    context_length = get_context_length(providers, model_spec)
    try:
        compact_model = await build_chat_model_from_spec(
            providers, spec=model_spec, intensity=None, purpose="compact"
        )
    except Exception as exc:  # noqa: BLE001
        log.error("构建压缩模型失败，auto compact 未启用: %s", exc)
        return None
    api_format = get_model_api_format(providers, model_spec)
    try:
        server_tool_specs = resolve_server_tool_specs(providers, model_spec)
    except ValueError as exc:
        # 服务端工具配置非法不应该让压缩整体失效：省略 specs，缓存前缀受损但能压
        log.error("压缩请求无法解析服务端工具规格（本次省略）: %s", exc)
        server_tool_specs = []
    # Anthropic 的 max_tokens 在构造时就已确定；读回来用于「摘要是否被截断」判定
    summary_max_tokens = (
        int(getattr(compact_model, "max_tokens", 0) or 0)
        if api_format == "anthropic_messages"
        else 0
    )
    return CompactMiddleware(
        model=model,
        compact_model=compact_model,
        tools=tools,
        context_length=context_length,
        threshold_ratio=conf.COMPACT_THRESHOLD_RATIO,
        keep_rounds=conf.COMPACT_KEEP_ROUNDS,
        api_format=api_format,
        server_tool_specs=server_tool_specs,
        summary_max_tokens=summary_max_tokens,
    )
