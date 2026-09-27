"""感知源模块的统一接口。

desktop-mood 的感知源被拆成若干自描述模块（dm_sensors_*、dm_timeline、dm_external），
每个模块导出：

    SOURCES: tuple[dict, ...]        # 源元数据（见 dm_sources.py 的字段说明）
    FIELD_LABELS: dict[str, str]     # 新增字段的中文名
    FORMATTERS: dict[str, Callable]  # 新增字段的面板显示格式化函数
    async def collect(sctx: SensorContext) -> SensorResult

约定（务必遵守，否则会污染上下文/面板/规则）：

1. `collect` 绝不抛异常：单个源失败时把原因写进 `SensorResult.status`，
   不要静默返回空——面板要把"为什么没数据"显示给用户（仓库规则：不隐瞒错误）。
2. 只返回自己声明过的字段（`SOURCES[*]['fields']` 里出现过的名字）。
3. 分级/单源开关由调用方统一判断，模块内用 `sctx.enabled(source_id)` 决定是否采集；
   被关闭的源必须完全不产生字段。
4. 运行态（上一轮值、累加器、后台线程句柄）放 `sctx.memory`（进程内、不落盘）；
   需要跨重启保存的内容走 `sctx.store`。
5. 中价/重价采集自己声明 `cadence`（秒），别每 10 秒心跳都跑。
6. 不写日志刷屏：`sctx.log.debug` 用于常规，偶发失败才用 `warning`。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class SensorResult:
    """一次采集的产出。"""

    fields: dict[str, Any] = field(default_factory=dict)
    # source_id -> 不可用原因（例如 '未安装 nvidia-smi'、'该设备无环境光传感器'）
    status: dict[str, str] = field(default_factory=dict)

    def merge(self, other: "SensorResult") -> "SensorResult":
        self.fields.update(other.fields)
        self.status.update(other.status)
        return self


@dataclass
class SensorContext:
    """模块采集时能拿到的一切。"""

    now: float
    observed: dict[str, Any]           # 上一轮合并后的上下文字段（做差分/边沿用）
    context: dict[str, Any]            # 本轮正在构建的上下文字段（可读前面模块的产出）
    memory: dict[str, Any]             # 模块自用的进程内运行态（键名请带模块前缀）
    external: dict[str, Any]           # 外部上报：{'electron': {...}, 'faust_ui': {...}, ...}
    store: Any                         # DesktopMoodStore（持久化状态）
    enabled: Callable[[str], bool]     # 源是否开启（分级 ∧ 单源）
    log: Any                           # logger
    get_config: Callable[..., Any] | None = None  # async (key, default) -> 插件配置值
    due: Callable[[str], bool] | None = None      # 本轮的源是否到采样时间（cadence）

    def is_due(self, source_id: str) -> bool:
        """中价/重价源请先问这个，避免每 10 秒心跳都做重活。"""
        return True if self.due is None else bool(self.due(source_id))

    async def config(self, key: str, default: Any = None) -> Any:
        if self.get_config is None:
            return default
        return await self.get_config(key, default)

    def carry(self, field_name: str, default: Any = None) -> Any:
        """上一轮的字段值（用于差分/边沿判断）。"""
        return self.observed.get(field_name, default)

    def external_fields(self, source: str) -> dict[str, Any]:
        """某个外部上报源的字段（如 'electron' / 'faust_ui'）。"""
        value = self.external.get(source)
        return value if isinstance(value, dict) else {}

    async def to_thread(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """把阻塞调用（ctypes/winsdk/子进程读）挪到线程，别卡事件循环。"""
        if kwargs:
            return await asyncio.to_thread(lambda: func(*args, **kwargs))
        return await asyncio.to_thread(func, *args)
