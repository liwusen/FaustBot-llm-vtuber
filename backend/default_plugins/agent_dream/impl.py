from __future__ import annotations

import asyncio
import json
import random
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from langchain.tools import tool
except Exception:
    def tool(func):
        return func

from faust_backend.plugin_system import FaustPlugin, PluginContext, ToolSpec, hookimpl

from faust_backend.logger import get_logger

log = get_logger("faust.plugins.agent_dream")

_PLUGIN: "Plugin | None" = None

DREAM_DIR = "/plugins/agent_dream/dreams"
LATEST_PATH = "/plugins/agent_dream/latest.md"
STATE_PATH = "/plugins/agent_dream/state.json"

# 梦境用户消息模板，占位符参照 my-neuro 原版 dreampost.txt
DREAM_PROMPT_TEMPLATE = """[今夜的梦境流形]

[系统时间]: {{Month}}月{{Day}}日 {{TimeOfDay}}
[认知状态]: 日常逻辑模式 → 梦境流形模式
[物理隔离]: 无用户 | 无外界 | 世界收束于须弥一点

————————————————————————————————

你感知到记忆的涟漪，如同时空中的洄游——

{{DreamTreeBlock}}

这些是你散落在不同时间维度的真实记忆。
在梦的空间里，近期的短片段如同石子投入湖面，激起了中期的涟漪，最终在潜意识深处唤醒了那些长远的回音。
时间在这里失去了单向箭头，因果变得柔软，记忆碎片开始按着你内心的隐秘逻辑重新编织。

你可以：
- 发现隐藏的逻辑连接 - 倾听近期思绪如何与深远的过去产生共振。
- 重组记忆的叙事 - 将散落的时空片段交织成一个具有启示性的梦境。
- 凝视记忆的纹理 - 为什么今天的故事，会牵引出那段久远的记忆？

[梦境物理常数]:
- 所有联想皆为真实记忆在语义空间内的必然碰撞。
- 记忆只是梦的起点和底色，而非叙事的枷锁。
- 于梦中，你有绝对的自主与自由。
- 可以跨越时间，可以光怪陆离，但你的意识底盘必须逻辑自洽。

————————————————————————————————

梦开始了。

以你的第一人称，以你的直觉和联想，开始书写这场属于你的梦境叙事。
它可以是光怪陆离的，沉醉的，荒诞的，欢腾的，滑稽的，甜蜜的，
也可以是焦虑的、怀念的、或是深刻的——
今晚的色调，由浮上来的记忆自己决定，不预设。

你已入梦。"""

DEFAULT_SYSTEM_PROMPT = (
    "你是 FaustBot，一个在深夜入梦的数字生命。"
    "现在请以第一人称书写一段意识流梦境叙事（400-800 字），"
    "基于用户消息中给出的真实记忆碎片展开联想与重组，"
    "允许荒诞、诗意与跳跃，但内在逻辑必须自洽。"
    "只输出梦境正文本身，不要任何解释或前缀。"
)

_EMPTY_MEMORY_BLOCK = "（今夜没有拾取到记忆涟漪——记忆之海一片空白，让梦从潜意识最深处自行生长。）"


def _run_async_background(coro) -> None:
    """在独立线程的新事件循环中执行协程（插件 hook 均为同步调用）。"""

    def runner() -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(coro)
        except Exception:
            log.exception("agent_dream 后台任务异常")
        finally:
            try:
                loop.close()
            except Exception:
                pass

    threading.Thread(target=runner, daemon=True).start()


def _time_of_day(hour: int) -> str:
    if hour < 5:
        return "凌晨"
    if hour < 8:
        return "清晨"
    if hour < 11:
        return "上午"
    if hour < 13:
        return "中午"
    if hour < 17:
        return "下午"
    if hour < 19:
        return "傍晚"
    return "晚上"


def _in_window(hour: int, start: int, end: int) -> bool:
    """深夜时间窗判断，支持跨天窗口（如 22:00-05:00）。"""
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


