"""Memory Injector：在用户消息送达 LLM 前，按检索结果注入记忆文档元数据。

两种模式（插件配置 MODE）:
- lite: jieba 分词(进程池) -> 过滤低信息 token -> 纯本地 BM25
- full: 同样过滤后, 混合检索 search_compact(向量+BM25+图谱+rerank)

低信息熵输入(如"你好")过滤后无有效 token, 直接跳过检索;
"谢谢，请问LSTM是什么"过滤掉"谢谢/请问", 只拿 "LSTM" 等有效词去搜。
n 轮内已注入过的文档 path 不再注入(滚动窗口, 会话内存态)。
"""
from typing import Any, List

from faust_backend.logger import get_logger
from faust_backend.memory import get_memory
from faust_backend.memory.stopwords import filter_info_tokens
from faust_backend.memory.tokenize_pool import jieba_tokenize
from faust_backend.plugin_system import FaustPlugin, PluginContext, hookimpl

log = get_logger("faust.plugins.memory-injector")


class Plugin(FaustPlugin):
    def __init__(self) -> None:
        self.ctx: PluginContext | None = None

    @hookimpl
    async def startup(self, ctx: PluginContext) -> None:
        self.ctx = ctx
        if ctx.storage is not None:
            ctx.storage.register_defaults({"recent_turns": []})
        await ctx.register_config([
            {"key": "MODE", "type": "str", "label": "检索模式(可选 lite/full)", "default": "full"},
            {"key": "DEDUP_TURNS", "type": "int", "label": "记忆去重轮数N", "default": 5},
            {"key": "TOP_K", "type": "int", "label": "单次注入条数上限", "default": 3},
            {"key": "MIN_INFO_TOKENS", "type": "int", "label": "低于该有效token数则跳过检索", "default": 1},
        ])

    def _remember(self, paths: set[str], dedup_turns: int) -> None:
        """把本轮注入的 path 写入 SESSION 存储（clear/compact 时自动重置）。"""
        if self.ctx is None or self.ctx.storage is None:
            return
        turns = list(self.ctx.storage.get("session", "recent_turns") or [])
        turns.append(sorted(paths))
        while len(turns) > max(dedup_turns, 1):
            turns.pop(0)
        self.ctx.storage.set("session", "recent_turns", turns)

    def _blocked_paths(self) -> set[str]:
        """最近 N 轮已注入过的 path 集合（本轮之前）。"""
        if self.ctx is None or self.ctx.storage is None:
            return set()
        blocked: set[str] = set()
        for turn in (self.ctx.storage.get("session", "recent_turns") or []):
            blocked |= set(turn)
        return blocked

    @hookimpl
    async def message_received(self, msg: Any, history: List[Any], ctx: PluginContext) -> str | None:
        if self.ctx is None:
            self.ctx = ctx
        text = str(msg or "").strip()
        if not text:
            return None

        cfg = self.ctx
        mode = str(await cfg.get_config("MODE", "full") or "full").strip().lower()
        top_k = min(max(int(await cfg.get_config("TOP_K", 3) or 3), 1), 3)  # spec: top_k<=3
        dedup_turns = max(int(await cfg.get_config("DEDUP_TURNS", 5) or 5), 1)
        min_info = max(int(await cfg.get_config("MIN_INFO_TOKENS", 1) or 1), 1)

        tokens = await jieba_tokenize(text)
        info = filter_info_tokens(tokens)
        if len(info) < min_info:
            return None  # 低信息熵输入: 不检索、不注入

        memory = get_memory()
        if mode == "lite":
            items = await memory.search_bm25(info, top_k=top_k)
        else:
            items = await memory.search_compact(" ".join(info), top_k=top_k)
        items = [it for it in (items or []) if it.get("path")][:top_k]

        blocked = self._blocked_paths()
        items = [it for it in items if it["path"] not in blocked]
        self._remember({it["path"] for it in items}, dedup_turns)
        if not items:
            return None

        lines = []
        for it in items:
            desc = str(it.get("description") or "").strip().replace("\n", " ")[:120]
            lines.append(f"- {it['path']} — {desc} (约{int(it.get('line_count') or 0)}行)")
        log.info("注入 %d 条记忆 (mode=%s): %s", len(items), mode, [it["path"] for it in items])
        return text + "\n\n[Memory]\n" + "\n".join(lines)
