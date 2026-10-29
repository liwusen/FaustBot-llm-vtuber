"""Memory Injector：在用户消息送达 LLM 前，按检索结果注入记忆路径 + 该文档中最相关的一段。

两种模式（插件配置 MODE）:
- lite: jieba 分词(进程池) -> 过滤低信息 token -> 纯本地 BM25
- full: 同样过滤后, 混合检索 search_compact(向量+BM25+图谱+rerank)

注入内容 = 记忆路径 + 行数 + 「最相关的一段」正文。段落由 select_passage 就地选出：
检索层只回答「哪个文件相关」，本插件回答「文件里的哪一段」。打分是纯本地字符串运算
（子串命中 + 段间稀有度加权），不新增任何模型/接口调用。

预算：PASSAGE_CHARS 字/条、MAX_TOTAL_CHARS 字/总；预算耗尽后只保留路径与行数。
图片或读不出正文时回退为文档 description（沿用旧格式）。

低信息熵输入(如"你好")过滤后无有效 token, 直接跳过检索;
"谢谢，请问LSTM是什么"过滤掉"谢谢/请问", 只拿 "LSTM" 等有效词去搜。
n 轮内已注入过的文档 path 不再注入(滚动窗口, 会话内存态)。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, List

from faust_backend.core.log import get_logger
from faust_backend.memory import get_memory
from faust_backend.memory.stopwords import filter_info_tokens
from faust_backend.memory.tokenize_pool import jieba_tokenize
from faust_backend.plugin_system import FaustPlugin, PluginContext, hookimpl

log = get_logger("faust.plugins.memory-injector")

DEFAULT_PASSAGE_CHARS = 100
DEFAULT_MAX_TOTAL_CHARS = 350
# 参与打分的 token 数与长度上限，防止畸形 query 拖慢本地扫描
MIN_TOKEN_LEN = 2
MAX_TOKENS = 24
# 超大文档（正常记忆文档远小于此）不再做段落扫描，直接取开头窗口
MAX_SCAN_CHARS = 200_000
# 图片节点没有正文可注入，直接回退 description
IMAGE_SUFFIXES = frozenset({"png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "svg"})


@dataclass(frozen=True)
class Passage:
    """从记忆文档中就地选出的一段正文。"""

    text: str
    start: int
    end: int
    score: float
    truncated_head: bool
    truncated_tail: bool


def _split_units(text: str) -> list[tuple[int, int]]:
    """按空行切段落（保留原文 offset）；无空行的多行文本退化为逐行切。"""
    units: list[tuple[int, int]] = []
    start: int | None = None
    offset = 0
    for line in text.splitlines(keepends=True):
        if line.strip():
            if start is None:
                start = offset
        elif start is not None:
            units.append((start, offset))
            start = None
        offset += len(line)
    if start is not None:
        units.append((start, offset))
    if len(units) <= 1 and text.count("\n") >= 1:
        units = []
        offset = 0
        for line in text.splitlines(keepends=True):
            if line.strip():
                units.append((offset, offset + len(line)))
            offset += len(line)
    return units


def _token_weights(text: str, units: list[tuple[int, int]],
                   tokens: Iterable[str]) -> dict[str, float]:
    """token -> 近似 IDF 权重：在本文档段落间出现得越少越有区分度。"""
    picked: list[str] = []
    for raw in tokens or []:
        token = str(raw).strip()
        if len(token) < MIN_TOKEN_LEN or token in picked:
            continue
        picked.append(token)
        if len(picked) >= MAX_TOKENS:
            break
    if not picked:
        return {}
    n = len(units)
    weights: dict[str, float] = {}
    for token in picked:
        df = sum(1 for s, e in units if text.find(token, s, e) != -1)
        if df:
            weights[token] = math.log(1.0 + n / (1.0 + df))
    return weights


def _best_window(text: str, start: int, end: int,
                 weights: dict[str, float], limit: int) -> tuple[int, int]:
    """在 [start, end) 内找权重和最大的 limit 字窗口（双指针，O(命中数)）。"""
    points: list[tuple[int, float]] = []
    for token, weight in weights.items():
        idx = text.find(token, start, end)
        while idx != -1:
            points.append((idx, weight))
            idx = text.find(token, idx + 1, end)
    if not points:
        return start, start + limit
    points.sort()
    best_sum = -1.0
    best_span = (points[0][0], points[0][0])
    total = 0.0
    left = 0
    for right, (pos, weight) in enumerate(points):
        total += weight
        while pos - points[left][0] > limit:
            total -= points[left][1]
            left += 1
        if total > best_sum:
            best_sum = total
            best_span = (points[left][0], pos)
    lo, hi = best_span
    win_start = lo - (limit - (hi - lo)) // 2
    win_start = max(start, min(win_start, end - limit))
    return win_start, win_start + limit


def _expand(src: str, units: list[tuple[int, int]], scores: list[float], best: int,
            start: int, end: int, limit: int) -> tuple[int, int]:
    """最佳段短于预算时，向两侧补入同样命中 token（score>0）的相邻段。"""
    left, right = best - 1, best + 1
    while True:
        remaining = limit - (end - start)
        if remaining <= 0:
            break
        candidates: list[tuple[float, int]] = []
        if left >= 0 and scores[left] > 0:
            candidates.append((scores[left], left))
        if right < len(units) and scores[right] > 0:
            candidates.append((scores[right], right))
        if not candidates:
            break
        candidates.sort(reverse=True)
        picked: tuple[int, int] | None = None
        for _score, idx in candidates:
            need = (start - units[idx][0]) if idx < best else (units[idx][1] - end)
            if need <= remaining:
                picked = (idx, need)
                break
        if picked is None:
            break
        idx, _need = picked
        if idx < best:
            start = units[idx][0]
            left = idx - 1
        else:
            end = units[idx][1]
            right = idx + 1
    return start, end


def select_passage(text: str, tokens: Iterable[str],
                   budget: int = DEFAULT_PASSAGE_CHARS) -> Passage:
    """从文档正文里选出与 query token 最相关的一段（纯本地、无网络、无分词）。

    1. 按空行切段，token 命中最多的段胜出（命中 token 越多、越稀有，分数越高）；
    2. 段短于预算则并入相邻的命中段，段长于预算则取段内权重和最大的窗口；
    3. 完全没有命中（或正文为空）时回退为正文开头窗口。
    """
    src = str(text or "")
    limit = max(int(budget), 1)
    if not src.strip():
        return Passage("", 0, 0, 0.0, False, False)
    if len(src) > MAX_SCAN_CHARS:
        window = src.strip()[:limit]
        head = src.find(window)
        return Passage(window, head, head + len(window), 0.0, bool(src[:head].strip()),
                       bool(src[head + len(window):].strip()))

    units = _split_units(src)
    if not units:
        return Passage("", 0, 0, 0.0, False, False)
    weights = _token_weights(src, units, tokens)
    scores = [sum(w for t, w in weights.items() if src.find(t, s, e) != -1)
              for s, e in units]
    best = max(range(len(units)), key=lambda i: scores[i]) if scores else -1
    if best < 0 or scores[best] <= 0:
        window = src.strip()[:limit]
        head = src.find(window)
        return Passage(window, head, head + len(window), 0.0, bool(src[:head].strip()),
                       bool(src[head + len(window):].strip()))

    start, end = units[best]
    if end - start > limit:
        start, end = _best_window(src, start, end, weights, limit)
    else:
        start, end = _expand(src, units, scores, best, start, end, limit)

    raw = src[start:end]
    lead = len(raw) - len(raw.lstrip())
    tail = len(raw) - len(raw.rstrip())
    snippet = raw.strip()
    start += lead
    end -= tail
    return Passage(snippet, start, end, float(scores[best]),
                   bool(src[:start].strip()), bool(src[end:].strip()))


class Plugin(FaustPlugin):
    def __init__(self) -> None:
        self.ctx: PluginContext | None = None

    @staticmethod
    def _one_line(text: str) -> str:
        """片段压成单行：注入块里每条记忆固定为「路径行 + `> 片段` 行」。"""
        return " ".join(str(text or "").split())

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
            {"key": "PASSAGE_CHARS", "type": "int", "label": "单条记忆注入的片段字数上限",
             "default": DEFAULT_PASSAGE_CHARS},
            {"key": "MAX_TOTAL_CHARS", "type": "int", "label": "单轮所有记忆片段的合计字数上限",
             "default": DEFAULT_MAX_TOTAL_CHARS},
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

    @staticmethod
    def _is_image_path(path: str) -> bool:
        suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        return suffix in IMAGE_SUFFIXES

    @staticmethod
    async def _read_content(memory: Any, path: str) -> str:
        """取记忆文档正文；节点已删除/不可读时告警并回退 description。"""
        try:
            result = await memory.file_read(path)
        except Exception as exc:
            log.warning("读取记忆正文失败 path=%s: %s", path, exc)
            return ""
        return str((result or {}).get("content") or "")

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
        passage_chars = max(int(await cfg.get_config("PASSAGE_CHARS", DEFAULT_PASSAGE_CHARS)
                               or DEFAULT_PASSAGE_CHARS), 1)
        total_chars = max(int(await cfg.get_config("MAX_TOTAL_CHARS", DEFAULT_MAX_TOTAL_CHARS)
                              or DEFAULT_MAX_TOTAL_CHARS), 1)

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

        remaining = total_chars
        lines: list[str] = []
        for it in items:
            path = str(it["path"])
            desc = str(it.get("description") or "").strip().replace("\n", " ")[:120]
            line_count = int(it.get("line_count") or 0)
            snippet = ""
            if remaining > 0 and not self._is_image_path(path):
                content = await self._read_content(memory, path)
                if content:
                    if line_count <= 0:
                        line_count = len(content.splitlines())
                    chosen = select_passage(content, info, min(passage_chars, remaining))
                    snippet = self._one_line(chosen.text)
            if not snippet and desc and remaining > 0:
                # 图片或读不出正文：退回文档 description（旧行为）
                snippet = desc[: min(passage_chars, remaining)]
            remaining -= len(snippet)
            meta = f"约{line_count}行" if line_count > 0 else "行数未知"
            head = f"- {path} ({meta})"
            lines.append(f"{head}\n  > {snippet}" if snippet else head)

        log.info("注入 %d 条记忆 (mode=%s, 片段=%d/%d字): %s",
                 len(items), mode, total_chars - remaining, total_chars,
                 [it["path"] for it in items])
        return text + "\n\n[Memory]\n" + "\n".join(lines)