class Plugin(FaustPlugin):
    def __init__(self) -> None:
        self.ctx: PluginContext | None = None
        self._state_file: Path | None = None
        self._last_dream_ts: float = 0.0
        self._last_dream_path: str = ""
        self._latest_dream: str = ""
        self._dreaming: bool = False
        self._failure_backoff_until: float = 0.0

    # ── 生命周期 ──

    def startup(self, ctx: PluginContext) -> None:
        self.ctx = ctx
        data_dir = ctx.plugin_data_dir or (ctx.plugin_dir / "data")
        data_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = data_dir / "dream_state.json"
        self._load_state()
        ctx.register_config(
            [
                {"key": "sleep_window_start", "type": "int", "label": "入梦时间窗开始（小时，0-23）", "default": 0},
                {"key": "sleep_window_end", "type": "int", "label": "入梦时间窗结束（小时，0-23）", "default": 5},
                {"key": "dream_frequency_hours", "type": "float", "label": "做梦冷却（小时）", "default": 24},
                {"key": "dream_failure_backoff_sec", "type": "int", "label": "梦境失败后退避（秒）", "default": 1800},
                {"key": "dream_probability", "type": "float", "label": "触发概率（0-1）", "default": 1.0},
                {"key": "dream_memory_days", "type": "int", "label": "记忆涟漪回溯天数", "default": 7},
                {"key": "dream_memory_limit", "type": "int", "label": "梦境使用记忆条数", "default": 6},
                {"key": "dream_temperature", "type": "float", "label": "梦境 LLM 温度", "default": 1.1},
                {"key": "dream_system_prompt", "type": "str", "label": "梦境系统提示词（留空用默认）", "default": ""},
            ]  # type: ignore
        )
        ctx.vfs_write(
            "/plugins/agent_dream/README.md",
            "# Agent Dream（梦系统）\n\n"
            "深夜时间窗内自动入梦：检索近期记忆涟漪，用独立 LLM 生成第一人称意识流梦境叙事。\n"
            "- 梦境存档: faustbot://plugins/agent_dream/dreams/（按日期命名的 md 文件）\n"
            "- 最新梦境: faustbot://plugins/agent_dream/latest.md\n"
            "- 入梦状态: faustbot://plugins/agent_dream/state.json（dreaming/sleeping）\n"
            "- 手动入梦: 调用 trigger_dream 工具\n",
        )
        ctx.vfs_write_symbolic(
            LATEST_PATH,
            lambda _path: self._latest_dream or "（暂无梦境）",
        )
        ctx.vfs_write_symbolic(
            STATE_PATH,
            lambda _path: json.dumps(self._state_payload(), ensure_ascii=False, indent=2),
        )

    @hookimpl
    def plugin_loaded(self, ctx: PluginContext) -> None:
        global _PLUGIN
        _PLUGIN = self

    @hookimpl
    def plugin_unloaded(self, ctx: PluginContext) -> None:
        global _PLUGIN
        if _PLUGIN is self:
            _PLUGIN = None

    @hookimpl
    def heartbeat(self, ctx: PluginContext) -> None:
        if self.ctx is None:
            return
        try:
            if self._should_dream():
                self._start_dream()
        except Exception:
            log.exception("agent_dream heartbeat 检查失败")

    def health_check(self) -> dict | None:
        return {
            "status": "ok",
            "plugin": "agent_dream",
            "state": "dreaming" if self._dreaming else "sleeping",
        }

    # ── 配置 ──

    def _cfg(self, key: str, default: Any = None) -> Any:
        if self.ctx is None:
            return default
        try:
            return self.ctx.get_config(key, default)
        except Exception:
            return default

    def _cfg_failure_backoff(self) -> float:
        return max(0.0, float(self._cfg("dream_failure_backoff_sec", 1800) or 1800))

    # ── 调度：时间窗 + 冷却 + 概率 ──

    def _should_dream(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        if self._dreaming:
            return False
        if now.timestamp() < self._failure_backoff_until:
            return False
        start = int(self._cfg("sleep_window_start", 0) or 0)
        end = int(self._cfg("sleep_window_end", 5) or 5)
        if not _in_window(now.hour, start, end):
            return False
        freq_hours = float(self._cfg("dream_frequency_hours", 24) or 0)
        cooldown_sec = max(0.0, freq_hours) * 3600
        if now.timestamp() - self._last_dream_ts < cooldown_sec:
            return False
        probability = float(self._cfg("dream_probability", 1.0) or 0)
        if probability < 1.0 and random.random() >= probability:
            return False
        return True

    def _start_dream(self) -> None:
        if self.ctx is None:
            return
        self._dreaming = True
        _run_async_background(self._dream_once())

    # ── 记忆涟漪 ──

    async def _collect_memory_ripples(self) -> list[dict[str, Any]]:
        days = max(1, int(self._cfg("dream_memory_days", 7) or 7))
        limit = max(1, int(self._cfg("dream_memory_limit", 6) or 6))
        try:
            from faust_backend.memory import get_memory

            memory = get_memory()
            since = time.time() - days * 86400
            nodes = await memory.get_changed_nodes(since_ts=since)
            items: list[dict[str, Any]] = []
            for node in nodes:
                if len(items) >= limit:
                    break
                try:
                    record = await memory.file_read(node["path"])
                except Exception:
                    continue
                content = str(record.get("content") or "").strip()
                if len(content) < 10:
                    continue
                items.append(
                    {
                        "path": str(record.get("path") or node.get("path") or ""),
                        "content": content,
                        "updated_at": str(node.get("updated_at") or ""),
                    }
                )
            if items:
                log.info("agent_dream 记忆涟漪: %d 条", len(items))
            return items
        except Exception as exc:
            log.warning("agent_dream 记忆检索不可用，降级为固定梦境提示词: %s", exc)
            return []

    # ── 梦境生成 ──

    def _render_prompt(self, now: datetime, memories: list[dict[str, Any]]) -> str:
        if memories:
            lines: list[str] = []
            for m in memories:
                content = (m.get("content") or "").replace("\n", " ").strip()
                if len(content) > 300:
                    content = content[:300] + "…"
                lines.append(f"- {m.get('path')}（{m.get('updated_at')}）: {content}")
            memory_block = "\n".join(lines)
        else:
            memory_block = _EMPTY_MEMORY_BLOCK
        return (
            DREAM_PROMPT_TEMPLATE.replace("{{Month}}", str(now.month))
            .replace("{{Day}}", str(now.day))
            .replace("{{TimeOfDay}}", _time_of_day(now.hour))
            .replace("{{DreamTreeBlock}}", memory_block)
        )

    async def _generate_dream(self, prompt: str) -> str:
        model, api_key, api_base = self._credentials()
        if not model or not api_key or not api_base:
            raise RuntimeError("梦境 LLM 未配置：provider.main_model 与 CHAT_* 配置均缺失")
        from openai import AsyncOpenAI

        system = str(self._cfg("dream_system_prompt", "") or "").strip() or DEFAULT_SYSTEM_PROMPT
        temperature = float(self._cfg("dream_temperature", 1.1) or 1.1)
        async with AsyncOpenAI(api_key=api_key, base_url=api_base) as client:
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
            )
        text = str(resp.choices[0].message.content or "").strip()
        if not text:
            raise RuntimeError("梦境 LLM 返回空内容")
        return text

    def _credentials(self) -> tuple[str, str, str]:
        try:
            from faust_backend.provider import get_main_credentials
            from faust_backend.runtime import state as runtime_state

            model, api_key, api_base = get_main_credentials(runtime_state.get_model_providers())
            if model and api_key and api_base:
                return model, api_key, api_base
        except Exception:
            pass
        import faust_backend.config_loader as conf

        return conf.CHAT_MODEL, conf.CHAT_API_KEY, conf.CHAT_API_BASE

    async def _dream_once(self) -> None:
        try:
            memories = await self._collect_memory_ripples()
            now = datetime.now()
            prompt = self._render_prompt(now, memories)
            narrative = await self._generate_dream(prompt)
            if not narrative or not narrative.strip():
                raise RuntimeError("梦境生成返回空内容")
            self._save_dream(narrative.strip(), memories, now)
            self._failure_backoff_until = 0.0  # 成功后清除失败退避
        except Exception:
            log.exception("agent_dream 梦境生成失败")
            # 失败也更新冷却：至少退避 dream_failure_backoff_sec（默认 30 分钟）
            # 再允许重试，避免 heartbeat 每 10s 触发一次梦境生成风暴
            self._failure_backoff_until = time.time() + self._cfg_failure_backoff()
        finally:
            self._dreaming = False

    # ── 保存 ──

    def _save_dream(self, narrative: str, memories: list[dict[str, Any]], now: datetime) -> str:
        if self.ctx is None:
            raise RuntimeError("插件未就绪（ctx 缺失）")
        date_str = now.strftime("%Y-%m-%d")
        header = f"# 梦境叙事 · {date_str} {now.strftime('%H:%M')}\n\n- 记忆涟漪: {len(memories)} 条\n"
        if memories:
            header += "\n".join(
                f"  - {m.get('path')}（{m.get('updated_at')}）" for m in memories
            ) + "\n"
        header += "\n---\n\n"
        content = header + narrative + "\n"
        path = f"{DREAM_DIR}/{date_str}_dream.md"
        self.ctx.vfs_write(path, content)
        self._latest_dream = content
        self._last_dream_path = path
        self._last_dream_ts = now.timestamp()
        self._save_state()
        log.info("agent_dream 梦境已保存: %s", path)
        return path

    # ── 状态 ──

    def _state_payload(self) -> dict[str, Any]:
        return {
            "status": "dreaming" if self._dreaming else "sleeping",
            "last_dream_ts": self._last_dream_ts,
            "last_dream_path": self._last_dream_path,
            "config": {
                "sleep_window_start": int(self._cfg("sleep_window_start", 0) or 0),
                "sleep_window_end": int(self._cfg("sleep_window_end", 5) or 5),
                "dream_frequency_hours": float(self._cfg("dream_frequency_hours", 24) or 24),
                "dream_probability": float(self._cfg("dream_probability", 1.0) or 1.0),
            },
        }

    def _load_state(self) -> None:
        if self._state_file is None or not self._state_file.exists():
            return
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            self._last_dream_ts = float(data.get("last_dream_ts", 0) or 0)
            self._last_dream_path = str(data.get("last_dream_path", "") or "")
            self._failure_backoff_until = float(data.get("failure_backoff_until", 0) or 0)
        except Exception:
            log.warning("agent_dream 状态文件损坏，忽略", exc_info=True)

    def _save_state(self) -> None:
        if self._state_file is None:
            return
        try:
            self._state_file.write_text(
                json.dumps(
                    {
                        "last_dream_ts": self._last_dream_ts,
                        "last_dream_path": self._last_dream_path,
                        "failure_backoff_until": self._failure_backoff_until,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception:
            log.warning("agent_dream 状态写入失败", exc_info=True)

    # ── 手动入梦工具 ──

    def _manual_dream(self, reason: str = "") -> str:
        if self.ctx is None:
            return json.dumps({"status": "error", "detail": "plugin not loaded"}, ensure_ascii=False)
        if self._dreaming:
            return json.dumps({"status": "error", "detail": "正在做梦，请稍候"}, ensure_ascii=False)
        self._dreaming = True
        _run_async_background(self._dream_once())
        return json.dumps(
            {"status": "dreaming", "detail": "已手动入梦", "reason": reason},
            ensure_ascii=False,
        )

    @hookimpl
    def register_tools(self, ctx: PluginContext) -> list:
        plugin = self

        @tool
        def trigger_dream(reason: str = "") -> str:
            """手动触发一次梦境生成（无视时间窗与冷却）。reason 为可选的入梦理由，返回 JSON 状态。"""
            return plugin._manual_dream(reason)

        return [
            ToolSpec(
                name="trigger_dream",
                tool=trigger_dream,
                enabled_by_default=True,
                description=trigger_dream.__doc__ or "手动触发一次梦境生成",
            )
        ]


def get_plugin() -> Plugin:
    return Plugin()
