# Processor 机制（子进程计算任务）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> 设计规格（本地文件，未纳入 git）：`docs/superpowers/specs/2026-09-25-processor-mechanism-design.md`。
> 本计划是实现该规格的唯一依据；两者冲突时以本计划为准（差异见文末「与规格的差异」）。

**Goal:** 给 FaustBot 后端引入 `ProcessorManager`，把重计算（VAD、easyocr OCR）搬进受管子进程，主进程只做引用计数与调用，空闲时自动回收 worker 的内存/显存。

**Architecture:** 父进程用 `asyncio.start_server` 在 `127.0.0.1:0` 开一条带 token 的一次性控制通道，`asyncio.create_subprocess_exec` 以**脚本路径**拉起 `backend/faust_backend/processors/worker.py`（`.runtime` 的 `._pth` 让 `-m 包名`/`PYTHONPATH` 都不可用）；父子之间用「4 字节长度 + pickle」帧通信（`StartOp` / `InvokeOp` / `StopOp` 下行，`Hello` / `Phase` / `Started` / `StartFailed` / `Result` / `LogMsg` 上行）。父进程侧每个 Processor 一个 `ProcessorHandle`（状态机 + FIFO 请求队列 + 日志环形缓冲），调用方通过 `ProcessorLease`（引用计数，支持 `async with`）使用它；`prune` 回收长期空闲的 worker。VAD 与 OCR 都改由子进程承载，`vad_runtime.py` 删除，主进程不再加载 torch / easyocr。

**Tech Stack:** Python 3（`.runtime/`）、asyncio（Windows ProactorEventLoop）、`asyncio.create_subprocess_exec`、pickle 帧、FastAPI（admin 路由）、pluggy（插件 hook）、pytest + pytest-asyncio。

## Global Constraints

- Python 解释器：`.runtime/python.exe`（仓库根下）。测试命令：`.runtime/python.exe -m pytest backend/tests -q`，cwd = 仓库根（`D:/dev/faustbot/faust`）。
- 分支：所有提交进 `dev` 分支，**禁止**提交 `main`；每个 Task 一个 commit，提交信息用中文 `feat: ...` / `refactor: ...` 风格。
- 无新依赖：`psutil`、`numpy`、`pytest-asyncio` 已在 `requirements.txt`；本计划**不改** `requirements.txt`。
- 异步优先：外部可见的 API 全部 async；文件与锁用 `(async) with` 上下文管理器。唯一允许的同步边界是「子进程内执行 Processor 钩子」，通过 `asyncio.to_thread` 隔离（钩子按设计是同步函数）。
- 错误不静默：任何失败都必须抛异常或至少写 ERROR 日志；禁止 `except: pass`、禁止猜测式 `getattr` 取依赖模块属性。
- 位置约定：本机制的全部实现代码位于 `backend/faust_backend/processors/`；新增测试位于 `backend/tests/`。
- **`.runtime` 是内嵌发行版**：存在 `python311._pth`，因此它**忽略 `PYTHONPATH`**，也不把 cwd/脚本目录放进 `sys.path`。任何「在子进程里 import `faust_backend`」的代码都必须自己把 `backend/` 插进 `sys.path`（`backend/main.py` 与 `processors/worker.py` 都这么做）；子进程只能以**脚本路径**启动，不能 `-m 包名`。
- 不新增 Agent 工具、不改 `agents/faust/*.md`（本版不把 Processor 暴露给 Agent）。
- **非目标**（照规格 §2 执行，不要顺手扩大范围）：不迁移 funasr（ASR）与 GPT-SoVITS（TTS）的 `.bat` 服务（`service_manager.py` 保持原样）；不做请求优先级；不做 invoke 抢占/协作式取消；不做单名字多实例（一个名字 = 一个子进程）。
- 控制通道只监听/连接 `127.0.0.1`，不对外开放端口。
- 前端不动（本计划不改 `frontend/**`）；`/faust/audio/vad/status` 的既有字段名必须保持不变（前端已有消费者）。
- 现有约定：日志用 `faust_backend.logger.get_logger("faust.processor.<NAME>")`；配置项通过 `faust_backend/config_loader.py` 的模块级大写全局暴露，并在 `backend/faust.config.example.json` + `docs/configuration.md` 登记。

## 文件结构（File Structure）

| 文件 | 责任 | Task |
| --- | --- | --- |
| `backend/faust_backend/processors/__init__.py` | 公开导出（轻量；manager 用 PEP 562 惰性导出，见「与规格的差异」3） | 1 |
| `backend/faust_backend/processors/errors.py` | 异常层级（9 个） | 1 |
| `backend/faust_backend/processors/protocol.py` | 帧编解码 + 消息 dataclass | 2 |
| `backend/faust_backend/processors/registry.py` | 名字 → 类 注册表、`@processor`、owner 归属 | 3 |
| `backend/faust_backend/processors/base.py` | `Processor` 基类、`ProcessorContext`、ProcessorData/marker 解析 | 4 |
| `backend/faust_backend/processors/worker.py` | 子进程入口：握手 → setup? → start → 串行 invoke → stop | 5 |
| `backend/faust_backend/processors/manager.py` | `ProcessorManager` / `ProcessorHandle` / `ProcessorLease` / `PruneReport` / `prune_loop` | 5-8 |
| `backend/faust_backend/processors/builtin/__init__.py` | 导入内置 Processor（触发注册） | 9 |
| `backend/faust_backend/processors/builtin/vad.py` | `VadProcessor`（silero-vad） | 9 |
| `backend/faust_backend/processors/builtin/ocr.py` | `OcrProcessor`（easyocr） | 11 |
| `backend/faust_backend/processors/admin_api.py` | admin 路由（4 个端点） | 14 |
| `backend/faust_backend/plugin_system/hooks.py` | 新增 hookspec `register_processors` | 13 |
| `backend/faust_backend/plugin_system/plugin_base.py` | `register_processors` 默认实现 | 13 |
| `backend/faust_backend/plugin_system/manager.py` | 注册/卸载插件 Processor | 13 |
| `backend/faust_backend/routes/audio.py` | VAD WS/status 改走 lease | 10 |
| `backend/faust_backend/runtime/lifecycle.py` | 删除 vad_runtime 接线；启动 prune 循环、关闭时 shutdown | 10 |
| `backend/faust_backend/runtime/state.py` | 新增 `processor_prune_task` | 10 |
| `backend/faust_backend/config_loader.py` | 新增 `PROCESSOR_PRUNE_INTERVAL` / `PROCESSOR_IDLE_TIMEOUT` | 8 |
| `backend/faust_backend/download_vad.py` | 抽出 `ensure_vad_cache()`，与 `VadProcessor.setup` 共用一份下载/校验逻辑（`main()` 对 CI 行为不变） | 9 |
| `AGENTS.md` | 语音管道条目里的 VAD 文件路径改为 `processors/builtin/vad.py` | 10 |
| `backend/default_plugins/ui_operator/main.py` | `screenOCRTool` 改走 OCR Processor | 12 |
| `backend/main.py` | 注册 `processors.admin_api.router` | 14 |
| `backend/tests/test_processor_manager.py` | manager/协议/注册表/基类/VAD/OCR 的测试（含 fake Processor 定义） | 1-11 |
| `backend/tests/test_vad_ws_lease.py` | VAD WS 的 lease/降级/自愈测试 | 10 |
| `backend/tests/test_ui_operator_ocr.py` | `screenOCRTool` 走 OCR Processor 的测试 | 12 |
| `backend/tests/test_processor_plugins.py` | 插件注册/卸载 Processor 的测试 | 13 |
| `backend/tests/test_processor_admin_api.py` | admin 路由的测试（TestClient） | 14 |
| `backend/faust_backend/vad_runtime.py` | **删除** | 10 |

（删除：`backend/faust_backend/vad_runtime.py`。改动：`backend/faust.config.example.json`、`docs/configuration.md`、`docs/plugin-api-reference.md`、`docs/plugins/ui-operator.md`。）## 与规格的差异（本计划的细化，实施时以本节为准）

1. **帧集合补全**：规格 §6.2 的 `InvokeOp(data)` 没有请求 id，且没有「启动成功」信号。本计划定义 `InvokeOp(req_id, data)`、`Started(setup_ran, fingerprint)`、`StartFailed(error)`，`Result` 仍按 `req_id` 唤醒等待方。
2. **`StartOp` 携带 `data_dir`**：ProcessorData 目录由**父进程**解析（父进程要据此判断 marker 命中并决定 `run_setup`），随 `StartOp` 下发；子进程直接使用，不再自行推导。避免「测试里 monkeypatch 了 `DATA_DIR`，子进程却按字面量写 marker」的父子不一致。
3. **`processors/__init__.py` 不 import manager**：worker 子进程会 import 本包（`faust_backend.processors.protocol` 等），若包导入顺带拉起 manager，就会连带初始化 `faust_backend.logger` 的 root handler（文件日志 + WS 队列）与 `psutil`，导致子进程重复写同一个日志文件。因此 `ProcessorManager` 等名字用 PEP 562 `__getattr__` 惰性导出（调用方 `from faust_backend.processors import get_processor_manager` 写法不变）。
4. **registry 的 `loader` 字段**：内置 Processor 用 `cls.__module__`（精确到单文件，VAD 子进程不会牵连 OCR 模块），插件 Processor 用源文件绝对路径（插件入口模块名是 `faust_plugin_<id>`，在子进程里不可导入）。
5. **`SETUP_TIMEOUT` / `START_TIMEOUT` 分两段计时**：父进程用子进程的 `Phase("starting")` 帧作为第一段的结束点（等 setup 完成），再用 `START_TIMEOUT` 等 `Started`/`StartFailed`。
6. **审计字段**：`status()` 在规格 §19 字段基础上加 `phase`（子进程上报的当前阶段）；`Started` 帧带回子进程计算的 `fingerprint`（父进程只做记录与对比）。
7. **子进程必须以「脚本路径」启动**：`.runtime` 是内嵌发行版（带 `python311._pth`），它**忽略 `PYTHONPATH`**、也不把 cwd/脚本目录放进 `sys.path`，所以 `python -m faust_backend.processors.worker` 与「靠 PYTHONPATH 传 backend」都不可行。改为 `python <repo>/backend/faust_backend/processors/worker.py`，由 `worker.py` 顶部自举 `sys.path`（写法对齐 `backend/main.py`）。同理，所有 `python.exe -c "import faust_backend..."` 的验证命令都要先 `sys.path.insert(0, 'backend')`。
8. **插件 Processor 在子进程里按 `NAME` 就地解析**：插件入口模块只在父进程被 `register_plugin_processors` 登记（子进程里没有那段代码），因此 worker 在加载完 `--registry-module` 指定的模块后，会在这几个模块里按 `NAME` 找到 `Processor` 子类并就地注册（`_resolve_processor_class`）。内置 Processor 则由 `@processor("VAD")` 装饰器在 import 时自行注册。
9. **握手失败必须回传子进程输出**：`stdout/stderr` 的按行采集要在握手成功之后才开始，所以握手超时/校验失败时会先把子进程已有输出捞回来（`_drain_child_output`：terminate → 按行读到「窗口内无更多输出」），并入异常与日志，避免「启动失败但看不到原因」。

---

### Task 1: processors 包骨架 + 异常层

**Files:**
- Create: `backend/faust_backend/processors/__init__.py`
- Create: `backend/faust_backend/processors/errors.py`
- Test: `backend/tests/test_processor_manager.py`（本次创建，头部 + 异常测试）

**Interfaces:**
- Produces: `faust_backend.processors.errors` 下 9 个异常类。后续所有 Task 依赖它们：
  `ProcessorError`（基类）、`ProcessorNotFoundError(name)`、`ProcessorStartError(message, *, last_error=None, logs=None)`、`ProcessorNotReadyError`、`ProcessorTimeoutError(message, *, phase=None)`、`ProcessorCrashedError(message, *, exit_code=None)`、`ProcessorConfigMismatchError`、`ProcessorInvokeError(message, *, error_type="", traceback_text="")`、`ProcessorLeaseError`、`ProcessorFrameError`。
- Produces: `from faust_backend.processors import Processor, ProcessorContext, processor, register_processor`（`__init__.py` 的静态导出；manager 相关名字惰性导出，Task 6 补上）。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_processor_manager.py`：

```python
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import numpy as np
import psutil
import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── Task 1: 异常层 ──────────────────────────────────────────


def test_exception_hierarchy_and_payloads():
    from faust_backend.processors import errors

    for exc in (
        errors.ProcessorNotFoundError,
        errors.ProcessorStartError,
        errors.ProcessorNotReadyError,
        errors.ProcessorTimeoutError,
        errors.ProcessorCrashedError,
        errors.ProcessorConfigMismatchError,
        errors.ProcessorInvokeError,
        errors.ProcessorLeaseError,
        errors.ProcessorFrameError,
    ):
        assert issubclass(exc, errors.ProcessorError)

    not_found = errors.ProcessorNotFoundError("VAD")
    assert not_found.name == "VAD"
    assert "VAD" in str(not_found)

    start_error = errors.ProcessorStartError(
        "启动失败", last_error="ModuleNotFoundError: torch", logs=[{"level": "ERROR", "message": "boom"}]
    )
    assert start_error.last_error == "ModuleNotFoundError: torch"
    assert start_error.logs == [{"level": "ERROR", "message": "boom"}]

    timeout = errors.ProcessorTimeoutError("start 超时", phase="start")
    assert timeout.phase == "start"

    crashed = errors.ProcessorCrashedError("worker 退出了", exit_code=7)
    assert crashed.exit_code == 7

    invoke_error = errors.ProcessorInvokeError("调用失败", error_type="ValueError", traceback_text="Traceback ...")
    assert invoke_error.error_type == "ValueError"
    assert invoke_error.traceback_text == "Traceback ..."
```

- [ ] **Step 2: 运行确认失败**

Run: `.runtime/python.exe -m pytest backend/tests/test_processor_manager.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'faust_backend.processors'`

- [ ] **Step 3: 创建包与异常层**

`backend/faust_backend/processors/__init__.py`：

```python
"""Processor 机制：把重计算放进受管子进程。

包导入刻意保持轻量（不 import .manager）：worker 子进程会 import
``faust_backend.processors.protocol`` 等子模块，若包导入顺带拉起 manager，
子进程就会初始化 faust logger 的 root handler（同一个日志文件 + WS 队列）
与 psutil。manager 相关名字通过 PEP 562 ``__getattr__`` 惰性导出。
"""

from __future__ import annotations

from .base import Processor, ProcessorContext
from .errors import (
    ProcessorConfigMismatchError,
    ProcessorCrashedError,
    ProcessorError,
    ProcessorFrameError,
    ProcessorInvokeError,
    ProcessorLeaseError,
    ProcessorNotFoundError,
    ProcessorNotReadyError,
    ProcessorStartError,
    ProcessorTimeoutError,
)
from .registry import processor, register_processor

__all__ = [
    "Processor",
    "ProcessorContext",
    "processor",
    "register_processor",
    "ProcessorError",
    "ProcessorNotFoundError",
    "ProcessorStartError",
    "ProcessorNotReadyError",
    "ProcessorTimeoutError",
    "ProcessorCrashedError",
    "ProcessorConfigMismatchError",
    "ProcessorInvokeError",
    "ProcessorLeaseError",
    "ProcessorFrameError",
    "ProcessorManager",
    "ProcessorHandle",
    "ProcessorLease",
    "PruneReport",
    "get_processor_manager",
]

_LAZY_MANAGER_NAMES = frozenset(
    {"ProcessorManager", "ProcessorHandle", "ProcessorLease", "PruneReport", "get_processor_manager"}
)


def __getattr__(name: str):
    if name in _LAZY_MANAGER_NAMES:
        from . import manager

        return getattr(manager, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

`backend/faust_backend/processors/errors.py`：

```python
"""Processor 机制异常层级。"""

from __future__ import annotations

from typing import Any


class ProcessorError(Exception):
    """Processor 机制异常基类。"""


class ProcessorNotFoundError(ProcessorError):
    """名字未在注册表中登记。"""

    def __init__(self, name: str) -> None:
        super().__init__(f"Processor 未注册: {name}")
        self.name = name


class ProcessorStartError(ProcessorError):
    """setup/start 失败、父进程握手失败，或等待就绪期间 worker 崩溃。"""

    def __init__(
        self,
        message: str,
        *,
        last_error: str | None = None,
        logs: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.last_error = last_error
        self.logs = list(logs or [])


class ProcessorNotReadyError(ProcessorError):
    """非 ACTIVE 状态下调用 invoke。"""


class ProcessorTimeoutError(ProcessorError):
    """setup/start/stop/invoke 超时。"""

    def __init__(self, message: str, *, phase: str | None = None) -> None:
        super().__init__(message)
        self.phase = phase


class ProcessorCrashedError(ProcessorError):
    """worker 意外退出导致在途/排队请求失败。"""

    def __init__(self, message: str, *, exit_code: int | None = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class ProcessorConfigMismatchError(ProcessorError):
    """Processor 已 ACTIVE 且仍被他人引用时，新请求的 config 与当前不一致。"""


class ProcessorInvokeError(ProcessorError):
    """Processor.invoke 在子进程内抛错。"""

    def __init__(self, message: str, *, error_type: str = "", traceback_text: str = "") -> None:
        super().__init__(message)
        self.error_type = error_type
        self.traceback_text = traceback_text


class ProcessorLeaseError(ProcessorError):
    """lease 重复 release / 未 acquire 就使用 / endRequire 无匹配 lease。"""


class ProcessorFrameError(ProcessorError):
    """控制通道帧超限或解码失败。"""
```

- [ ] **Step 4: 运行确认通过**

Run: `.runtime/python.exe -m pytest backend/tests/test_processor_manager.py -q`
Expected: PASS（1 passed）。

注意：此时 `__init__.py` 里 `from .base import ...` 与 `from .registry import ...` 还指向不存在的模块（Task 3、4 才创建）。若本步报 `ModuleNotFoundError: faust_backend.processors.base`，先只保留 `errors` 相关导入，Task 4 结束时把这四行补回。**不要**为绕开而留下 `try/except ImportError`。

- [ ] **Step 5: Commit**

```bash
git add backend/faust_backend/processors/__init__.py backend/faust_backend/processors/errors.py backend/tests/test_processor_manager.py
git commit -m "feat: Processor 机制包骨架与异常层级"
```

---

### Task 2: 控制通道帧协议

**Files:**
- Create: `backend/faust_backend/processors/protocol.py`
- Test: `backend/tests/test_processor_manager.py`（追加）

**Interfaces:**
- Produces: 消息 dataclass —— 下行 `StartOp(run_setup: bool, config: dict, data_dir: str)`、`InvokeOp(req_id: int, data: Any)`、`StopOp()`；上行 `Hello(token: str, pid: int, name: str)`、`Phase(phase: str)`、`Started(setup_ran: bool, fingerprint: str)`、`StartFailed(error: ErrorInfo)`、`Result(req_id: int, ok: bool, value: Any = None, error: ErrorInfo | None = None)`、`LogMsg(ts: float, level: str, message: str, source: str)`、`ErrorInfo(type: str, message: str, traceback: str = "")` + `ErrorInfo.from_exception(exc)`。
- Produces: `MAX_FRAME_BYTES: int`、`pack_frame(obj) -> bytes`、`async send_frame(writer, obj) -> None`、`async recv_frame(reader) -> Any`。
- Consumes: Task 1 的 `ProcessorFrameError`。

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_processor_manager.py`：

```python
# ── Task 2: 帧协议 ──────────────────────────────────────────


def test_frame_roundtrip_over_tcp():
    from faust_backend.processors import protocol as proto

    async def _roundtrip() -> object:
        received: list[object] = []
        done = asyncio.Event()

        async def _handler(reader, writer):
            received.append(await proto.recv_frame(reader))
            writer.close()
            done.set()

        server = await asyncio.start_server(_handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        _reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await proto.send_frame(writer, proto.InvokeOp(req_id=7, data={"audio": b"\x00\x01", "n": 3}))
        await asyncio.wait_for(done.wait(), 5)
        writer.close()
        server.close()
        await server.wait_closed()
        return received[0]

    got = asyncio.run(_roundtrip())
    assert isinstance(got, proto.InvokeOp)
    assert got.req_id == 7
    assert got.data == {"audio": b"\x00\x01", "n": 3}


def test_frame_size_limit_is_enforced(monkeypatch):
    from faust_backend.processors import protocol as proto
    from faust_backend.processors.errors import ProcessorFrameError

    monkeypatch.setattr(proto, "MAX_FRAME_BYTES", 16)
    with pytest.raises(ProcessorFrameError):
        proto.pack_frame(b"x" * 64)


def test_error_info_from_exception_keeps_type_and_traceback():
    from faust_backend.processors.protocol import ErrorInfo

    try:
        raise ValueError("坏输入")
    except ValueError as exc:
        info = ErrorInfo.from_exception(exc)

    assert info.type == "ValueError"
    assert info.message == "坏输入"
    assert "ValueError: 坏输入" in info.traceback
```

- [ ] **Step 2: 运行确认失败**

Run: `.runtime/python.exe -m pytest backend/tests/test_processor_manager.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'faust_backend.processors.protocol'`

- [ ] **Step 3: 实现协议**

`backend/faust_backend/processors/protocol.py`：

```python
"""Processor 父子进程控制通道的帧协议。

承载：loopback TCP，帧格式 = 4 字节大端长度 + pickle。
独立于 stdio 的原因：torch/easyocr 会往 stdout 打印，stdio 专供日志行。
"""

from __future__ import annotations

import asyncio
import pickle
import struct
import traceback as _traceback
from dataclasses import dataclass, field
from typing import Any

from .errors import ProcessorFrameError

#: 单帧上限。4K 截图的 uint8 数组约 25 MB，留足余量。
MAX_FRAME_BYTES = 256 * 1024 * 1024

_HEADER = struct.Struct(">I")


# ── 父 -> 子 ──────────────────────────────────────────────


@dataclass
class StartOp:
    """请求启动 worker 侧 Processor。"""

    run_setup: bool
    config: dict = field(default_factory=dict)
    data_dir: str = ""


@dataclass
class InvokeOp:
    """一次计算请求；`req_id` 由父进程单调递增分配。"""

    req_id: int
    data: Any


@dataclass
class StopOp:
    """请求优雅停止。"""


# ── 子 -> 父 ──────────────────────────────────────────────


@dataclass
class Hello:
    """握手首帧。"""

    token: str
    pid: int
    name: str


@dataclass
class Phase:
    """阶段上报：``setting_up`` | ``starting``。"""

    phase: str


@dataclass
class Started:
    """setup/start 全部完成，worker 进入 ACTIVE。"""

    setup_ran: bool
    fingerprint: str


@dataclass
class StartFailed:
    """setup/start 失败，worker 即将退出。"""

    error: "ErrorInfo"


@dataclass
class Result:
    """`InvokeOp` 的应答。"""

    req_id: int
    ok: bool
    value: Any = None
    error: "ErrorInfo | None" = None


@dataclass
class LogMsg:
    """子进程日志。``source`` = ``ctx`` | ``stdout`` | ``stderr``。"""

    ts: float
    level: str
    message: str
    source: str


@dataclass
class ErrorInfo:
    """可跨进程传递的异常快照。"""

    type: str
    message: str
    traceback: str = ""

    @classmethod
    def from_exception(cls, exc: BaseException) -> "ErrorInfo":
        return cls(
            type=type(exc).__name__,
            message=str(exc),
            traceback="".join(_traceback.format_exception(type(exc), exc, exc.__traceback__)),
        )


# ── 编解码 ────────────────────────────────────────────────


def pack_frame(obj: Any) -> bytes:
    """把消息序列化为一帧。超限或序列化失败立即报错（不静默截断）。"""
    try:
        payload = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as exc:  # noqa: BLE001 - 序列化失败必须暴露
        raise ProcessorFrameError(f"帧序列化失败: {exc}") from exc
    if len(payload) > MAX_FRAME_BYTES:
        raise ProcessorFrameError(f"帧过大: {len(payload)} 字节 > {MAX_FRAME_BYTES}")
    return _HEADER.pack(len(payload)) + payload


async def send_frame(writer: asyncio.StreamWriter, obj: Any) -> None:
    """写出一帧并 flush。"""
    writer.write(pack_frame(obj))
    await writer.drain()


async def recv_frame(reader: asyncio.StreamReader) -> Any:
    """读入一帧。连接断开抛 ``asyncio.IncompleteReadError``。"""
    header = await reader.readexactly(_HEADER.size)
    (size,) = _HEADER.unpack(header)
    if size > MAX_FRAME_BYTES:
        raise ProcessorFrameError(f"收到超限帧: 声明长度 {size} > {MAX_FRAME_BYTES}")
    payload = await reader.readexactly(size)
    try:
        return pickle.loads(payload)
    except Exception as exc:  # noqa: BLE001 - 解码失败必须暴露
        raise ProcessorFrameError(f"帧反序列化失败: {exc}") from exc
```

- [ ] **Step 4: 运行确认通过**

Run: `.runtime/python.exe -m pytest backend/tests/test_processor_manager.py -q`
Expected: PASS（4 passed）。

- [ ] **Step 5: Commit**

```bash
git add backend/faust_backend/processors/protocol.py backend/tests/test_processor_manager.py
git commit -m "feat: Processor 控制通道帧协议"
```

---

### Task 3: Processor 注册表

**Files:**
- Create: `backend/faust_backend/processors/registry.py`
- Test: `backend/tests/test_processor_manager.py`（追加）

**Interfaces:**
- Produces: `RegisteredProcessor(name, cls, owner, loader, source_file)`（frozen dataclass）、`register_processor(cls, name=None, *, owner="builtin", loader=None, source_file=None) -> str`、`@processor(name)`、`get_processor(name) -> RegisteredProcessor`（未注册抛 `ProcessorNotFoundError`）、`list_processors() -> list[RegisteredProcessor]`、`registered_names() -> list[str]`、`unregister_owner(owner) -> list[str]`。
- Consumes: Task 1 的异常、Task 4 的 `Processor` 基类（本 Task 需要它做类型与「未实现 start/invoke」校验，因此**先写 Task 4 的 `base.py` 再跑本 Task 的测试**；执行顺序上把 Task 3/4 视为一体：先建 `base.py`，再建 `registry.py`）。

- [ ] **Step 1: 先建基类（Task 4 的产物，此处先落文件）**

按 Task 4 Step 3 的代码创建 `backend/faust_backend/processors/base.py`。本 Task 的测试依赖 `Processor` 存在。

- [ ] **Step 2: 写失败测试**

追加到 `backend/tests/test_processor_manager.py`：

```python
# ── Task 3: 注册表 ──────────────────────────────────────────


def test_registry_rejects_incomplete_and_duplicate_processors():
    from faust_backend.processors.base import Processor
    from faust_backend.processors.errors import ProcessorError, ProcessorNotFoundError
    from faust_backend.processors import registry

    class Incomplete(Processor):
        NAME = "TEST_INCOMPLETE_REG"

    with pytest.raises(ProcessorError):
        registry.register_processor(Incomplete, owner="test_tmp_registry")

    class Ok(Processor):
        NAME = "TEST_OK_REG"

        def start(self, ctx):
            pass

        def invoke(self, ctx, data):
            return data

    name = registry.register_processor(Ok, owner="test_tmp_registry", loader="/tmp/fake_plugin.py")
    assert name == "TEST_OK_REG"
    entry = registry.get_processor(name)
    assert entry.owner == "test_tmp_registry"
    assert entry.loader == "/tmp/fake_plugin.py"
    assert entry.cls is Ok

    with pytest.raises(ProcessorError):
        registry.register_processor(Ok, owner="test_tmp_registry", loader="/tmp/fake_plugin.py")

    assert registry.unregister_owner("test_tmp_registry") == [name]
    with pytest.raises(ProcessorNotFoundError):
        registry.get_processor(name)
```

- [ ] **Step 3: 实现注册表**

`backend/faust_backend/processors/registry.py`：

```python
"""Processor 类注册表（父/子进程共用）。

父进程登记类名、owner 与来源模块；子进程按 ``loader`` 导入模块后重新登记，
再按名字取出唯一的 Processor 实例。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from .base import Processor
from .errors import ProcessorError, ProcessorNotFoundError

_PROC = TypeVar("_PROC", bound=type[Processor])


@dataclass(frozen=True)
class RegisteredProcessor:
    """一条注册记录。"""

    name: str
    cls: type[Processor]
    owner: str  # "builtin" 或 plugin_id
    loader: str  # 子进程加载用：模块名（builtin）或 .py 绝对路径（插件）
    source_file: str  # 类所在模块的文件路径


_REGISTRY: dict[str, RegisteredProcessor] = {}


def _module_file(cls: type) -> str:
    module = sys.modules.get(getattr(cls, "__module__", "") or "")
    path = getattr(module, "__file__", None)
    return str(Path(path).resolve()) if path else ""


def register_processor(
    cls: _PROC,
    name: str | None = None,
    *,
    owner: str = "builtin",
    loader: str | None = None,
    source_file: str | None = None,
) -> str:
    """登记一个 Processor 类，返回最终名字。名字重复/类不合法立即报错。"""
    if not (isinstance(cls, type) and issubclass(cls, Processor)):
        raise ProcessorError(f"{cls!r} 不是 Processor 子类")

    proc_name = str(name or getattr(cls, "NAME", "") or "").strip()
    if not proc_name:
        raise ProcessorError(f"{cls.__name__} 未提供 Processor 名称（NAME 为空且未显式指定）")
    if cls.start is Processor.start:
        raise ProcessorError(f"{cls.__name__} 未实现 start()")
    if cls.invoke is Processor.invoke:
        raise ProcessorError(f"{cls.__name__} 未实现 invoke()")

    existing = _REGISTRY.get(proc_name)
    if existing is not None:
        raise ProcessorError(
            f"Processor 名字冲突: {proc_name} 已由 {existing.owner} ({existing.cls.__name__}) 注册"
        )

    file_path = str(source_file or _module_file(cls))
    resolved_loader = str(loader or (cls.__module__ if owner == "builtin" else file_path))

    _REGISTRY[proc_name] = RegisteredProcessor(
        name=proc_name,
        cls=cls,
        owner=str(owner),
        loader=resolved_loader,
        source_file=file_path,
    )
    return proc_name


def processor(name: str | None = None):
    """装饰器形式：``@processor("VAD")``。"""

    def _decorator(cls: _PROC) -> _PROC:
        register_processor(cls, name or getattr(cls, "NAME", "") or cls.__name__)
        return cls

    return _decorator


def get_processor(name: str) -> RegisteredProcessor:
    """取注册记录；未注册抛 ``ProcessorNotFoundError``。"""
    entry = _REGISTRY.get(str(name))
    if entry is None:
        raise ProcessorNotFoundError(str(name))
    return entry


def list_processors() -> list[RegisteredProcessor]:
    """全部注册记录（按名字排序）。"""
    return [_REGISTRY[key] for key in sorted(_REGISTRY)]


def registered_names() -> list[str]:
    """全部已注册名字（按名字排序）。"""
    return sorted(_REGISTRY)


def unregister_owner(owner: str) -> list[str]:
    """摘除某 owner 名下的全部注册，返回被摘除的名字。"""
    removed = [name for name, entry in _REGISTRY.items() if entry.owner == owner]
    for name in removed:
        _REGISTRY.pop(name, None)
    return sorted(removed)
```

- [ ] **Step 4: 运行确认通过**

Run: `.runtime/python.exe -m pytest backend/tests/test_processor_manager.py -q`
Expected: PASS（5 passed）。

- [ ] **Step 5: Commit**

```bash
git add backend/faust_backend/processors/registry.py backend/tests/test_processor_manager.py
git commit -m "feat: Processor 注册表与 @processor 装饰器"
```

---

### Task 4: Processor 基类与 Context

**Files:**
- Create: `backend/faust_backend/processors/base.py`（Task 3 Step 1 已落文件，本 Task 补齐测试与 `__init__.py` 导出）
- Modify: `backend/faust_backend/processors/__init__.py`（补 `base` / `registry` 导入，若 Task 1 Step 4 曾临时省略）
- Test: `backend/tests/test_processor_manager.py`（追加）

**Interfaces:**
- Produces:
  - `resolve_data_dir(name: str, override: str | Path | None) -> Path`：`override` 非空时用覆盖值，否则 `Path(conf.DATA_ROOT) / "processors" / name`（读模块属性，便于测试 monkeypatch）。
  - `read_setup_marker(data_dir) -> dict`、`write_setup_marker(data_dir, fingerprint) -> None`；marker 文件 `data_dir/setup.json`，内容 `{"fingerprint": str, "completed_at": iso}`；文件缺失或解析失败返回 `{}`。
  - `ProcessorContext(name, data_dir, config, log_sink)`，成员 `name` / `data_dir` / `config` / `log(message, level="INFO")`（非 str 用 `str()` 化，level 大写）/ `read_marker()` / `write_marker(fingerprint)`。
  - `Processor`：类属性 `NAME`、`DATA_DIR`、`SETUP_VERSION="1"`、`SETUP_TIMEOUT=1800.0`、`START_TIMEOUT=300.0`、`STOP_TIMEOUT=15.0`、`INVOKE_TIMEOUT=None`、`LOG_BUFFER=500`；方法 `setup(ctx)`（空实现）、`start(ctx)`（抛 `NotImplementedError`，注册时即被拦下）、`invoke(ctx, data)`（同）、`stop(ctx)`（空实现）、`setup_fingerprint(config) -> str`（默认返回 `SETUP_VERSION`）。
- Consumes: Task 1 的异常层级。

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_processor_manager.py`：

```python
# ── Task 4: 基类与 Context ──────────────────────────────────


def test_resolve_data_dir_default_and_override(tmp_path, monkeypatch):
    import faust_backend.config_loader as conf
    from faust_backend.processors.base import resolve_data_dir

    monkeypatch.setattr(conf, "DATA_ROOT", str(tmp_path / "data"))
    assert resolve_data_dir("DEMO", None) == (tmp_path / "data" / "processors" / "DEMO")
    assert resolve_data_dir("DEMO", str(tmp_path / "legacy")) == (tmp_path / "legacy")


def test_setup_marker_roundtrip_and_broken_file(tmp_path):
    from faust_backend.processors.base import read_setup_marker, write_setup_marker

    assert read_setup_marker(tmp_path) == {}
    write_setup_marker(tmp_path, "1:ch_sim|en")
    marker = read_setup_marker(tmp_path)
    assert marker["fingerprint"] == "1:ch_sim|en"
    assert marker["completed_at"]

    (tmp_path / "setup.json").write_text("{ 不是 json", encoding="utf-8")
    assert read_setup_marker(tmp_path) == {}


def test_context_log_stringifies_and_uppercases_level(tmp_path):
    from faust_backend.processors.base import ProcessorContext

    seen: list[tuple[str, str]] = []
    ctx = ProcessorContext(
        name="DEMO",
        data_dir=tmp_path,
        config={"a": 1},
        log_sink=lambda level, message: seen.append((level, message)),
    )
    ctx.log(123)
    ctx.log("boom", level="warning")
    assert seen == [("INFO", "123"), ("WARNING", "boom")]
    assert ctx.config == {"a": 1}


def test_processor_defaults_and_fingerprint():
    from faust_backend.processors.base import Processor

    class Demo(Processor):
        NAME = "TEST_DEFAULTS"
        SETUP_VERSION = "7"

        def start(self, ctx):
            pass

        def invoke(self, ctx, data):
            return data

    demo = Demo()
    assert demo.setup_fingerprint({"any": "config"}) == "7"
    assert Demo.INVOKE_TIMEOUT is None
    assert Demo.LOG_BUFFER == 500
    assert Demo.DATA_DIR is None
    assert Processor.start is Demo.start or Demo.start is not Processor.start
```

- [ ] **Step 2: 运行确认失败**

Run: `.runtime/python.exe -m pytest backend/tests/test_processor_manager.py -q`
Expected: 若 `base.py` 尚未创建 → FAIL `ModuleNotFoundError`；已创建 → `resolve_data_dir` 等缺失导致 FAIL。

- [ ] **Step 3: 实现基类**

`backend/faust_backend/processors/base.py`：

```python
"""Processor 基类与子进程内的运行时上下文。"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any, Callable, ClassVar

import faust_backend.config_loader as conf

#: marker 文件名（位于 ProcessorData 目录内）
SETUP_MARKER_NAME = "setup.json"


def resolve_data_dir(name: str, override: str | Path | None) -> Path:
    """解析 ProcessorData 目录。

    ``override`` 非空（类属性 ``DATA_DIR``）时按覆盖值使用——迁移已有数据的
    Processor（VAD 的 torch hub 目录）靠它把数据留在原处；否则默认
    ``<DATA_ROOT>/processors/<NAME>``。
    """
    if override:
        return Path(override).expanduser().resolve()
    return (Path(conf.DATA_ROOT) / "processors" / str(name)).resolve()


def read_setup_marker(data_dir: str | Path) -> dict[str, Any]:
    """读取 setup marker；缺失或损坏返回空 dict（调用方据此重跑 setup）。"""
    path = Path(data_dir) / SETUP_MARKER_NAME
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_setup_marker(data_dir: str | Path, fingerprint: str) -> None:
    """写入 setup marker（目录不存在时创建）。"""
    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "fingerprint": str(fingerprint),
        "completed_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    with (directory / SETUP_MARKER_NAME).open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


class ProcessorContext:
    """Processor 钩子在子进程内拿到的上下文。

    ``log_sink`` 由 worker 注入（把日志变成 ``LogMsg`` 帧），因此钩子里调用
    ``ctx.log`` 不会经过子进程自己的 logging/文件 handler。
    """

    def __init__(
        self,
        name: str,
        data_dir: str | Path,
        config: dict[str, Any],
        log_sink: Callable[[str, str], None],
    ) -> None:
        self.name = str(name)
        self.data_dir = Path(data_dir)
        self.config = dict(config or {})
        self._log_sink = log_sink

    def log(self, message: Any, level: str = "INFO") -> None:
        """上报一条日志（任意可序列化对象，非 str 用 ``str()`` 化）。"""
        text = message if isinstance(message, str) else str(message)
        self._log_sink(str(level).upper(), text)

    def read_marker(self) -> dict[str, Any]:
        """读 setup marker。"""
        return read_setup_marker(self.data_dir)

    def write_marker(self, fingerprint: str) -> None:
        """写 setup marker。"""
        write_setup_marker(self.data_dir, fingerprint)


class Processor:
    """一类计算任务的类定义（名字唯一）。"""

    #: 全局唯一的 Processor 名（如 "VAD" / "OCR"）
    NAME: ClassVar[str] = ""
    #: ProcessorData 目录覆盖值；None = <DATA_ROOT>/processors/<NAME>
    DATA_DIR: ClassVar[str | Path | None] = None
    #: 需要重跑 setup 时 bump
    SETUP_VERSION: ClassVar[str] = "1"
    SETUP_TIMEOUT: ClassVar[float] = 1800.0
    START_TIMEOUT: ClassVar[float] = 300.0
    STOP_TIMEOUT: ClassVar[float] = 15.0
    #: None = 不限时
    INVOKE_TIMEOUT: ClassVar[float | None] = None
    #: 父进程日志环形缓冲容量
    LOG_BUFFER: ClassVar[int] = 500

    def setup(self, ctx: ProcessorContext) -> None:
        """一次性耗时初始化（例如下载模型）。默认无操作。"""

    def start(self, ctx: ProcessorContext) -> None:
        """每次进程启动的初始化（例如把模型加载进内存）。必须实现。"""
        raise NotImplementedError

    def invoke(self, ctx: ProcessorContext, data: Any) -> Any:
        """一次计算。必须实现。"""
        raise NotImplementedError

    def stop(self, ctx: ProcessorContext) -> None:
        """释放资源。默认无操作。"""

    def setup_fingerprint(self, config: dict[str, Any]) -> str:
        """setup 是否可跳过的指纹；config 变化会影响模型文件时需覆写。"""
        return str(self.SETUP_VERSION)
```

- [ ] **Step 4: 补齐 `processors/__init__.py` 导出**

确认 `backend/faust_backend/processors/__init__.py` 顶部四行静态导入齐备（`from .base import Processor, ProcessorContext`、`from .registry import processor, register_processor`、`from .errors import ...`）。

Run: `.runtime/python.exe -c "import sys; sys.path.insert(0, 'backend'); import faust_backend.processors as p; print(p.Processor, p.processor)"`（cwd=仓库根）
Expected: 打印 `<class 'faust_backend.processors.base.Processor'>` 与装饰器函数，无异常。

- [ ] **Step 5: 运行确认通过**

Run: `.runtime/python.exe -m pytest backend/tests/test_processor_manager.py -q`
Expected: PASS（9 passed）。

- [ ] **Step 6: Commit**

```bash
git add backend/faust_backend/processors/base.py backend/faust_backend/processors/__init__.py backend/tests/test_processor_manager.py
git commit -m "feat: Processor 基类、Context 与 ProcessorData 解析"
```

---

### Task 5: worker 子进程 + ProcessorManager 最小闭环

本 Task 是「能跑起来」的垂直切片：启动 → 状态机 → 调用 → 停止 → 日志。worker 单独不可测（没有父进程就没有通道），因此与 manager 一起交付。

**Files:**
- Create: `backend/faust_backend/processors/worker.py`
- Create: `backend/faust_backend/processors/manager.py`
- Test: `backend/tests/test_processor_manager.py`（追加：fake Processor + fixture + 闭环测试）

**Interfaces:**
- Produces（manager 侧，后续 Task 在此之上扩展）:
  - 常量 `STATE_STOPPED / STATE_SETTING_UP / STATE_STARTING / STATE_ACTIVE / STATE_STOPPING`
  - `ProcessorManager()`（可直接构造；`get_processor_manager()` 返回进程单例）：`handle(name) -> ProcessorHandle`、`require(name, requirer, *, config=None) -> ProcessorLease`、`status() -> list[dict]`、`await stop(name) -> None`、`await shutdown() -> None`
  - `ProcessorLease(name, requirer, handle, config)`：`release()`、`__aenter__/__aexit__`、`await wait_until_ready(timeout=None)`、`await invoke(data, *, timeout=None)`、`await get_log(level=None, limit=None)`
  - `ProcessorHandle`：`state`、`phase`、`pid`、`pid_alive`、`last_error`、`config`、`refcount`、`holders`、`invokes_total`、`status()`、`await get_log(level=None, limit=None)`、`await stop(*, reason="manual")`
  - worker 入口：`python <repo>/backend/faust_backend/processors/worker.py --name --host --port --token --parent-pid --log-level --registry-module`（脚本方式启动，worker.py 自己 bootstrap `sys.path`）
- Consumes: Task 1-4 的异常、协议、注册表、基类。

- [ ] **Step 1: 实现 worker 子进程入口**

`backend/faust_backend/processors/worker.py`：

```python
"""Processor worker 子进程入口。

只由 ``ProcessorHandle`` 以**脚本路径**拉起（``python <repo>/backend/faust_backend/processors/worker.py``），
不要手工调用。职责：连回父进程 → 握手 → 按需 setup → start → 串行处理
``InvokeOp`` → 收到 ``StopOp``/EOF 后 stop 并退出。

同步钩子（setup/start/invoke/stop）统一放进 ``asyncio.to_thread``，保证日志
发送与父进程指令读取在钩子执行期间仍然通畅。
"""

from __future__ import annotations

import os
import sys

if __package__ in {None, ""}:
    # 以脚本方式启动（ProcessorManager 就是这么拉起的）。.runtime 内嵌解释器带有
    # python311._pth：它会忽略 PYTHONPATH，也不把 cwd/脚本目录放进 sys.path，
    # 因此这里手工把 backend/ 加进去，再按包名重新导入自己（写法对齐 backend/main.py）。
    _BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _BACKEND_ROOT not in sys.path:
        sys.path.insert(0, _BACKEND_ROOT)
    from faust_backend.processors.worker import main as _main

    raise SystemExit(_main())

import argparse
import asyncio
import importlib
import importlib.util
import logging
import time
from pathlib import Path
from typing import Any

from types import ModuleType

from . import protocol as proto
from .base import Processor, ProcessorContext, write_setup_marker
from .errors import ProcessorNotFoundError
from .registry import get_processor, register_processor

log = logging.getLogger("faust.processor.worker")


class _Worker:
    """一个 worker 进程只承载一个 Processor 实例。"""

    def __init__(self, *, name: str, processor_cls: type[Processor], host: str, port: int, token: str, parent_pid: int) -> None:
        self.name = name
        self.processor = processor_cls()
        self.host = host
        self.port = port
        self.token = token
        self.parent_pid = parent_pid
        self.ctx: ProcessorContext | None = None
        self.active = False
        self.phase: str | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._log_queue: asyncio.Queue = asyncio.Queue()
        self._log_task: asyncio.Task | None = None

    # ── 日志 ──────────────────────────────────────────────

    def _sink(self, level: str, message: str) -> None:
        """``ctx.log`` 的落点。可能从 to_thread 的工作线程调用。"""
        loop = self._loop
        message_obj = proto.LogMsg(ts=time.time(), level=str(level).upper(), message=str(message), source="ctx")
        loop.call_soon_threadsafe(self._log_queue.put_nowait, message_obj)

    async def _log_writer(self) -> None:
        while True:
            msg = await self._log_queue.get()
            if msg is None:
                return
            await self._send(msg)

    async def _send(self, obj: Any) -> None:
        if self._writer is None:
            return
        await proto.send_frame(self._writer, obj)

    # ── 生命周期 ──────────────────────────────────────────

    def _make_ctx(self, op: proto.StartOp) -> ProcessorContext:
        data_dir = Path(op.data_dir).resolve() if op.data_dir else Path.cwd()
        data_dir.mkdir(parents=True, exist_ok=True)
        return ProcessorContext(name=self.name, data_dir=data_dir, config=op.config, log_sink=self._sink)

    async def _handle_start(self, op: proto.StartOp) -> None:
        ctx = self._make_ctx(op)
        self.ctx = ctx
        processor = self.processor
        if op.run_setup:
            self.phase = "setting_up"
            await self._send(proto.Phase(phase=self.phase))
            try:
                await asyncio.to_thread(processor.setup, ctx)
            except Exception as exc:  # noqa: BLE001 - 失败必须回到父进程
                await self._send(proto.StartFailed(error=proto.ErrorInfo.from_exception(exc)))
                return
            write_setup_marker(ctx.data_dir, processor.setup_fingerprint(dict(op.config)))
        self.phase = "starting"
        await self._send(proto.Phase(phase=self.phase))
        try:
            await asyncio.to_thread(processor.start, ctx)
        except Exception as exc:  # noqa: BLE001
            await self._send(proto.StartFailed(error=proto.ErrorInfo.from_exception(exc)))
            return
        self.active = True
        await self._send(
            proto.Started(setup_ran=bool(op.run_setup), fingerprint=processor.setup_fingerprint(dict(op.config)))
        )

    async def _handle_invoke(self, op: proto.InvokeOp) -> None:
        if not self.active or self.ctx is None:
            await self._send(
                proto.Result(
                    req_id=op.req_id,
                    ok=False,
                    error=proto.ErrorInfo(type="ProcessorNotReadyError", message="Processor 尚未 start 完成"),
                )
            )
            return
        try:
            value = await asyncio.to_thread(self.processor.invoke, self.ctx, op.data)
        except Exception as exc:  # noqa: BLE001
            await self._send(proto.Result(req_id=op.req_id, ok=False, error=proto.ErrorInfo.from_exception(exc)))
            return
        await self._send(proto.Result(req_id=op.req_id, ok=True, value=value))

    async def _handle_stop(self) -> None:
        if self.ctx is None:
            return
        try:
            await asyncio.to_thread(self.processor.stop, self.ctx)
        except Exception as exc:  # noqa: BLE001 - stop 失败记日志但不阻断退出
            self._sink("ERROR", f"stop() 失败: {exc}")

    async def _watchdog(self) -> None:
        """父进程消失则立刻自杀，避免遗留孤儿 worker。"""
        import psutil

        while True:
            await asyncio.sleep(3.0)
            if not psutil.pid_exists(self.parent_pid):
                os._exit(3)

    async def run(self) -> int:
        self._loop = asyncio.get_running_loop()
        try:
            self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        except OSError as exc:
            log.critical("无法连接父进程 %s:%s: %s", self.host, self.port, exc)
            return 2
        await self._send(proto.Hello(token=self.token, pid=os.getpid(), name=self.name))
        self._log_task = asyncio.create_task(self._log_writer())
        watchdog = asyncio.create_task(self._watchdog())
        exit_code = 0
        try:
            while True:
                try:
                    frame = await proto.recv_frame(self._reader)
                except asyncio.IncompleteReadError:
                    log.info("父进程关闭了控制通道，worker 退出")
                    break
                except proto.ProcessorFrameError as exc:
                    log.critical("控制帧解码失败，worker 退出: %s", exc)
                    exit_code = 4
                    break
                if isinstance(frame, proto.StartOp):
                    await self._handle_start(frame)
                elif isinstance(frame, proto.InvokeOp):
                    await self._handle_invoke(frame)
                elif isinstance(frame, proto.StopOp):
                    break
                else:
                    log.warning("收到未知帧: %r", frame)
        finally:
            await self._handle_stop()
            watchdog.cancel()
            self._log_queue.put_nowait(None)
            if self._log_task is not None:
                try:
                    await asyncio.wait_for(self._log_task, 2.0)
                except Exception:  # noqa: BLE001 - 退出阶段日志丢失可接受
                    self._log_task.cancel()
            if self._writer is not None:
                self._writer.close()
        return exit_code


def _load_registry_module(spec: str) -> ModuleType:
    """加载注册模块（模块名或 ``.py`` 绝对路径），返回模块对象。"""
    if spec.endswith(".py"):
        path = Path(spec).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"registry module 文件不存在: {path}")
        module_name = f"faust_processor_plugin_{path.stem}"
        module_spec = importlib.util.spec_from_file_location(module_name, str(path))
        if module_spec is None or module_spec.loader is None:
            raise ImportError(f"无法为 {path} 创建 import spec")
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[module_name] = module
        module_spec.loader.exec_module(module)
        return module
    return importlib.import_module(spec)


def _resolve_processor_class(name: str, modules: list[ModuleType]) -> type[Processor]:
    """取本次要承载的 Processor 类。

    内置 Processor 在 import 时由 ``@processor(...)`` 自行注册；插件/测试用的 ``.py``
    模块只在**父进程**里登记（父进程调 ``register_plugin_processors``），子进程里没有
    那段登记代码，因此在刚加载的模块里按 ``NAME`` 找同名子类并就地注册。找不到即报错。
    """
    try:
        return get_processor(name).cls
    except ProcessorNotFoundError:
        pass
    for module in modules:
        for obj in vars(module).values():
            if (
                isinstance(obj, type)
                and issubclass(obj, Processor)
                and obj is not Processor
                and getattr(obj, "NAME", "") == name
            ):
                register_processor(obj, name, owner="plugin")
                return obj
    raise ProcessorNotFoundError(name)


def main(argv: list[str] | None = None) -> int:
    """worker 入口。注册/连接失败一律显式打印并以非零码退出。"""
    parser = argparse.ArgumentParser(description="FaustBot Processor worker")
    parser.add_argument("--name", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--registry-module", action="append", default=[], dest="registry_modules")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )
    try:
        modules = [_load_registry_module(spec) for spec in args.registry_modules]
        processor_cls = _resolve_processor_class(args.name, modules)
    except Exception as exc:  # noqa: BLE001 - 初始化失败必须显式退出
        log.critical("worker 初始化失败: %s", exc, exc_info=True)
        return 2

    worker = _Worker(
        name=args.name,
        processor_cls=processor_cls,
        host=args.host,
        port=args.port,
        token=args.token,
        parent_pid=args.parent_pid,
    )
    try:
        return asyncio.run(worker.run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 实现 ProcessorManager 最小闭环**

`backend/faust_backend/processors/manager.py`：

```python
"""ProcessorManager：主进程侧的引用计数、生命周期与调用入口。"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import sys
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil

import faust_backend.config_loader as conf
from faust_backend.logger import get_logger

from . import protocol as proto
from .base import Processor, read_setup_marker, resolve_data_dir
from .errors import (
    ProcessorConfigMismatchError,
    ProcessorCrashedError,
    ProcessorError,
    ProcessorFrameError,
    ProcessorInvokeError,
    ProcessorLeaseError,
    ProcessorNotFoundError,
    ProcessorNotReadyError,
    ProcessorStartError,
    ProcessorTimeoutError,
)
from .registry import RegisteredProcessor, get_processor, list_processors

log = get_logger("faust.processor")

STATE_STOPPED = "STOPPED"
STATE_SETTING_UP = "SETTING_UP"
STATE_STARTING = "STARTING"
STATE_ACTIVE = "ACTIVE"
STATE_STOPPING = "STOPPING"

#: 父进程等待 worker TCP 连接 / Hello 帧的上限
HANDSHAKE_TIMEOUT = 30.0
#: terminate() 后等待退出的宽限期
KILL_GRACE_SECONDS = 5.0
#: worker 入口脚本（本文件同目录）
WORKER_SCRIPT = Path(__file__).resolve().with_name("worker.py")

_LEVEL_NO = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "WARN": 30, "ERROR": 40, "CRITICAL": 50}
_LEVEL_LINE_RE = re.compile(r"^(ERROR|WARN|WARNING|INFO|DEBUG|CRITICAL)\b[:：]?\s*", re.IGNORECASE)


def _swallow(future: "asyncio.Future[Any]") -> None:
    """丢弃无人 await 的 future 异常，避免 "exception was never retrieved" 噪音。"""
    if not future.cancelled():
        future.exception()


class _InvokeJob:
    """一次待执行/在途的 invoke。"""

    __slots__ = ("req_id", "data", "future")

    def __init__(self, req_id: int, data: Any, future: "asyncio.Future[Any]") -> None:
        self.req_id = req_id
        self.data = data
        self.future = future


class ProcessorLease:
    """一次引用。``requirer`` 持有，支持 ``async with``。"""

    def __init__(self, name: str, requirer: str, handle: "ProcessorHandle", config: dict[str, Any] | None) -> None:
        self.name = str(name)
        self.requirer = str(requirer)
        self.handle = handle
        self.config = dict(config or {})
        self._acquired = False
        self._released = False

    async def acquire(self) -> "ProcessorLease":
        """登记这次引用（触发启动）。重复 acquire / 已 release 后复用会报错。"""
        if self._released or self._acquired:
            raise ProcessorLeaseError(f"{self.name} 的 lease 只能 acquire 一次（requirer={self.requirer}）")
        await self.handle.acquire_lease(self)
        self._acquired = True
        return self

    def release(self) -> None:
        """释放这次引用（幂等报错：重复 release 抛 ``ProcessorLeaseError``）。"""
        if not self._acquired:
            raise ProcessorLeaseError(f"{self.name} 的 lease 尚未 acquire，不能 release（requirer={self.requirer}）")
        if self._released:
            raise ProcessorLeaseError(f"{self.name} 的 lease 已 release（requirer={self.requirer}）")
        self.handle.release_lease(self)
        self._released = True

    async def __aenter__(self) -> "ProcessorLease":
        return await self.acquire()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.release()

    async def wait_until_ready(self, timeout: float | None = None) -> None:
        """等 handle 进入 ACTIVE；启动失败/崩溃抛 ``ProcessorStartError``，超时抛 ``ProcessorTimeoutError``。"""
        self._require_acquired("wait_until_ready")
        await self.handle.wait_until_ready(timeout)

    async def invoke(self, data: Any, *, timeout: float | None = None) -> Any:
        """一次计算调用（FIFO 串行）。"""
        self._require_acquired("invoke")
        return await self.handle.invoke(data, timeout=timeout)

    async def get_log(self, level: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        """读该 Processor 的日志（最新在前）。"""
        return await self.handle.get_log(level=level, limit=limit)

    def _require_acquired(self, action: str) -> None:
        if not self._acquired or self._released:
            raise ProcessorLeaseError(f"{self.name} 的 lease 未持有，不能 {action}（requirer={self.requirer}）")


class ProcessorHandle:
    """某个 Processor 的运行时状态对象（按名字单例，归 ProcessorManager 所有）。"""

    def __init__(self, name: str, entry: RegisteredProcessor) -> None:
        self.name = str(name)
        self.entry = entry
        self.state = STATE_STOPPED
        self.phase: str | None = None
        self.last_error: str | None = None
        self.pid: int | None = None
        self.config: dict[str, Any] = {}
        self.fingerprint: str | None = None
        self.started_at: float | None = None
        self.idle_since: float | None = None
        self.invokes_total = 0
        self.invokes_failed = 0
        self.invokes_cancelled = 0
        self.last_invoke_seconds: float | None = None
        self.leases: list[ProcessorLease] = []
        self.logs: deque[dict[str, Any]] = deque(maxlen=max(1, int(entry.cls.LOG_BUFFER)))

        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._server: asyncio.AbstractServer | None = None
        self._token = ""
        self._connected: "asyncio.Future[bool] | None" = None
        self._start_result: "asyncio.Future[None] | None" = None
        self._start_task: asyncio.Task | None = None
        self._stop_task: asyncio.Task | None = None
        self._cleanup_task: asyncio.Task | None = None
        self._reader_task: asyncio.Task | None = None
        self._stdio_tasks: list[asyncio.Task] = []
        self._pump_task: asyncio.Task | None = None
        self._queue: asyncio.Queue[_InvokeJob] | None = None
        self._pending: dict[int, "asyncio.Future[Any]"] = {}
        self._next_req_id = 1
        self._state_event = asyncio.Event()

    # ── 状态 ──────────────────────────────────────────────

    def _notify(self) -> None:
        self._state_event.set()
        self._state_event = asyncio.Event()

    def _set_state(self, state: str) -> None:
        if self.state == state:
            return
        log.debug("Processor %s: %s -> %s", self.name, self.state, state)
        self.state = state
        self._notify()

    @property
    def refcount(self) -> int:
        """未释放 lease 总数。"""
        return len(self.leases)

    @property
    def holders(self) -> dict[str, int]:
        """按 requirer 分类的引用数。"""
        return dict(Counter(lease.requirer for lease in self.leases))

    @property
    def pid_alive(self) -> bool:
        return bool(self.pid) and psutil.pid_exists(int(self.pid))

    def status(self) -> dict[str, Any]:
        """状态快照（admin 接口与排查用）。"""
        now = time.time()
        return {
            "name": self.name,
            "owner": self.entry.owner,
            "state": self.state,
            "phase": self.phase,
            "pid": self.pid,
            "pid_alive": self.pid_alive,
            "last_error": self.last_error,
            "refcount": self.refcount,
            "holders": self.holders,
            "queue_depth": self._queue.qsize() if self._queue is not None else 0,
            "idle_seconds": (now - self.idle_since) if self.idle_since is not None else None,
            "uptime_seconds": (now - self.started_at) if self.started_at is not None else None,
            "setup_done": self._setup_done(),
            "fingerprint": self.fingerprint,
            "config": dict(self.config),
            "invokes_total": self.invokes_total,
            "invokes_failed": self.invokes_failed,
            "invokes_cancelled": self.invokes_cancelled,
            "last_invoke_seconds": self.last_invoke_seconds,
        }

    async def get_log(self, level: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        """读日志缓冲，最新在前；``level`` 表示最低等级（如 ``"ERROR"``）。"""
        threshold = _LEVEL_NO.get(str(level).upper(), 0) if level else 0
        items = [dict(item) for item in self.logs if int(item["levelno"]) >= threshold]
        items.reverse()
        if limit is not None and int(limit) > 0:
            items = items[: int(limit)]
        return items

    def _record_log(self, level: str, message: str, source: str, *, ts: float | None = None) -> None:
        level_text = str(level or "INFO").upper()
        item = {
            "ts": float(ts if ts is not None else time.time()),
            "level": level_text,
            "levelno": _LEVEL_NO.get(level_text, 20),
            "message": str(message),
            "source": str(source),
        }
        self.logs.append(item)
        get_logger(f"faust.processor.{self.name}").log(item["levelno"], "[%s] %s", item["source"], item["message"])

    def _log_tail(self, count: int = 50) -> list[dict[str, Any]]:
        return list(self.logs)[-count:]

    def _setup_done(self) -> bool:
        try:
            return not self._needs_setup()
        except Exception:  # noqa: BLE001 - 指纹计算失败只影响展示
            return False

    def _fingerprint(self) -> str:
        return str(self.entry.cls().setup_fingerprint(dict(self.config)))

    def _needs_setup(self) -> bool:
        data_dir = resolve_data_dir(self.name, self.entry.cls.DATA_DIR)
        marker = read_setup_marker(data_dir)
        return str(marker.get("fingerprint") or "") != self._fingerprint()

    # ── 启动 ──────────────────────────────────────────────

    def ensure_started(self) -> None:
        """触发启动（同一 handle 上并发 require 复用同一次启动）。"""
        if self.state in (STATE_SETTING_UP, STATE_STARTING, STATE_ACTIVE):
            return
        if self._start_task is not None and not self._start_task.done():
            return
        self._start_task = asyncio.create_task(self._run_start())
        self._start_task.add_done_callback(self._on_start_done)

    def _on_start_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.error("Processor %s 启动失败: %s", self.name, exc)

    async def wait_until_ready(self, timeout: float | None = None) -> None:
        """等 ACTIVE。启动失败/崩溃 → ``ProcessorStartError``；超时 → ``ProcessorTimeoutError``。"""
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + float(timeout)
        while True:
            event = self._state_event
            if self.state == STATE_ACTIVE:
                return
            if self._start_task is not None and not self._start_task.done():
                pass  # 启动进行中，继续等
            elif self.state == STATE_STOPPING:
                pass  # 等停止完成
            else:
                raise ProcessorStartError(
                    f"{self.name} 无法就绪: {self.last_error or self.state}",
                    last_error=self.last_error,
                    logs=self._log_tail(),
                )
            if deadline is None:
                await event.wait()
                continue
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ProcessorTimeoutError(f"等待 {self.name} 就绪超时（{timeout}s）", phase="wait_until_ready")
            try:
                await asyncio.wait_for(event.wait(), remaining)
            except asyncio.TimeoutError:
                raise ProcessorTimeoutError(
                    f"等待 {self.name} 就绪超时（{timeout}s）", phase="wait_until_ready"
                ) from None

    async def _run_start(self) -> None:
        cls = self.entry.cls
        # 上一次崩溃/超时的收尾可能还在跑（会 cancel 后台任务）：先让它结束，
        # 否则新 worker 的 reader task 会被旧的 teardown 取消
        cleanup = self._cleanup_task
        if cleanup is not None and not cleanup.done():
            await asyncio.shield(cleanup)
        self.last_error = None
        self.phase = None
        data_dir = resolve_data_dir(self.name, cls.DATA_DIR)
        self.fingerprint = self._fingerprint()
        run_setup = self._needs_setup()
        try:
            await self._spawn_worker()
            await self._await_start(run_setup, cls, data_dir)
        except ProcessorError as exc:
            self.last_error = str(exc)
            await self._kill_worker()
            raise
        except Exception as exc:  # noqa: BLE001
            message = f"{self.name} 启动失败: {exc}"
            self.last_error = message
            await self._kill_worker()
            raise ProcessorStartError(message, last_error=message, logs=self._log_tail()) from exc
        self.started_at = time.time()
        self.idle_since = None if self.refcount else time.time()
        self._set_state(STATE_ACTIVE)
        log.info("Processor %s 已就绪（pid=%s, setup_ran=%s）", self.name, self.pid, run_setup)

    async def _await_start(self, run_setup: bool, cls: type[Processor], data_dir: Path) -> None:
        loop = asyncio.get_running_loop()
        self._start_result = loop.create_future()
        await self._send(
            proto.StartOp(run_setup=run_setup, config=dict(self.config), data_dir=str(data_dir))
        )
        if run_setup:
            await self._wait_for(
                lambda: self.phase == "starting" or self._start_done(), cls.SETUP_TIMEOUT, phase="setup"
            )
        await self._wait_for(self._start_done, cls.START_TIMEOUT, phase="start")
        assert self._start_result is not None
        exc = self._start_result.exception()
        if exc is not None:
            raise exc

    def _start_done(self) -> bool:
        return self._start_result is not None and self._start_result.done()

    async def _wait_for(self, predicate: Any, timeout: float, *, phase: str) -> None:
        """等条件成立（事件驱动 + 超时）。超时不杀进程，由调用方处理。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(timeout)
        while True:
            event = self._state_event
            if predicate():
                return
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ProcessorTimeoutError(f"{self.name} {phase} 超时（{timeout}s）", phase=phase)
            try:
                await asyncio.wait_for(event.wait(), remaining)
            except asyncio.TimeoutError:
                raise ProcessorTimeoutError(f"{self.name} {phase} 超时（{timeout}s）", phase=phase) from None

    async def _on_worker_connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._connected is None or self._connected.done():
            writer.close()  # 一个 handle 只允许一条控制通道
            return
        self._reader = reader
        self._writer = writer
        self._connected.set_result(True)

    async def _spawn_worker(self) -> None:
        loop = asyncio.get_running_loop()
        self._connected = loop.create_future()
        self._token = secrets.token_hex(16)
        self._server = await asyncio.start_server(self._on_worker_connect, host="127.0.0.1", port=0)
        port = int(self._server.sockets[0].getsockname()[1])

        backend_root = Path(conf.PROJECT_ROOT)
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        # 用「脚本路径」而不是 "-m 包名"：.runtime 是内嵌发行版（有 python311._pth），
        # 它忽略 PYTHONPATH 也不把 cwd/脚本目录放进 sys.path，只有 worker.py 自己
        # bootstrap（见 worker.py 顶部）才能 import faust_backend。
        cmd = [
            sys.executable,
            str(WORKER_SCRIPT),
            "--name",
            self.name,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--token",
            self._token,
            "--parent-pid",
            str(os.getpid()),
            "--log-level",
            "INFO",
            "--registry-module",
            self.entry.loader,
        ]
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(backend_root),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except NotImplementedError as exc:
            await self._close_server()
            raise ProcessorStartError(
                f"{self.name} 无法启动子进程：当前事件循环不支持 subprocess（需要 ProactorEventLoop）"
            ) from exc
        self.pid = self._proc.pid

        try:
            await asyncio.wait_for(self._connected, timeout=HANDSHAKE_TIMEOUT)
        except asyncio.TimeoutError:
            raise ProcessorStartError(
                f"{self.name} 握手超时（{HANDSHAKE_TIMEOUT}s）：{await self._drain_child_output()}",
                last_error="handshake timeout",
                logs=self._log_tail(),
            ) from None
        assert self._reader is not None
        try:
            first = await asyncio.wait_for(proto.recv_frame(self._reader), timeout=HANDSHAKE_TIMEOUT)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError) as exc:
            raise ProcessorStartError(
                f"{self.name} 握手帧读取失败: {exc}\n{await self._drain_child_output()}",
                logs=self._log_tail(),
            ) from exc
        if (
            not isinstance(first, proto.Hello)
            or first.name != self.name
            or not secrets.compare_digest(str(first.token), self._token)
        ):
            raise ProcessorStartError(
                f"{self.name} 握手校验失败：token 或名字不匹配\n{await self._drain_child_output()}",
                logs=self._log_tail(),
            )
        self.pid = int(first.pid)

        await self._close_server()
        self._reader_task = asyncio.create_task(self._reader_loop())
        assert self._proc.stdout is not None and self._proc.stderr is not None
        self._stdio_tasks = [
            asyncio.create_task(self._pipe_loop(self._proc.stdout, "stdout")),
            asyncio.create_task(self._pipe_loop(self._proc.stderr, "stderr")),
        ]
        self._queue = asyncio.Queue()
        self._pump_task = asyncio.create_task(self._pump())

    async def _drain_child_output(self) -> str:
        """握手失败时把子进程已有输出捞回来（此时 stdio 管道还没开始按行采集）。

        先 terminate 再读到 EOF，避免 read() 一直等下去；输出同时写进日志缓冲，
        保证「启动失败必须能看到原因」。
        """
        proc = self._proc
        if proc is None:
            return ""
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=KILL_GRACE_SECONDS)
            except asyncio.TimeoutError:
                proc.kill()
                try:
                    await proc.wait()
                except Exception:  # noqa: BLE001 - 进程已消失
                    pass
        # 按行读到「窗口内没有更多输出」为止：不能等 EOF——子进程可能又派生了进程，
        # 它们会一直hold住管道的写端，read() 会一直挂着。
        loop = asyncio.get_running_loop()
        lines: list[str] = []
        for stream in (proc.stdout, proc.stderr):
            if stream is None:
                continue
            deadline = loop.time() + KILL_GRACE_SECONDS
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    chunk = await asyncio.wait_for(stream.readline(), remaining)
                except Exception:  # noqa: BLE001 - 拿不到输出也要继续报错
                    break
                if not chunk:
                    break
                lines.append(chunk.decode("utf-8", errors="replace").rstrip("\r\n"))
        output = "\n".join(line for line in lines if line.strip())
        for line in output.splitlines():
            self._record_log("ERROR", line, "stderr")
        return output

    async def _close_server(self) -> None:
        server = self._server
        self._server = None
        if server is None:
            return
        server.close()
        try:
            await server.wait_closed()
        except Exception:  # noqa: BLE001 - 关闭 listener 失败不影响后续
            pass

    async def _send(self, obj: Any) -> None:
        writer = self._writer
        if writer is None or writer.is_closing():
            raise ProcessorCrashedError(f"{self.name} 控制通道不可用（worker 未连接）")
        try:
            await proto.send_frame(writer, obj)
        except ProcessorFrameError:
            raise
        except (ConnectionError, OSError) as exc:
            raise ProcessorCrashedError(f"{self.name} 控制通道写入失败: {exc}") from exc

    # ── 控制通道读取 ──────────────────────────────────────

    async def _reader_loop(self) -> None:
        reader = self._reader
        assert reader is not None
        try:
            while True:
                self._dispatch(await proto.recv_frame(reader))
        except asyncio.CancelledError:
            raise
        except asyncio.IncompleteReadError:
            pass
        except ProcessorFrameError as exc:
            self._record_log("ERROR", f"控制通道帧错误: {exc}", "stderr")
        except Exception as exc:  # noqa: BLE001
            if self.state in (STATE_STOPPING, STATE_STOPPED):
                log.debug("Processor %s 控制通道在停止过程中关闭: %s", self.name, exc)
            else:
                self._record_log("ERROR", f"控制通道读取失败: {exc}", "stderr")
        finally:
            if self.state not in (STATE_STOPPING, STATE_STOPPED):
                await self._on_worker_gone()

    def _dispatch(self, frame: Any) -> None:
        if isinstance(frame, proto.Phase):
            self.phase = str(frame.phase)
            self._set_state(STATE_SETTING_UP if self.phase == "setting_up" else STATE_STARTING)
            return
        if isinstance(frame, proto.Started):
            self.fingerprint = str(frame.fingerprint)
            self._resolve_start()
            return
        if isinstance(frame, proto.StartFailed):
            info = frame.error
            self._record_log("ERROR", f"{info.type}: {info.message}", "ctx")
            for line in str(info.traceback or "").rstrip().splitlines():
                self._record_log("ERROR", line, "ctx")
            self._resolve_start(
                ProcessorStartError(
                    f"{self.name} {info.type}: {info.message}",
                    last_error=info.message,
                    logs=self._log_tail(),
                )
            )
            return
        if isinstance(frame, proto.Result):
            pending = self._pending.pop(int(frame.req_id), None)
            if pending is None or pending.done():
                return
            if frame.ok:
                pending.set_result(frame.value)
            else:
                info = frame.error
                error_type = info.type if info is not None else "ProcessorError"
                message = info.message if info is not None else "未知错误"
                pending.set_exception(
                    ProcessorInvokeError(
                        f"{self.name} invoke 失败: {error_type}: {message}",
                        error_type=error_type,
                        traceback_text=(info.traceback if info is not None else ""),
                    )
                )
            return
        if isinstance(frame, proto.LogMsg):
            self._record_log(frame.level, frame.message, frame.source, ts=frame.ts)
            return
        log.warning("Processor %s 收到未知帧: %r", self.name, frame)

    def _resolve_start(self, exc: ProcessorStartError | None = None) -> None:
        future = self._start_result
        if future is not None and not future.done():
            if exc is None:
                future.set_result(None)
            else:
                future.set_exception(exc)
        self._notify()

    async def _pipe_loop(self, stream: asyncio.StreamReader, source: str) -> None:
        try:
            while True:
                raw = await stream.readline()
                if not raw:
                    return
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line.strip():
                    continue
                match = _LEVEL_LINE_RE.match(line)
                self._record_log(match.group(1).upper() if match else "INFO", line, source)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 管道关闭属正常退出路径
            log.debug("Processor %s %s 管道结束: %s", self.name, source, exc)

    # ── invoke ────────────────────────────────────────────

    async def invoke(self, data: Any, *, timeout: float | None = None) -> Any:
        """FIFO 串行调用；非 ACTIVE 立即抛 ``ProcessorNotReadyError``。"""
        if self.state != STATE_ACTIVE:
            raise ProcessorNotReadyError(f"{self.name} 当前状态 {self.state}，不能 invoke")
        loop = asyncio.get_running_loop()
        limit = timeout if timeout is not None else self.entry.cls.INVOKE_TIMEOUT
        job = _InvokeJob(req_id=self._next_req_id, data=data, future=loop.create_future())
        self._next_req_id += 1
        job.future.add_done_callback(_swallow)
        self.invokes_total += 1
        self.idle_since = None
        assert self._queue is not None
        self._queue.put_nowait(job)

        started = loop.time()
        try:
            if limit:
                result = await asyncio.wait_for(asyncio.shield(job.future), float(limit))
            else:
                result = await asyncio.shield(job.future)
        except asyncio.TimeoutError:
            self.last_error = f"invoke 超时（{limit}s）"
            log.error("Processor %s %s，回收 worker", self.name, self.last_error)
            await self._kill_worker()
            raise ProcessorTimeoutError(f"{self.name} invoke 超时（{limit}s）", phase="invoke") from None
        except asyncio.CancelledError:
            self.invokes_cancelled += 1
            raise
        return result

    async def _pump(self) -> None:
        """单条 pump：FIFO 依次发送、等结果（并发调用因此严格串行）。"""
        queue = self._queue
        assert queue is not None
        loop = asyncio.get_running_loop()
        while True:
            job = await queue.get()
            if self.state != STATE_ACTIVE or self._writer is None or self._writer.is_closing():
                if not job.future.done():
                    job.future.set_exception(
                        ProcessorNotReadyError(f"{self.name} 当前状态 {self.state}，请求被丢弃")
                    )
                continue
            self._pending[job.req_id] = job.future
            started = loop.time()
            try:
                await self._send(proto.InvokeOp(req_id=job.req_id, data=job.data))
                await asyncio.shield(job.future)
                self.last_invoke_seconds = loop.time() - started
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 失败已经在 future 上抛给调用方
                self.invokes_failed += 1
                log.debug("Processor %s invoke #%d 失败: %s", self.name, job.req_id, exc)
            finally:
                self._pending.pop(job.req_id, None)
                if self.refcount == 0 and queue.empty() and self.state == STATE_ACTIVE:
                    # 空闲计时统一用 time.time()（prune/status 也用它算差值，别混 loop.time()）
                    self.idle_since = time.time()

    # ── 引用计数 ──────────────────────────────────────────

    async def acquire_lease(self, lease: ProcessorLease) -> None:
        """登记一次引用并（必要时）触发启动。"""
        self.leases.append(lease)
        self.idle_since = None
        if not self.config and lease.config:
            self.config = dict(lease.config)
        self.ensure_started()

    def release_lease(self, lease: ProcessorLease) -> None:
        """注销一次引用；无引用且无在途请求时开始计时空闲。"""
        try:
            self.leases.remove(lease)
        except ValueError as exc:
            raise ProcessorLeaseError(f"{self.name} 的 lease 不属于该 handle") from exc
        if self.refcount == 0 and (self._queue is None or self._queue.empty()) and self.state == STATE_ACTIVE:
            self.idle_since = time.time()

    # ── 停止 ──────────────────────────────────────────────

    async def stop(self, *, reason: str = "manual") -> None:
        """停止 worker（等 StopOp → terminate → kill）。幂等。"""
        if self.state == STATE_STOPPED and self._proc is None:
            return
        if self._stop_task is not None and not self._stop_task.done():
            await asyncio.shield(self._stop_task)
            return
        self._set_state(STATE_STOPPING)
        log.info("停止 Processor %s（原因: %s）", self.name, reason)
        self._stop_task = asyncio.create_task(self._run_stop(reason))
        await asyncio.shield(self._stop_task)

    async def _run_stop(self, reason: str) -> None:
        try:
            proc = self._proc
            if self._writer is not None and not self._writer.is_closing():
                try:
                    await asyncio.wait_for(self._send(proto.StopOp()), timeout=float(self.entry.cls.STOP_TIMEOUT))
                except Exception as exc:  # noqa: BLE001
                    log.warning("Processor %s 发送 StopOp 失败: %s", self.name, exc)
            if proc is not None and proc.returncode is None:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=KILL_GRACE_SECONDS)
                except asyncio.TimeoutError:
                    log.warning("Processor %s 未按时退出，terminate()", self.name)
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=KILL_GRACE_SECONDS)
                    except asyncio.TimeoutError:
                        log.warning("Processor %s 仍未退出，kill()", self.name)
                        proc.kill()
                        await proc.wait()
        finally:
            self._fail_pending(ProcessorCrashedError(f"{self.name} 已停止（{reason}），请求被丢弃"))
            await self._teardown()

    async def _kill_worker(self, *, reason: str | None = None) -> None:
        """异常路径的强制回收：terminate → kill → teardown（reason 会并入 last_error）。"""
        if reason:
            self.last_error = f"{self.last_error}；{reason}" if self.last_error else reason
        proc = self._proc
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=KILL_GRACE_SECONDS)
            except asyncio.TimeoutError:
                proc.kill()
                try:
                    await proc.wait()
                except Exception:  # noqa: BLE001 - 进程已消失
                    pass
            except ProcessLookupError:
                pass
        self._fail_pending(ProcessorCrashedError(f"{self.name} worker 被回收，请求被丢弃"))
        await self._teardown()

    def _fail_pending(self, exc: ProcessorError) -> None:
        pending = list(self._pending.items())
        self._pending.clear()
        for _req_id, future in pending:
            if not future.done():
                future.set_exception(exc)

    async def _teardown(self) -> None:
        """取消后台任务、关通道、清空运行时字段；保留 last_error（供诊断）。"""
        tasks = [task for task in (self._pump_task, self._reader_task, *self._stdio_tasks) if task is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001 - 收尾阶段不因任务异常中断
                log.debug("Processor %s 后台任务收尾异常: %s", self.name, exc)
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:  # noqa: BLE001
                pass
        await self._close_server()
        self._proc = None
        self._reader = None
        self._writer = None
        self._queue = None
        self._pending.clear()
        self._pump_task = None
        self._reader_task = None
        self._stdio_tasks = []
        self._connected = None
        self.pid = None
        self.phase = None
        self.started_at = None
        self.idle_since = None
        self._set_state(STATE_STOPPED)

    async def _on_worker_gone(self) -> None:
        """worker 意外退出：让在途/排队请求失败，等下一次 require 重启。

        连接先断、进程随后被 OS 回收，因此这里先等一小会儿拿到真实退出码，
        再把原因**追加**到已有 last_error 上（例如「invoke 超时（0.7s）；worker exited with code 1」），
        避免覆盖掉真正的根因。
        """
        proc = self._proc
        exit_code: int | None = None
        if proc is not None:
            if proc.returncode is None:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=KILL_GRACE_SECONDS)
                except asyncio.TimeoutError:
                    pass
            exit_code = proc.returncode
        reason = f"worker exited with code {exit_code}" if exit_code is not None else "worker 连接中断"
        log.error("Processor %s %s", self.name, reason)
        self.last_error = f"{self.last_error}；{reason}" if self.last_error else reason
        self._fail_pending(ProcessorCrashedError(f"{self.name} 子进程已退出: {reason}", exit_code=exit_code))
        self._resolve_start(
            ProcessorStartError(
                f"{self.name} 启动期间子进程退出: {reason}",
                last_error=self.last_error,
                logs=self._log_tail(),
            )
        )
        self._set_state(STATE_STOPPED)
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._teardown())


class ProcessorManager:
    """全部 Processor 的注册表入口、状态查询与回收开关。"""

    def __init__(self) -> None:
        self._handles: dict[str, ProcessorHandle] = {}

    def handle(self, name: str) -> ProcessorHandle:
        """取 handle（按需创建）。名字未注册抛 ``ProcessorNotFoundError``。"""
        entry = get_processor(name)
        handle = self._handles.get(entry.name)
        if handle is None:
            handle = ProcessorHandle(entry.name, entry)
            self._handles[entry.name] = handle
        return handle

    def require(self, name: str, requirer: str, *, config: dict[str, Any] | None = None) -> ProcessorLease:
        """返回**未获取**的 lease；``async with`` 进入时 acquire、退出时 release。

        注意：``require()`` 本身不启动进程也不计数——引用计数在 ``acquire``/``async with``
        进入时才 +1，启动也在那一刻触发（避免只是构造 lease 就拉起子进程）。
        """
        handle = self.handle(name)
        return ProcessorLease(handle.name, str(requirer), handle, config)

    def status(self) -> list[dict[str, Any]]:
        """全部已注册 Processor 的状态快照。"""
        return [self.handle(entry.name).status() for entry in list_processors()]

    async def stop(self, name: str) -> None:
        """管理用途：忽略引用计数强制停止（写日志说明）。"""
        handle = self.handle(name)
        if handle.refcount:
            log.warning("强制停止 Processor %s：仍有 %d 个引用 %s", handle.name, handle.refcount, handle.holders)
        await handle.stop(reason="forced by manager.stop()")

    async def shutdown(self) -> None:
        """后端关闭时调用：忽略引用计数停止全部 Processor 并等待回收。"""
        for handle in list(self._handles.values()):
            try:
                await handle.stop(reason="backend shutdown")
            except Exception as exc:  # noqa: BLE001 - 关闭阶段继续处理其它 Processor
                log.error("关闭 Processor %s 失败: %s", handle.name, exc)
        alive = [handle.name for handle in self._handles.values() if handle.pid_alive]
        if alive:
            log.error("后端关闭后仍有残留 worker 进程: %s", alive)
        else:
            log.info("全部 Processor 已回收")


_MANAGER: ProcessorManager | None = None


def get_processor_manager() -> ProcessorManager:
    """进程级单例。"""
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = ProcessorManager()
    return _MANAGER
```

- [ ] **Step 3: 写失败测试（fake Processor + fixture + 闭环）**

追加到 `backend/tests/test_processor_manager.py`：

```python
# ── Task 5: worker + manager 最小闭环 ───────────────────────

from faust_backend.processors.base import Processor, ProcessorContext  # noqa: E402
from faust_backend.processors.registry import register_processor  # noqa: E402


class EchoProcessor(Processor):
    """最小可用 Processor：setup 留痕、invoke 回显。"""

    NAME = "TEST_ECHO"
    SETUP_TIMEOUT = 20.0
    START_TIMEOUT = 20.0
    STOP_TIMEOUT = 5.0

    def setup(self, ctx: ProcessorContext) -> None:
        with (ctx.data_dir / "setup_runs.txt").open("a", encoding="utf-8") as fh:
            fh.write("setup\n")

    def start(self, ctx: ProcessorContext) -> None:
        ctx.log("echo 已 start")
        print("[echo] stdout 也能被采集")

    def invoke(self, ctx: ProcessorContext, data):
        with (ctx.data_dir / "invokes.txt").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(data, ensure_ascii=False) + "\n")
        return {"echo": data}

    def stop(self, ctx: ProcessorContext) -> None:
        ctx.log("echo 已 stop")


class FailSetupProcessor(Processor):
    NAME = "TEST_FAIL_SETUP"
    SETUP_TIMEOUT = 20.0
    START_TIMEOUT = 20.0

    def setup(self, ctx: ProcessorContext) -> None:
        raise ValueError("模型下载失败")

    def start(self, ctx: ProcessorContext) -> None:
        pass

    def invoke(self, ctx: ProcessorContext, data):
        return data


class FailStartProcessor(Processor):
    NAME = "TEST_FAIL_START"
    SETUP_TIMEOUT = 20.0
    START_TIMEOUT = 20.0

    def setup(self, ctx: ProcessorContext) -> None:
        pass

    def start(self, ctx: ProcessorContext) -> None:
        raise RuntimeError("权重文件损坏")

    def invoke(self, ctx: ProcessorContext, data):
        return data


register_processor(EchoProcessor, owner="test")
register_processor(FailSetupProcessor, owner="test")
register_processor(FailStartProcessor, owner="test")


@pytest_asyncio.fixture
async def manager_factory(tmp_path, monkeypatch):
    """每个测试独立的 ProcessorManager；退出时统一 shutdown。"""
    from faust_backend.processors.manager import ProcessorManager

    monkeypatch.setattr(
        EchoProcessor, "DATA_DIR", str(tmp_path / "data" / "TEST_ECHO"), raising=False
    )
    monkeypatch.setattr(
        FailSetupProcessor, "DATA_DIR", str(tmp_path / "data" / "TEST_FAIL_SETUP"), raising=False
    )
    monkeypatch.setattr(
        FailStartProcessor, "DATA_DIR", str(tmp_path / "data" / "TEST_FAIL_START"), raising=False
    )
    managers: list[ProcessorManager] = []

    def _make() -> ProcessorManager:
        manager = ProcessorManager()
        managers.append(manager)
        return manager

    yield _make
    for manager in managers:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_start_reaches_active_and_reports_status(manager_factory):
    manager = manager_factory()
    lease = await manager.require("TEST_ECHO", requirer="t1").acquire()
    await lease.wait_until_ready(timeout=30)

    snapshot = manager.handle("TEST_ECHO").status()
    assert snapshot["state"] == "ACTIVE"
    assert snapshot["owner"] == "test"
    assert snapshot["refcount"] == 1
    assert snapshot["holders"] == {"t1": 1}
    assert snapshot["pid"] and snapshot["pid_alive"] is True
    assert snapshot["setup_done"] is True
    assert snapshot["uptime_seconds"] is not None

    logs = await lease.get_log()
    assert any("echo 已 start" in item["message"] for item in logs)
    lease.release()


@pytest.mark.asyncio
async def test_setup_runs_once_then_marker_skips_it(manager_factory):
    manager = manager_factory()
    lease = await manager.require("TEST_ECHO", requirer="t1").acquire()
    await lease.wait_until_ready(timeout=30)
    lease.release()
    await manager.stop("TEST_ECHO")

    lease = await manager.require("TEST_ECHO", requirer="t1").acquire()
    await lease.wait_until_ready(timeout=30)

    setup_runs = (Path(EchoProcessor.DATA_DIR) / "setup_runs.txt").read_text(encoding="utf-8")
    assert setup_runs.count("setup") == 1
    lease.release()


@pytest.mark.asyncio
async def test_setup_failure_surfaces_start_error_and_logs(manager_factory):
    from faust_backend.processors.errors import ProcessorStartError

    manager = manager_factory()
    lease = await manager.require("TEST_FAIL_SETUP", requirer="t1").acquire()
    with pytest.raises(ProcessorStartError) as excinfo:
        await lease.wait_until_ready(timeout=30)

    assert "模型下载失败" in str(excinfo.value)
    handle = manager.handle("TEST_FAIL_SETUP")
    assert handle.state == "STOPPED"
    assert "模型下载失败" in (handle.last_error or "")
    logs = await handle.get_log(level="ERROR")
    assert any("ValueError" in item["message"] for item in logs)
    lease.release()


@pytest.mark.asyncio
async def test_start_failure_surfaces_start_error(manager_factory):
    from faust_backend.processors.errors import ProcessorStartError

    manager = manager_factory()
    lease = await manager.require("TEST_FAIL_START", requirer="t1").acquire()
    with pytest.raises(ProcessorStartError):
        await lease.wait_until_ready(timeout=30)
    assert manager.handle("TEST_FAIL_START").state == "STOPPED"
    lease.release()


@pytest.mark.asyncio
async def test_invoke_roundtrip_and_stdout_capture(manager_factory):
    manager = manager_factory()
    async with manager.require("TEST_ECHO", requirer="t1") as lease:
        await lease.wait_until_ready(timeout=30)
        assert await lease.invoke({"n": 1}) == {"echo": {"n": 1}}
        logs = await lease.get_log()
        assert any(item["source"] == "stdout" and "stdout 也能被采集" in item["message"] for item in logs)
        assert any(item["message"] == "echo 已 start" for item in logs)


@pytest.mark.asyncio
async def test_invoke_requires_active_state(manager_factory):
    from faust_backend.processors.errors import ProcessorNotReadyError

    manager = manager_factory()
    lease = await manager.require("TEST_ECHO", requirer="t1").acquire()
    await lease.wait_until_ready(timeout=30)
    await manager.stop("TEST_ECHO")
    with pytest.raises(ProcessorNotReadyError):
        await lease.invoke({"n": 1})
    lease.release()


@pytest.mark.asyncio
async def test_shutdown_stops_all_workers(manager_factory):
    manager = manager_factory()
    lease = await manager.require("TEST_ECHO", requirer="t1").acquire()
    await lease.wait_until_ready(timeout=30)
    pid = manager.handle("TEST_ECHO").pid

    await manager.shutdown()

    snapshot = manager.handle("TEST_ECHO").status()
    assert snapshot["state"] == "STOPPED"
    assert snapshot["pid"] is None
    assert not psutil.pid_exists(int(pid))
```

- [ ] **Step 4: 运行确认失败 → 通过**

Run: `.runtime/python.exe -m pytest backend/tests/test_processor_manager.py -q`
Expected（实现前）: FAIL —— `ModuleNotFoundError: No module named 'faust_backend.processors.manager'`
Expected（实现后）: 全部 PASS（16 passed），耗时约 20-60s（每个用例会拉起 1-2 个 python 子进程）。

逐个用例调试时可用 `-k` 缩小范围，例如：
`.runtime/python.exe -m pytest backend/tests/test_processor_manager.py -q -k setup_failure`

- [ ] **Step 5: Commit**

```bash
git add backend/faust_backend/processors/worker.py backend/faust_backend/processors/manager.py backend/tests/test_processor_manager.py
git commit -m "feat: Processor worker 子进程与 ProcessorManager 最小闭环"
```

---

### Task 6: 引用计数 API 完整语义

**Files:**
- Modify: `backend/faust_backend/processors/manager.py`（`ProcessorManager` 增加 `startRequire` / `endRequire`）
- Test: `backend/tests/test_processor_manager.py`（追加）

**Interfaces:**
- Produces: `await ProcessorManager.startRequire(name, requirer, *, config=None) -> ProcessorLease`（立即 acquire）、`await ProcessorManager.endRequire(name, requirer) -> None`（FIFO 释放该 requirer 最早的未释放 lease；无匹配抛 `ProcessorLeaseError`）。
- Consumes: Task 5 的 `require` / `ProcessorLease` / `holders`。

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_processor_manager.py`：

```python
# ── Task 6: 引用计数 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_two_requirers_count_references(manager_factory):
    manager = manager_factory()
    first = await manager.startRequire("TEST_ECHO", requirer="chat")
    second = await manager.startRequire("TEST_ECHO", requirer="chat")
    third = await manager.startRequire("TEST_ECHO", requirer="ui")

    handle = manager.handle("TEST_ECHO")
    assert handle.refcount == 3
    assert handle.holders == {"chat": 2, "ui": 1}
    # startRequire 不等待就绪；三次调用复用同一次启动（同一个 _start_task）
    start_task = handle._start_task
    assert start_task is not None
    await first.wait_until_ready(timeout=30)
    assert handle.state == "ACTIVE"
    assert handle._start_task is start_task

    await manager.endRequire("TEST_ECHO", "chat")
    await manager.endRequire("TEST_ECHO", "chat")
    await manager.endRequire("TEST_ECHO", "ui")
    assert handle.refcount == 0
    assert handle.holders == {}
    assert handle.idle_since is not None


@pytest.mark.asyncio
async def test_end_require_without_match_raises(manager_factory):
    from faust_backend.processors.errors import ProcessorLeaseError

    manager = manager_factory()
    lease = await manager.startRequire("TEST_ECHO", requirer="chat")
    with pytest.raises(ProcessorLeaseError):
        await manager.endRequire("TEST_ECHO", "nobody")
    assert manager.handle("TEST_ECHO").refcount == 1
    lease.release()


@pytest.mark.asyncio
async def test_lease_lifecycle_errors(manager_factory):
    from faust_backend.processors.errors import ProcessorLeaseError

    manager = manager_factory()
    lease = manager.require("TEST_ECHO", requirer="chat")
    with pytest.raises(ProcessorLeaseError):
        lease.release()                      # 未 acquire 就 release

    await lease.acquire()
    with pytest.raises(ProcessorLeaseError):
        await lease.acquire()                # 重复 acquire
    lease.release()
    with pytest.raises(ProcessorLeaseError):
        lease.release()                      # 重复 release
    with pytest.raises(ProcessorLeaseError):
        await lease.invoke({"n": 1})         # 已 release 的 lease 不能再用


@pytest.mark.asyncio
async def test_async_with_releases_reference(manager_factory):
    manager = manager_factory()
    async with manager.require("TEST_ECHO", requirer="vad_ws:1") as lease:
        await lease.wait_until_ready(timeout=30)
        assert manager.handle("TEST_ECHO").holders == {"vad_ws:1": 1}
    handle = manager.handle("TEST_ECHO")
    assert handle.refcount == 0
    assert handle.state == "ACTIVE"          # 引用归零不自动停，交给 prune
```

- [ ] **Step 2: 运行确认失败**

Run: `.runtime/python.exe -m pytest backend/tests/test_processor_manager.py -q -k "requirers or end_require or lease_lifecycle or async_with"`
Expected: FAIL —— `AttributeError: 'ProcessorManager' object has no attribute 'startRequire'` / `endRequire`

- [ ] **Step 3: 实现两个方法**

在 `manager.py` 的 `ProcessorManager.require` 之后插入：

```python
    async def startRequire(
        self, name: str, requirer: str, *, config: dict[str, Any] | None = None
    ) -> ProcessorLease:
        """立即 acquire 并返回已持有的 lease（等价于 ``await require(...).acquire()``）。"""
        lease = self.require(name, requirer, config=config)
        return await lease.acquire()

    async def endRequire(self, name: str, requirer: str) -> None:
        """释放该 requirer 最早的一个未释放 lease；无匹配抛 ``ProcessorLeaseError``。"""
        handle = self.handle(name)
        for lease in list(handle.leases):
            if lease.requirer == str(requirer):
                lease.release()
                return
        raise ProcessorLeaseError(f"{name} 没有属于 {requirer} 的未释放引用")
```

- [ ] **Step 4: 运行确认通过**

Run: `.runtime/python.exe -m pytest backend/tests/test_processor_manager.py -q`
Expected: PASS（20 passed）。

- [ ] **Step 5: Commit**

```bash
git add backend/faust_backend/processors/manager.py backend/tests/test_processor_manager.py
git commit -m "feat: Processor 引用计数 API（startRequire/endRequire）"
```

---

### Task 7: 崩溃、超时、取消与 config 规则

**Files:**
- Modify: `backend/faust_backend/processors/manager.py`（`ProcessorHandle` 增加 `busy`；`acquire_lease` 落实 config 规则）
- Test: `backend/tests/test_processor_manager.py`（追加 4 个 fake Processor + 8 个用例）

**Interfaces:**
- Produces: config 规则（§10）——「`refcount==0` 且 config 不同 → 先 stop 再按新 config 重启」；「仍有其它引用 → `ProcessorConfigMismatchError`」。
- Produces: `ProcessorHandle.busy -> bool`（有在途或排队请求），Task 8 的 prune 使用。

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_processor_manager.py`：

```python
# ── Task 7: 崩溃 / 超时 / 取消 / config ─────────────────────


class BoomProcessor(Processor):
    NAME = "TEST_BOOM"
    SETUP_TIMEOUT = 20.0
    START_TIMEOUT = 20.0

    def setup(self, ctx: ProcessorContext) -> None:
        pass

    def start(self, ctx: ProcessorContext) -> None:
        pass

    def invoke(self, ctx: ProcessorContext, data):
        if data == "boom":
            raise ValueError("输入不合法")
        return {"ok": data}


class CrashProcessor(Processor):
    NAME = "TEST_CRASH"
    SETUP_TIMEOUT = 20.0
    START_TIMEOUT = 20.0

    def setup(self, ctx: ProcessorContext) -> None:
        pass

    def start(self, ctx: ProcessorContext) -> None:
        pass

    def invoke(self, ctx: ProcessorContext, data):
        if data == "die":
            import os

            os._exit(7)
        return {"ok": data}


class SlowProcessor(Processor):
    NAME = "TEST_SLOW"
    SETUP_TIMEOUT = 20.0
    START_TIMEOUT = 20.0
    INVOKE_TIMEOUT = 0.5

    def setup(self, ctx: ProcessorContext) -> None:
        pass

    def start(self, ctx: ProcessorContext) -> None:
        pass

    def invoke(self, ctx: ProcessorContext, data):
        import time

        seq = int(data.get("seq", 0))
        with (ctx.data_dir / "seq.txt").open("a", encoding="utf-8") as fh:
            fh.write(f"start {seq}\n")
        time.sleep(float(data.get("sleep", 0.05)))
        with (ctx.data_dir / "seq.txt").open("a", encoding="utf-8") as fh:
            fh.write(f"end {seq}\n")
        return {"seq": seq}


class SlowPeerProcessor(SlowProcessor):
    """与 TEST_SLOW 同类但独立进程；用于验证跨 Processor 并行。"""

    NAME = "TEST_SLOW_PEER"


class ConfigEchoProcessor(Processor):
    NAME = "TEST_CONFIG_ECHO"
    SETUP_TIMEOUT = 20.0
    START_TIMEOUT = 20.0

    def setup(self, ctx: ProcessorContext) -> None:
        pass

    def start(self, ctx: ProcessorContext) -> None:
        pass

    def invoke(self, ctx: ProcessorContext, data):
        return {"config": dict(ctx.config)}


register_processor(BoomProcessor, owner="test")
register_processor(CrashProcessor, owner="test")
register_processor(SlowProcessor, owner="test")
register_processor(SlowPeerProcessor, owner="test")
register_processor(ConfigEchoProcessor, owner="test")


@pytest.fixture(autouse=True)
def _test_data_dirs(tmp_path, monkeypatch):
    for cls in (BoomProcessor, CrashProcessor, SlowProcessor, SlowPeerProcessor, ConfigEchoProcessor):
        monkeypatch.setattr(cls, "DATA_DIR", str(tmp_path / "data" / cls.NAME), raising=False)


@pytest.mark.asyncio
async def test_invoke_error_keeps_worker_alive(manager_factory):
    from faust_backend.processors.errors import ProcessorInvokeError

    manager = manager_factory()
    async with manager.require("TEST_BOOM", requirer="t1") as lease:
        await lease.wait_until_ready(timeout=30)
        with pytest.raises(ProcessorInvokeError) as excinfo:
            await lease.invoke("boom")
        assert excinfo.value.error_type == "ValueError"
        assert "输入不合法" in excinfo.value.traceback_text
        assert manager.handle("TEST_BOOM").state == "ACTIVE"
        assert await lease.invoke("fine") == {"ok": "fine"}


@pytest.mark.asyncio
async def test_worker_crash_fails_inflight_and_pending_then_restart_works(manager_factory):
    from faust_backend.processors.errors import ProcessorCrashedError, ProcessorNotReadyError

    manager = manager_factory()
    lease = await manager.startRequire("TEST_CRASH", requirer="t1")
    await lease.wait_until_ready(timeout=30)

    inflight = asyncio.create_task(lease.invoke("die"))
    await asyncio.sleep(0.2)
    queued = asyncio.create_task(lease.invoke("later"))

    with pytest.raises(ProcessorCrashedError):
        await inflight
    with pytest.raises((ProcessorCrashedError, ProcessorNotReadyError)):
        await queued

    handle = manager.handle("TEST_CRASH")
    assert handle.state == "STOPPED"
    assert "7" in (handle.last_error or "")
    await asyncio.sleep(0.2)
    assert handle.pid_alive is False

    await manager.endRequire("TEST_CRASH", "t1")
    lease = await manager.startRequire("TEST_CRASH", requirer="t1")   # 崩溃后需重新 require 才重启
    await lease.wait_until_ready(timeout=30)
    assert await lease.invoke("hello") == {"ok": "hello"}
    lease.release()


@pytest.mark.asyncio
async def test_invoke_timeout_recycles_worker(manager_factory):
    from faust_backend.processors.errors import ProcessorTimeoutError

    manager = manager_factory()
    lease = await manager.startRequire("TEST_SLOW", requirer="t1")
    await lease.wait_until_ready(timeout=30)

    with pytest.raises(ProcessorTimeoutError):
        await lease.invoke({"seq": 1, "sleep": 5.0})

    handle = manager.handle("TEST_SLOW")
    assert handle.state == "STOPPED"
    assert "invoke 超时" in (handle.last_error or "")
    await asyncio.sleep(0.2)
    assert handle.pid_alive is False
    lease.release()


@pytest.mark.asyncio
async def test_invoke_is_fifo_and_serial(manager_factory):
    manager = manager_factory()
    async with manager.require("TEST_SLOW", requirer="t1") as lease:
        await lease.wait_until_ready(timeout=30)
        results = await asyncio.gather(*(lease.invoke({"seq": i}) for i in range(1, 6)))
        assert [item["seq"] for item in results] == [1, 2, 3, 4, 5]

        lines = (Path(SlowProcessor.DATA_DIR) / "seq.txt").read_text(encoding="utf-8").split()
        seq_events = list(zip(lines[0::2], lines[1::2]))
        assert seq_events == [("start", "1"), ("end", "1"), ("start", "2"), ("end", "2"),
                              ("start", "3"), ("end", "3"), ("start", "4"), ("end", "4"),
                              ("start", "5"), ("end", "5")]


@pytest.mark.asyncio
async def test_different_processors_run_in_parallel(manager_factory):
    manager = manager_factory()
    async with manager.require("TEST_SLOW", requirer="t1") as left:
        async with manager.require("TEST_SLOW_PEER", requirer="t1") as right:
            await asyncio.gather(left.wait_until_ready(timeout=30), right.wait_until_ready(timeout=30))
            loop = asyncio.get_running_loop()
            started = loop.time()
            await asyncio.gather(
                left.invoke({"seq": 1, "sleep": 0.6}, timeout=5.0),
                right.invoke({"seq": 1, "sleep": 0.6}, timeout=5.0),
            )
            elapsed = loop.time() - started
            assert elapsed < 1.1, f"两个 Processor 应当并行，实际耗时 {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_cancelled_invoke_is_counted(manager_factory):
    manager = manager_factory()
    lease = await manager.startRequire("TEST_SLOW", requirer="t1")
    await lease.wait_until_ready(timeout=30)

    task = asyncio.create_task(lease.invoke({"seq": 1, "sleep": 1.0}))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    handle = manager.handle("TEST_SLOW")
    assert handle.invokes_cancelled == 1
    assert handle.state == "ACTIVE"          # 取消不杀 worker
    lease.release()


@pytest.mark.asyncio
async def test_config_mismatch_raises_when_referenced(manager_factory):
    from faust_backend.processors.errors import ProcessorConfigMismatchError

    manager = manager_factory()
    first = await manager.startRequire("TEST_CONFIG_ECHO", requirer="chat", config={"langs": ["en"]})
    await first.wait_until_ready(timeout=30)
    assert await first.invoke({}) == {"config": {"langs": ["en"]}}

    with pytest.raises(ProcessorConfigMismatchError):
        await manager.startRequire("TEST_CONFIG_ECHO", requirer="other", config={"langs": ["ch_sim"]})

    assert manager.handle("TEST_CONFIG_ECHO").state == "ACTIVE"
    first.release()


@pytest.mark.asyncio
async def test_config_change_restarts_when_unreferenced(manager_factory):
    manager = manager_factory()
    lease = await manager.startRequire("TEST_CONFIG_ECHO", requirer="chat", config={"langs": ["en"]})
    await lease.wait_until_ready(timeout=30)
    first_pid = manager.handle("TEST_CONFIG_ECHO").pid
    lease.release()

    lease = await manager.startRequire("TEST_CONFIG_ECHO", requirer="chat", config={"langs": ["ch_sim", "en"]})
    await lease.wait_until_ready(timeout=30)
    assert await lease.invoke({}) == {"config": {"langs": ["ch_sim", "en"]}}
    assert manager.handle("TEST_CONFIG_ECHO").pid != first_pid
    lease.release()
```

- [ ] **Step 2: 运行确认失败**

Run: `.runtime/python.exe -m pytest backend/tests/test_processor_manager.py -q -k "config or crash or timeout or fifo or parallel or cancelled"`
Expected: FAIL —— `test_config_mismatch_raises_when_referenced` 与 `test_config_change_restarts_when_unreferenced` 失败（当前 `acquire_lease` 只在 config 为空时记录，不重启也不报错）。其它用例应已通过（崩溃/超时语义在 Task 5 已实现）。

- [ ] **Step 3: 实现 config 规则与 `busy`**

把 `manager.py` 中 `ProcessorHandle.acquire_lease` 整体替换为：

```python
    async def acquire_lease(self, lease: ProcessorLease) -> None:
        """登记一次引用并（必要时）触发启动。

        config 规则（规格 §10）：ACTIVE 且无人引用时 config 变化 → 先停再按新
        config 重启；仍有其它引用时 config 不一致 → 明确报错，不偷偷重启。
        """
        if self.state == STATE_STOPPING and self._stop_task is not None and not self._stop_task.done():
            await asyncio.shield(self._stop_task)   # 等上一轮停止收尾，避免启动/停止交叉
        desired = dict(lease.config)
        if desired and desired != self.config:
            if self.state == STATE_ACTIVE and self.refcount == 0:
                await self.stop(reason="config 变更，按新配置重启")
            elif self.state in (STATE_ACTIVE, STATE_SETTING_UP, STATE_STARTING):
                raise ProcessorConfigMismatchError(
                    f"{self.name} 正在使用 config={self.config}（holders={self.holders}），"
                    f"无法切换到 {desired}"
                )
            self.config = desired
        elif not self.config and desired:
            self.config = desired
        self.leases.append(lease)
        self.idle_since = None
        self.ensure_started()
```

在 `ProcessorHandle.pid_alive` 之后加入 `busy` 属性：

```python
    @property
    def busy(self) -> bool:
        """是否有在途或排队的 invoke（prune 空闲判定用）。"""
        if self._pending:
            return True
        return self._queue is not None and not self._queue.empty()
```

- [ ] **Step 4: 运行确认通过**

Run: `.runtime/python.exe -m pytest backend/tests/test_processor_manager.py -q`
Expected: PASS（28 passed）。

- [ ] **Step 5: 手工确认「崩溃不静默」**

Run: `.runtime/python.exe -m pytest backend/tests/test_processor_manager.py -q -k crash -s`
Expected: 输出里能看到 `Processor TEST_CRASH worker exited with code 7` 的 ERROR 日志（说明崩溃既回抛给调用方，也落日志）。

- [ ] **Step 6: Commit**

```bash
git add backend/faust_backend/processors/manager.py backend/tests/test_processor_manager.py
git commit -m "feat: Processor config 规则与崩溃/超时/取消语义测试"
```

---

### Task 8: prune、后台回收循环与配置项

**Files:**
- Modify: `backend/faust_backend/processors/manager.py`（`PruneReport`、`ProcessorManager.prune`、`ProcessorManager.prune_loop`）
- Modify: `backend/faust_backend/config_loader.py`（新增两个配置项）
- Modify: `backend/faust.config.example.json`（登记默认值）
- Modify: `docs/configuration.md`（新增一节说明）
- Test: `backend/tests/test_processor_manager.py`（追加）

**Interfaces:**
- Produces: `PruneReport(timeout, items)` + `as_dict()`；`await ProcessorManager.prune(timeout=37.0, whitelist=None) -> PruneReport`；`await ProcessorManager.prune_loop() -> None`。
- Produces: `config_loader.PROCESSOR_PRUNE_INTERVAL`（默认 30.0，0 = 禁用后台 prune）、`config_loader.PROCESSOR_IDLE_TIMEOUT`（默认 300.0）。
- Consumes: Task 7 的 `ProcessorHandle.busy`。

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_processor_manager.py`：

```python
# ── Task 8: prune 与后台回收 ────────────────────────────────


@pytest.mark.asyncio
async def test_prune_skips_stopped_and_referenced(manager_factory):
    manager = manager_factory()
    report = await manager.prune(timeout=0.0, whitelist=["TEST_ECHO"])
    assert report.items == [{"name": "TEST_ECHO", "action": "skipped", "reason": "already stopped"}]

    lease = await manager.startRequire("TEST_ECHO", requirer="chat")
    await lease.wait_until_ready(timeout=30)
    report = await manager.prune(timeout=0.0, whitelist=["TEST_ECHO"])
    assert report.items[0]["action"] == "skipped"
    assert "referenced by" in report.items[0]["reason"]
    assert manager.handle("TEST_ECHO").state == "ACTIVE"
    lease.release()


@pytest.mark.asyncio
async def test_prune_respects_idle_timeout_and_stops_when_expired(manager_factory):
    manager = manager_factory()
    lease = await manager.startRequire("TEST_ECHO", requirer="chat")
    await lease.wait_until_ready(timeout=30)
    lease.release()

    report = await manager.prune(timeout=60.0, whitelist=["TEST_ECHO"])
    assert report.items[0]["action"] == "skipped"
    assert "idle" in report.items[0]["reason"]
    assert manager.handle("TEST_ECHO").state == "ACTIVE"

    pid = manager.handle("TEST_ECHO").pid
    report = await manager.prune(timeout=0.0, whitelist=["TEST_ECHO"])
    assert report.as_dict()["timeout"] == 0.0
    assert report.items[0]["action"] == "stopped"
    snapshot = manager.handle("TEST_ECHO").status()
    assert snapshot["state"] == "STOPPED"
    assert snapshot["pid"] is None
    assert not psutil.pid_exists(int(pid))


@pytest.mark.asyncio
async def test_prune_whitelist_and_unknown_name(manager_factory):
    from faust_backend.processors.errors import ProcessorNotFoundError

    manager = manager_factory()
    lease = await manager.startRequire("TEST_SLOW", requirer="chat")
    await lease.wait_until_ready(timeout=30)
    peer = await manager.startRequire("TEST_SLOW_PEER", requirer="chat")
    await peer.wait_until_ready(timeout=30)
    lease.release()
    peer.release()

    report = await manager.prune(timeout=0.0, whitelist=["TEST_SLOW"])
    assert [item["name"] for item in report.items] == ["TEST_SLOW"]
    assert manager.handle("TEST_SLOW").state == "STOPPED"
    assert manager.handle("TEST_SLOW_PEER").state == "ACTIVE"   # 白名单外的没被动

    with pytest.raises(ProcessorNotFoundError):
        await manager.prune(timeout=0.0, whitelist=["NOT_REGISTERED"])
    await manager.stop("TEST_SLOW_PEER")


@pytest.mark.asyncio
async def test_prune_loop_reads_config_and_recycles(manager_factory, monkeypatch):
    import faust_backend.config_loader as conf

    manager = manager_factory()
    lease = await manager.startRequire("TEST_ECHO", requirer="chat")
    await lease.wait_until_ready(timeout=30)
    lease.release()

    monkeypatch.setattr(conf, "PROCESSOR_PRUNE_INTERVAL", 0.1)
    monkeypatch.setattr(conf, "PROCESSOR_IDLE_TIMEOUT", 0.0)
    task = asyncio.create_task(manager.prune_loop())
    try:
        for _ in range(50):
            if manager.handle("TEST_ECHO").state == "STOPPED":
                break
            await asyncio.sleep(0.1)
        assert manager.handle("TEST_ECHO").state == "STOPPED"
    finally:
        task.cancel()
        await task           # prune_loop 自己吞掉 CancelledError 后正常返回（对齐 _plugin_heartbeat_loop 写法）
```

- [ ] **Step 2: 运行确认失败**

Run: `.runtime/python.exe -m pytest backend/tests/test_processor_manager.py -q -k prune`
Expected: FAIL —— `AttributeError: 'ProcessorManager' object has no attribute 'prune'`

- [ ] **Step 3: 实现 prune 与后台循环**

在 `manager.py` 的 `_InvokeJob` 之后加入 dataclass：

```python
@dataclass
class PruneReport:
    """一次 prune 的结果。"""

    timeout: float
    items: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"timeout": self.timeout, "items": [dict(item) for item in self.items]}
```

（`dataclass` / `field` 已在 Task 5 的 import 区引入。）

在 `ProcessorManager.stop` 之前插入：

```python
    async def prune(self, timeout: float = 37.0, whitelist: list[str] | None = None) -> PruneReport:
        """回收长期空闲的 Processor（回溯式空闲判定）。

        候选集：``whitelist`` 为 None 表示全部已注册 Processor；给定列表时只处理
        列表内的名字，出现未注册名字抛 ``ProcessorNotFoundError``（不静默忽略）。
        """
        if whitelist is None:
            names = [entry.name for entry in list_processors()]
        else:
            names = []
            for raw in whitelist:
                names.append(get_processor(str(raw)).name)   # 未注册 → ProcessorNotFoundError

        report = PruneReport(timeout=float(timeout))
        now = time.time()
        for name in names:
            handle = self.handle(name)
            if handle.state == STATE_STOPPED:
                report.items.append({"name": name, "action": "skipped", "reason": "already stopped"})
                continue
            if handle.refcount > 0:
                report.items.append(
                    {"name": name, "action": "skipped", "reason": f"referenced by {handle.holders}"}
                )
                continue
            if handle.busy:
                report.items.append({"name": name, "action": "skipped", "reason": "requests pending"})
                continue
            idle = (now - handle.idle_since) if handle.idle_since is not None else 0.0
            if idle < report.timeout:
                report.items.append(
                    {"name": name, "action": "skipped", "reason": f"idle {idle:.1f}s < {report.timeout:.1f}s"}
                )
                continue
            try:
                await handle.stop(reason=f"prune: idle {idle:.1f}s >= {report.timeout:.1f}s")
            except Exception as exc:  # noqa: BLE001 - 单个 Processor 停止失败不影响其它
                report.items.append({"name": name, "action": "skipped", "reason": f"stop failed: {exc}"})
                continue
            report.items.append(
                {"name": name, "action": "stopped", "reason": f"idle {idle:.1f}s >= {report.timeout:.1f}s"}
            )
        return report

    async def prune_loop(self) -> None:
        """后台回收循环（lifespan 启动；写法对齐 ``_plugin_heartbeat_loop``）。

        每轮读一次配置，因此改配置无需重启后端：
        ``PROCESSOR_PRUNE_INTERVAL`` 为 0 表示禁用（仍睡眠，等待配置热改）。
        """
        while True:
            try:
                interval = float(conf.PROCESSOR_PRUNE_INTERVAL or 0)
                if interval <= 0:
                    await asyncio.sleep(30.0)
                    continue
                await asyncio.sleep(interval)
                report = await self.prune(timeout=float(conf.PROCESSOR_IDLE_TIMEOUT or 0))
                stopped = [item["name"] for item in report.items if item["action"] == "stopped"]
                if stopped:
                    log.info("Processor 空闲回收: %s", stopped)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 - 循环必须存活
                log.error("Processor prune 循环错误: %s", exc)
                await asyncio.sleep(1.0)
```

- [ ] **Step 4: 新增两个配置项**

`backend/faust_backend/config_loader.py`：

1. `load_configs()` 的 `global` 声明区（`global TTS_CHUNK_IDEAL_TOKENS` 之后）加一行：

```python
    global PROCESSOR_PRUNE_INTERVAL, PROCESSOR_IDLE_TIMEOUT
```

2. 同一函数里 `TTS_CHUNK_IDEAL_TOKENS = int(config.get('TTS_CHUNK_IDEAL_TOKENS', 30) or 30)` 之后、`AGENT_ROOT = ...` 之前插入：

```python
    # Processor（子进程计算任务）空闲回收策略，单位秒
    # PROCESSOR_PRUNE_INTERVAL = 0 表示禁用后台 prune（仍可手动调 admin 接口）
    PROCESSOR_PRUNE_INTERVAL = float(config.get('PROCESSOR_PRUNE_INTERVAL', 30) or 0)
    PROCESSOR_IDLE_TIMEOUT = float(config.get('PROCESSOR_IDLE_TIMEOUT', 300) or 0)
```

`backend/faust.config.example.json`：把结尾

```json
    "EDGE_TTS_TIMEOUT_SECONDS": 120
}
```

改为

```json
    "EDGE_TTS_TIMEOUT_SECONDS": 120,
    "PROCESSOR_PRUNE_INTERVAL": 30,
    "PROCESSOR_IDLE_TIMEOUT": 300
}
```

`docs/configuration.md` 末尾追加：

```markdown
## 高级：重计算子进程（Processor）回收

FaustBot 把 VAD、OCR 这类重计算放在独立子进程中运行，空闲时自动回收以释放内存/显存。

| 配置项 | 默认值 | 说明 |
| ---- | -------- | ------------------------------------------ |
| `PROCESSOR_PRUNE_INTERVAL` | `30` | 后台回收检查间隔（秒）。设为 `0` 表示禁用后台自动回收 |
| `PROCESSOR_IDLE_TIMEOUT` | `300` | 空闲超过该秒数且无人引用时回收子进程 |

> 提示：正在被语音识别等功能使用的子进程不会被回收；回收只发生在引用计数归零且请求队列为空的空闲时刻。
```

- [ ] **Step 5: 运行确认通过**

Run: `.runtime/python.exe -m pytest backend/tests/test_processor_manager.py -q`
Expected: PASS（32 passed）。

Run: `.runtime/python.exe -c "import sys; sys.path.insert(0, 'backend'); import faust_backend.config_loader as c; print(c.PROCESSOR_PRUNE_INTERVAL, c.PROCESSOR_IDLE_TIMEOUT)"`（cwd=仓库根）
Expected: 打印 `30.0 300.0`（本地 `~/.faustbot/faust.config.json` 未显式覆盖时）。

- [ ] **Step 6: Commit**

```bash
git add backend/faust_backend/processors/manager.py backend/faust_backend/config_loader.py backend/faust.config.example.json docs/configuration.md backend/tests/test_processor_manager.py
git commit -m "feat: Processor prune 回收、后台循环与配置项"
```

---

### Task 9: 内置 Processor —— VAD

**Files:**
- Create: `backend/faust_backend/processors/builtin/__init__.py`
- Create: `backend/faust_backend/processors/builtin/vad.py`
- Modify: `backend/faust_backend/download_vad.py`（抽出可复用函数，消除与 Processor 的重复实现）
- Test: `backend/tests/test_processor_manager.py`（追加契约与阈值映射测试）

**Interfaces:**
- Produces: `VadProcessor`（`NAME="VAD"`）、模块常量 `SAMPLE_RATE=16000` / `WINDOW_SIZE=512` / `VAD_THRESHOLD=0.5`（`routes/audio.py` 在 Task 10 引用它们）。
- Produces: `download_vad.ensure_vad_cache(torch_hub_dir, log_fn=None) -> None`（CI 的 `main()` 与 `VadProcessor.setup` 共用同一份下载/校验逻辑）。
- Consumes: Task 4 的 `Processor` / `ProcessorContext`。

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_processor_manager.py`：

```python
# ── Task 9: 内置 VAD Processor ──────────────────────────────


def test_vad_processor_contract():
    from faust_backend.processors.builtin.vad import (
        SAMPLE_RATE,
        VAD_THRESHOLD,
        WINDOW_SIZE,
        VadProcessor,
    )

    assert (SAMPLE_RATE, WINDOW_SIZE, VAD_THRESHOLD) == (16000, 512, 0.5)
    assert VadProcessor.NAME == "VAD"
    assert Path(VadProcessor.DATA_DIR).parts[-4:] == ("backend", "asr-hub", "model", "torch_hub")
    assert VadProcessor.SETUP_TIMEOUT == 900.0
    assert VadProcessor().setup_fingerprint({"unused": 1}) == VadProcessor.SETUP_VERSION


def test_vad_invoke_maps_probability_to_speech_flag(tmp_path):
    import faust_backend.processors.builtin.vad as vad_module

    class _FakeTensor:
        def __init__(self, value: float) -> None:
            self._value = value

        def item(self) -> float:
            return self._value

    class _FakeModel:
        def __init__(self, value: float) -> None:
            self.value = value

        def __call__(self, tensor, sample_rate):
            assert sample_rate == 16000
            return _FakeTensor(self.value)

    class _FakeTorch:
        @staticmethod
        def from_numpy(array):
            return array

        @staticmethod
        def no_grad():
            import contextlib

            return contextlib.nullcontext()

    ctx = ProcessorContext(name="VAD", data_dir=tmp_path, config={}, log_sink=lambda level, msg: None)

    loud = vad_module.VadProcessor()
    loud._torch = _FakeTorch
    loud._model = _FakeModel(0.91)
    assert loud.invoke(ctx, {"audio": [0.0] * 512}) == {"probability": 0.91, "is_speech": True}

    quiet = vad_module.VadProcessor()
    quiet._torch = _FakeTorch
    quiet._model = _FakeModel(0.2)
    assert quiet.invoke(ctx, {"audio": [0.0] * 512}) == {"probability": 0.2, "is_speech": False}

    with pytest.raises(ValueError):
        loud.invoke(ctx, {"audio": [0.0] * 256})


def test_vad_stop_drops_model_reference(tmp_path):
    import faust_backend.processors.builtin.vad as vad_module

    ctx = ProcessorContext(name="VAD", data_dir=tmp_path, config={}, log_sink=lambda level, msg: None)
    processor = vad_module.VadProcessor()
    processor._model = object()
    processor.stop(ctx)
    assert processor._model is None
```

- [ ] **Step 2: 运行确认失败**

Run: `.runtime/python.exe -m pytest backend/tests/test_processor_manager.py -q -k vad`
Expected: FAIL —— `ModuleNotFoundError: No module named 'faust_backend.processors.builtin'`

- [ ] **Step 3: 实现 VAD Processor**

`backend/faust_backend/processors/builtin/__init__.py`：

```python
"""内置 Processor 的注册入口（导入即完成注册）。"""

from __future__ import annotations

from .ocr import OcrProcessor
from .vad import VadProcessor

__all__ = ["OcrProcessor", "VadProcessor"]
```

> 注：`ocr.py` 由 Task 11 创建。若本 Task 单独运行，`__init__.py` 先只 import `VadProcessor`，Task 11 再把 `OcrProcessor` 那两行补上（**不要**用 try/except 兜底）。

`backend/faust_backend/processors/builtin/vad.py`：

```python
"""VAD（语音活动检测）Processor：silero-vad 跑在独立子进程里。"""

from __future__ import annotations

import gc
from pathlib import Path
from typing import Any

import numpy as np

from ..base import Processor, ProcessorContext
from ..registry import processor

SAMPLE_RATE = 16000
WINDOW_SIZE = 512
VAD_THRESHOLD = 0.5

#: backend/ 目录（本文件位于 backend/faust_backend/processors/builtin/）
BACKEND_ROOT = Path(__file__).resolve().parents[3]
#: torch hub 缓存目录：与迁移前 vad_runtime.py / download_vad.py 完全一致
TORCH_HUB_DIR = BACKEND_ROOT / "asr-hub" / "model" / "torch_hub"
SILERO_CACHE_DIR = TORCH_HUB_DIR / "snakers4_silero-vad_master"

#: 依赖缺失时的提示（与迁移前的文案保持一致）
TORCH_MISSING_MESSAGE = (
    "PyTorch 未安装，VAD 语音检测不可用。"
    "请运行 setup-runtime.bat --torch cpu 安装 PyTorch 后重试。"
)


@processor("VAD")
class VadProcessor(Processor):
    """`{"audio": float32[512]}` → `{"probability": float, "is_speech": bool}`。"""

    NAME = "VAD"
    #: 数据目录=torch hub 目录（保持迁移前的磁盘布局：模型缓存与 setup marker 同处）
    DATA_DIR = TORCH_HUB_DIR
    #: 下载 + 构建 silero 模型
    SETUP_TIMEOUT = 900.0
    START_TIMEOUT = 120.0
    STOP_TIMEOUT = 15.0
    INVOKE_TIMEOUT = 10.0

    def __init__(self) -> None:
        self._torch: Any = None
        self._model: Any = None

    def _import_torch(self):
        try:
            import torch
        except ModuleNotFoundError as exc:
            raise RuntimeError(TORCH_MISSING_MESSAGE) from exc
        return torch

    def setup(self, ctx: ProcessorContext) -> None:
        """无缓存时才联网下载（与 download_vad.ensure_vad_cache 同一实现）。"""
        from faust_backend.download_vad import ensure_vad_cache

        ensure_vad_cache(TORCH_HUB_DIR, log_fn=lambda msg: ctx.log(msg))

    def start(self, ctx: ProcessorContext) -> None:
        """只做本地加载：缓存缺失时明确报错，绝不偷偷联网。"""
        torch = self._import_torch()
        ctx.log(f"torch {torch.__version__}，从 {SILERO_CACHE_DIR} 加载 silero-vad")
        torch.hub.set_dir(str(TORCH_HUB_DIR))
        if not (SILERO_CACHE_DIR.is_dir() and (SILERO_CACHE_DIR / "hubconf.py").is_file()):
            raise RuntimeError(f"silero-vad 缓存缺失: {SILERO_CACHE_DIR}（setup 未成功完成）")
        model, _ = torch.hub.load(repo_or_dir=str(SILERO_CACHE_DIR), model="silero_vad", source="local")
        model.to("cpu")
        model.eval()
        self._torch = torch
        self._model = model

    def invoke(self, ctx: ProcessorContext, data: Any) -> dict[str, Any]:
        if self._model is None or self._torch is None:
            raise RuntimeError("VAD 模型未加载")
        audio = data.get("audio") if isinstance(data, dict) else data
        frame = np.asarray(audio, dtype=np.float32)
        if frame.ndim != 1 or frame.shape[0] != WINDOW_SIZE:
            raise ValueError(f"unexpected VAD frame shape: {frame.shape}")
        tensor = self._torch.from_numpy(frame)
        with self._torch.no_grad():
            probability = float(self._model(tensor, SAMPLE_RATE).item())
        return {"probability": probability, "is_speech": probability > VAD_THRESHOLD}

    def stop(self, ctx: ProcessorContext) -> None:
        self._model = None
        self._torch = None
        gc.collect()
```

另需在 `backend/faust_backend/processors/manager.py` 的 import 区（`from .registry import ...` 两行之后）加一行，保证**父进程**一旦用到 manager 就已经注册好内置 Processor：

```python
from . import builtin as _builtin  # noqa: F401 - import 副作用：注册内置 Processor（VAD/OCR）
```

（worker 只 import `protocol` / `base` / `registry`，不会 import manager，所以子进程不会因此多加载模块。）

- [ ] **Step 4: 消除与 `download_vad.py` 的重复实现**

把 `backend/faust_backend/download_vad.py` 改为（`main()` 行为对 CI 不变）：

```python
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

REPO_OR_DIR = "snakers4/silero-vad"
MODEL_NAME = "silero_vad"
CACHE_DIR_NAME = "snakers4_silero-vad_master"


def ensure_vad_cache(torch_hub_dir: Path, log_fn: Callable[[str], None] | None = None) -> None:
    """确保 silero-vad 已缓存到 ``torch_hub_dir``（无缓存时才联网）。

    被 CI 脚本 ``main()`` 与 ``VadProcessor.setup`` 共用，避免两份实现漂移。
    """
    import torch

    hub_dir = Path(torch_hub_dir)
    hub_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = hub_dir / CACHE_DIR_NAME
    if cache_dir.is_dir() and (cache_dir / "hubconf.py").is_file():
        if log_fn:
            log_fn(f"silero-vad 缓存命中: {cache_dir}")
        return

    if log_fn:
        log_fn(f"silero-vad 缓存缺失，联网下载到 {hub_dir}")
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        torch.hub.set_auth_token(token)
    torch.hub.set_dir(str(hub_dir))
    original_torch_home = os.environ.get("TORCH_HOME")
    os.environ["TORCH_HOME"] = str(hub_dir)
    try:
        model, _utils = torch.hub.load(
            repo_or_dir=REPO_OR_DIR,
            model=MODEL_NAME,
            force_reload=False,
            trust_repo=True,
            onnx=False,
        )
        model.to("cpu")
        model.eval()
    finally:
        if original_torch_home is None:
            os.environ.pop("TORCH_HOME", None)
        else:
            os.environ["TORCH_HOME"] = original_torch_home
    if log_fn:
        log_fn("silero-vad 下载完成")


def main() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    torch_hub_dir = backend_root / "asr-hub" / "model" / "torch_hub"
    print(f"[download_vad] torch hub dir: {torch_hub_dir}")
    ensure_vad_cache(torch_hub_dir, log_fn=lambda msg: print(f"[download_vad] {msg}"))
    print("[download_vad] VAD model is ready.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 运行确认通过**

Run: `.runtime/python.exe -m pytest backend/tests/test_processor_manager.py -q -k vad`
Expected: PASS（3 passed）。

Run（确认内置 Processor 在父进程里已注册）:
`.runtime/python.exe -c "import sys; sys.path.insert(0, 'backend'); from faust_backend.processors import get_processor_manager; print([item['name'] for item in get_processor_manager().status()])"`（cwd=仓库根）
Expected: `['VAD']`（Task 11 完成后变成 `['OCR', 'VAD']`）。

Run（真实下载路径可用性检查，命中缓存时应立即返回）：
`.runtime/python.exe -c "import sys; sys.path.insert(0, 'backend'); from pathlib import Path; from faust_backend.download_vad import ensure_vad_cache; ensure_vad_cache(Path('backend/asr-hub/model/torch_hub'), print)"`（cwd=仓库根）
Expected: 打印 `silero-vad 缓存命中: ...torch_hub\snakers4_silero-vad_master`（若本机无缓存则会联网下载，属预期）。

- [ ] **Step 6: Commit**

```bash
git add backend/faust_backend/processors/builtin/__init__.py backend/faust_backend/processors/builtin/vad.py backend/faust_backend/download_vad.py backend/tests/test_processor_manager.py
git commit -m "feat: 内置 VAD Processor 并统一 silero 缓存逻辑"
```

---

### Task 10: VAD 迁移（路由 / 生命周期 / 服务可用性降级）

**Files:**
- Modify: `backend/faust_backend/routes/audio.py:1-44`
- Modify: `backend/faust_backend/runtime/lifecycle.py:37,682-686,735`（另在 :699 附近加 prune 循环任务）
- Modify: `backend/faust_backend/runtime/state.py:78` 附近
- Delete: `backend/faust_backend/vad_runtime.py`
- Modify: `AGENTS.md:58-63`（语音管道里的 VAD 文件路径）
- Test: `backend/tests/test_vad_ws_lease.py`（新建）

**Interfaces:**
- Consumes: Task 9 的 `VadProcessor` / `SAMPLE_RATE` / `WINDOW_SIZE` / `VAD_THRESHOLD`，Task 5-8 的 manager API。
- Produces: `/faust/audio/ws/vad` 保持「每帧回一条 JSON」的协议；降级帧**必须**带 `error` 字段（前端 `app.js:2855` 用它排除降级帧、避免污染 PTT 命中率统计）。
- Produces: `/faust/audio/vad/status` 保留旧字段 `is_loaded`/`is_running`/`active_connections`/`sample_rate`/`window_size`/`threshold`/`unavailable_reason`，追加 `state`/`last_error`/`pid`/`refcount`。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_vad_ws_lease.py`：

```python
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _FakeWebSocket:
    """最小 WebSocket 替身：按预设帧序列喂数据，记录发回去的文本。"""

    def __init__(self, frames: list[bytes]) -> None:
        self._frames = list(frames)
        self.sent: list[dict] = []
        self.accepted = False
        self.closed = False

    async def accept(self) -> None:
        self.accepted = True

    async def receive_bytes(self) -> bytes:
        if not self._frames:
            from fastapi import WebSocketDisconnect

            raise WebSocketDisconnect(code=1000)
        return self._frames.pop(0)

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def close(self) -> None:
        self.closed = True


class _FakeHandle:
    def __init__(self, state: str = "ACTIVE") -> None:
        self.state = state
        self.pid = 4242
        self.last_error = None
        self.refcount = 1

    def status(self) -> dict:
        return {
            "name": "VAD",
            "owner": "builtin",
            "state": self.state,
            "phase": None,
            "pid": self.pid,
            "pid_alive": True,
            "last_error": self.last_error,
            "refcount": self.refcount,
            "holders": {"vad_ws:1": 1},
            "queue_depth": 0,
            "idle_seconds": None,
            "uptime_seconds": 3.0,
            "setup_done": True,
            "fingerprint": "1",
            "config": {},
            "invokes_total": 1,
            "invokes_failed": 0,
            "invokes_cancelled": 0,
            "last_invoke_seconds": 0.01,
        }


class _FakeLease:
    def __init__(self, handle: _FakeHandle, results: list) -> None:
        self.name = "VAD"
        self.requirer = "vad_ws:1"
        self.handle = handle
        self._results = list(results)
        self.released = False
        self.ready_calls = 0

    async def wait_until_ready(self, timeout=None) -> None:
        self.ready_calls += 1

    async def invoke(self, data, *, timeout=None):
        item = self._results.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item()
        return item

    async def get_log(self, level=None, limit=None):
        return []

    def release(self) -> None:
        self.released = True


class _FakeManager:
    def __init__(self, lease_factory) -> None:
        self._lease_factory = lease_factory
        self.start_calls = 0
        self.handle_obj = _FakeHandle()

    def handle(self, name: str) -> _FakeHandle:
        return self.handle_obj

    async def startRequire(self, name: str, requirer: str, *, config=None):
        self.start_calls += 1
        return self._lease_factory()


def _frame() -> bytes:
    return np.zeros(512, dtype=np.float32).tobytes()


@pytest.mark.asyncio
async def test_vad_ws_forwards_probability_and_is_speech(monkeypatch):
    import faust_backend.routes.audio as audio_routes

    manager = _FakeManager(lambda: _FakeLease(_FakeHandle(), [{"probability": 0.83, "is_speech": True}]))
    monkeypatch.setattr(audio_routes, "get_processor_manager", lambda: manager)

    ws = _FakeWebSocket([_frame()])
    await audio_routes.speech_vad_ws(ws)

    assert ws.accepted is True and ws.closed is True
    assert ws.sent == [{"probability": 0.83, "is_speech": True}]


@pytest.mark.asyncio
async def test_vad_ws_degrades_with_error_field_and_backs_off(monkeypatch):
    from faust_backend.processors.errors import ProcessorStartError

    import faust_backend.routes.audio as audio_routes

    def _raise():
        raise ProcessorStartError("torch 缺失", last_error="ModuleNotFoundError: torch")

    manager = _FakeManager(_raise)
    monkeypatch.setattr(audio_routes, "get_processor_manager", lambda: manager)

    ws = _FakeWebSocket([_frame(), _frame()])
    await audio_routes.speech_vad_ws(ws)

    assert len(ws.sent) == 2
    for item in ws.sent:
        assert item["is_speech"] is False
        assert item["probability"] == 0.0
        assert "torch 缺失" in item["error"]   # 降级帧必须带 error，前端据此排除 PTT 命中率统计
    assert manager.start_calls == 1            # 后续帧落在退避窗口内，不再拉起子进程


@pytest.mark.asyncio
async def test_vad_ws_self_heals_after_worker_crash(monkeypatch):
    """同一连接里 worker 崩溃后：换新 lease 并继续回帧（自愈）。"""
    import faust_backend.routes.audio as audio_routes

    handle = _FakeHandle(state="ACTIVE")
    leases: list[_FakeLease] = []

    def _factory() -> _FakeLease:
        lease = _FakeLease(handle, [{"probability": 0.4, "is_speech": False}])
        leases.append(lease)
        return lease

    manager = _FakeManager(_factory)
    monkeypatch.setattr(audio_routes, "get_processor_manager", lambda: manager)

    # 第二帧之前把 handle 标记为 STOPPED，模拟 worker 崩溃/被 prune 回收
    class _CrashAfterFirstFrameWs(_FakeWebSocket):
        async def receive_bytes(self) -> bytes:
            if self._frames and len(self._frames) == 1:
                handle.state = "STOPPED"
            return await super().receive_bytes()

    ws = _CrashAfterFirstFrameWs([_frame(), _frame()])
    await audio_routes.speech_vad_ws(ws)

    assert manager.start_calls == 2                                   # 崩溃后重新 require
    assert leases[0].released is True                                 # 旧 lease 已释放
    assert ws.sent == [
        {"probability": 0.4, "is_speech": False},
        {"probability": 0.4, "is_speech": False},
    ]


@pytest.mark.asyncio
async def test_vad_ws_releases_lease_on_disconnect(monkeypatch):
    import faust_backend.routes.audio as audio_routes

    lease = _FakeLease(_FakeHandle(), [{"probability": 0.1, "is_speech": False}])
    manager = _FakeManager(lambda: lease)
    monkeypatch.setattr(audio_routes, "get_processor_manager", lambda: manager)

    await audio_routes.speech_vad_ws(_FakeWebSocket([]))
    assert lease.released is True


@pytest.mark.asyncio
async def test_vad_status_keeps_legacy_fields(monkeypatch):
    import faust_backend.routes.audio as audio_routes

    manager = _FakeManager(lambda: _FakeLease(_FakeHandle(), []))
    manager.handle_obj.state = "ACTIVE"
    manager.handle_obj.refcount = 2
    manager.handle_obj.last_error = None
    monkeypatch.setattr(audio_routes, "get_processor_manager", lambda: manager)

    payload = await audio_routes.speech_vad_status_get()

    for key in (
        "is_loaded",
        "is_running",
        "active_connections",
        "sample_rate",
        "window_size",
        "threshold",
        "unavailable_reason",
        "state",
        "last_error",
        "pid",
        "refcount",
    ):
        assert key in payload, key
    assert payload["is_loaded"] is True
    assert payload["active_connections"] == 2
    assert (payload["sample_rate"], payload["window_size"], payload["threshold"]) == (16000, 512, 0.5)
```

- [ ] **Step 2: 运行确认失败**

Run: `.runtime/python.exe -m pytest backend/tests/test_vad_ws_lease.py -q`
Expected: FAIL —— `AttributeError: module 'faust_backend.routes.audio' has no attribute 'get_processor_manager'`

- [ ] **Step 3: 改造路由**

`backend/faust_backend/routes/audio.py` 顶部：把 `import faust_backend.vad_runtime as vad_runtime` 替换为

```python
from faust_backend.processors import get_processor_manager
from faust_backend.processors.builtin.vad import SAMPLE_RATE, VAD_THRESHOLD, WINDOW_SIZE
from faust_backend.processors.errors import ProcessorError
```

并把 `VAD_DEGRADED_BACKOFF_SECONDS = 30.0` 加在 `router = APIRouter(tags=["audio"])` 之后。

`/faust/audio/vad/status` 端点替换为：

```python
@router.get("/faust/audio/vad/status")
async def speech_vad_status_get():
    # 旧字段（is_loaded/is_running/active_connections/unavailable_reason）保持不变，
    # 追加 Processor 视角的 state/last_error/pid/refcount
    snapshot = get_processor_manager().handle("VAD").status()
    return {
        "is_loaded": snapshot["state"] == "ACTIVE",
        "is_running": snapshot["refcount"] > 0,
        "active_connections": snapshot["refcount"],
        "sample_rate": SAMPLE_RATE,
        "window_size": WINDOW_SIZE,
        "threshold": VAD_THRESHOLD,
        "unavailable_reason": snapshot["last_error"],
        "state": snapshot["state"],
        "last_error": snapshot["last_error"],
        "pid": snapshot["pid"],
        "refcount": snapshot["refcount"],
    }
```

`/faust/audio/ws/vad` 端点替换为：

```python
@router.websocket("/faust/audio/ws/vad")
async def speech_vad_ws(websocket: WebSocket):
    await websocket.accept()
    manager = get_processor_manager()
    requirer = f"vad_ws:{id(websocket)}"
    loop = asyncio.get_running_loop()
    lease = None
    needs_ready = True
    retry_after = 0.0
    last_error_text = ""
    try:
        try:
            lease = await manager.startRequire("VAD", requirer=requirer)   # 连接即持有引用
        except ProcessorError as e:
            # 依赖缺失（如未装 torch）等：连接保留，按降级帧回复，退避后重试
            last_error_text = str(e)
            retry_after = loop.time() + VAD_DEGRADED_BACKOFF_SECONDS
            log.error("VAD 不可用: %s", e)

        while True:
            data = await websocket.receive_bytes()
            audio = np.frombuffer(data, dtype=np.float32).copy()
            if len(audio) != WINDOW_SIZE:
                continue

            if lease is None:
                now = loop.time()
                if now < retry_after:
                    await websocket.send_text(
                        json.dumps(
                            {"is_speech": False, "probability": 0.0, "error": last_error_text or "VAD 正在恢复，请稍候"},
                            ensure_ascii=False,
                        )
                    )
                    continue
                try:
                    lease = await manager.startRequire("VAD", requirer=requirer)
                    needs_ready = True
                except ProcessorError as e:
                    last_error_text = str(e)
                    retry_after = now + VAD_DEGRADED_BACKOFF_SECONDS
                    log.error("VAD 不可用: %s", e)
                    await websocket.send_text(
                        json.dumps({"is_speech": False, "probability": 0.0, "error": last_error_text}, ensure_ascii=False)
                    )
                    continue

            if needs_ready:
                try:
                    await lease.wait_until_ready()
                    needs_ready = False
                except ProcessorError as e:
                    last_error_text = str(e)
                    retry_after = loop.time() + VAD_DEGRADED_BACKOFF_SECONDS
                    log.error("VAD 不可用: %s", e)
                    lease.release()
                    lease = None
                    await websocket.send_text(
                        json.dumps({"is_speech": False, "probability": 0.0, "error": last_error_text}, ensure_ascii=False)
                    )
                    continue
            elif lease.handle.state != "ACTIVE":
                # worker 崩溃或已被 prune 回收：换一条新 lease（自愈），失败则退避
                lease.release()
                lease = None
                try:
                    lease = await manager.startRequire("VAD", requirer=requirer)
                    await lease.wait_until_ready()
                    needs_ready = False
                    log.warning("VAD worker 已重启")
                except ProcessorError as e:
                    last_error_text = str(e)
                    retry_after = loop.time() + VAD_DEGRADED_BACKOFF_SECONDS
                    log.error("VAD 不可用: %s", e)
                    lease = None
                    await websocket.send_text(
                        json.dumps({"is_speech": False, "probability": 0.0, "error": last_error_text}, ensure_ascii=False)
                    )
                    continue

            try:
                result = await lease.invoke({"audio": audio})
            except ProcessorError as e:
                last_error_text = str(e)
                log.error("VAD invoke 失败: %s", e)
                lease.release()
                lease = None
                needs_ready = True
                await websocket.send_text(
                    json.dumps({"is_speech": False, "probability": 0.0, "error": last_error_text}, ensure_ascii=False)
                )
                continue
            await websocket.send_text(json.dumps(result, ensure_ascii=False))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.error("VAD WebSocket 错误: %s", e)
    finally:
        if lease is not None:
            lease.release()
        try:
            await websocket.close()
        except Exception:
            pass
```

行为要点（测试会覆盖）：

- **连接即持有引用**（规格 §17）：`GET /faust/audio/vad/status` 的 `active_connections`/`refcount` 等于当前 WS 连接数。
- 首个有效帧触发 `wait_until_ready()`（首次连接要等模型加载/下载完成，这段时间该连接不回帧）。
- 运行期发现 `handle.state != "ACTIVE"` → 换新 lease 并记一条「VAD worker 已重启」（崩溃自愈）。
- 任何 `ProcessorError` 都回一条**带 `error` 字段**的降级帧（前端 `app.js` 靠 `error` 排除降级帧，避免污染 PTT 命中率统计），并进入 30s 退避，避免每帧都拉起子进程。

- [ ] **Step 4: 生命周期接线**

`backend/faust_backend/runtime/state.py`：在 `plugin_heartbeat_task = None` 之后加一行

```python
processor_prune_task = None
```

`backend/faust_backend/runtime/lifecycle.py`：

1. 删除 :37 的 `import faust_backend.vad_runtime as vad_runtime`，改为（放在 runtime imports 区）

```python
from faust_backend.processors import get_processor_manager
```

2. 删除启动段的 VAD 预热块（原 :682-686）：

```python
    try:
        await vad_runtime.vad_runtime.startup()
        log.info("VAD 运行时已加载到 CPU")
    except Exception as e:
        log.warning("启动 VAD 初始化失败: %s", e)
```

3. 在 `if state.plugin_heartbeat_task is None:` 那三行之后加：

```python
    if state.processor_prune_task is None:
        state.processor_prune_task = asyncio.create_task(get_processor_manager().prune_loop())
```

4. 关闭段：删除 `await vad_runtime.vad_runtime.shutdown()`，并在 `if state.plugin_heartbeat_task is not None:` 取消块之后加：

```python
    if state.processor_prune_task is not None:
        state.processor_prune_task.cancel()
        try:
            await state.processor_prune_task
        except Exception:
            pass
        state.processor_prune_task = None
    await get_processor_manager().shutdown()
```

5. 删除文件 `backend/faust_backend/vad_runtime.py`：

```bash
git rm backend/faust_backend/vad_runtime.py
```

6. `AGENTS.md` 语音管道条目改为：

```markdown
   - VAD: 语音活动检测（`processors/builtin/vad.py`，跑在受管子进程内）
```

- [ ] **Step 5: 运行确认通过**

Run: `.runtime/python.exe -m pytest backend/tests/test_vad_ws_lease.py -q`
Expected: PASS（5 passed）。

Run（确认没有残留引用）: `.runtime/python.exe -c "import sys; sys.path.insert(0, 'backend'); import faust_backend.routes.audio, faust_backend.runtime.lifecycle; print('ok')"`（cwd=仓库根）
Expected: 打印 `ok`，不出现 `ModuleNotFoundError: faust_backend.vad_runtime`。

- [ ] **Step 6: Commit**

```bash
git add -A backend/faust_backend/routes/audio.py backend/faust_backend/runtime/lifecycle.py backend/faust_backend/runtime/state.py backend/tests/test_vad_ws_lease.py AGENTS.md
git commit -m "refactor: VAD 迁移到 Processor 子进程并删除 vad_runtime"
```

---

### Task 11: 内置 Processor —— OCR（easyocr）

**Files:**
- Create: `backend/faust_backend/processors/builtin/ocr.py`
- Modify: `backend/faust_backend/processors/builtin/__init__.py`（补 `OcrProcessor` 导入）
- Test: `backend/tests/test_processor_manager.py`（追加）

**Interfaces:**
- Produces: `OcrProcessor`（`NAME="OCR"`）、`normalize_config(config) -> (langs, gpu)`、`MODEL_DIR`（`~/.faustbot/models/easyocr`）。
- 输入 `{"image": np.uint8[H,W,3], "detail": 1}` → 输出 `[{"text": str, "confidence": float, "box": [[x, y] × 4]}]`（`detail=0` 时返回 `[str]`）。
- Consumes: Task 4 的 `Processor` / `ProcessorContext`。

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_processor_manager.py`：

```python
# ── Task 11: 内置 OCR Processor ─────────────────────────────


def test_ocr_normalize_config_defaults_and_overrides():
    from faust_backend.processors.builtin.ocr import DEFAULT_LANGS, normalize_config

    assert normalize_config({}) == (DEFAULT_LANGS, False)
    assert normalize_config({"langs": ["en"], "gpu": True}) == (["en"], True)
    assert normalize_config({"langs": "ch_sim,en"}) == (["ch_sim", "en"], False)


def test_ocr_fingerprint_tracks_langs():
    from faust_backend.processors.builtin.ocr import OcrProcessor

    processor = OcrProcessor()
    assert processor.setup_fingerprint({"langs": ["en", "ch_sim"]}) == processor.setup_fingerprint(
        {"langs": ["ch_sim", "en"]}
    )
    assert processor.setup_fingerprint({"langs": ["en"]}) != processor.setup_fingerprint(
        {"langs": ["ch_sim", "en"]}
    )


def test_ocr_invoke_shapes_with_fake_reader(tmp_path):
    import faust_backend.processors.builtin.ocr as ocr_module

    class _FakeReader:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def readtext(self, image, detail=1):
            self.calls.append((image.shape, detail))
            if detail <= 0:
                return ["Hello", "World"]
            return [
                ([[10, 10], [30, 10], [30, 30], [10, 30]], "Hello", 0.93),
                ([[0, 0], [5, 0], [5, 5], [0, 5]], "noise", 0.11),
            ]

    ctx = ProcessorContext(name="OCR", data_dir=tmp_path, config={}, log_sink=lambda level, msg: None)
    processor = ocr_module.OcrProcessor()
    reader = _FakeReader()
    processor._reader = reader

    image = np.zeros((20, 40, 3), dtype=np.uint8)
    items = processor.invoke(ctx, {"image": image, "detail": 1})
    assert items == [
        {"text": "Hello", "confidence": 0.93, "box": [[10.0, 10.0], [30.0, 10.0], [30.0, 30.0], [10.0, 30.0]]},
        {"text": "noise", "confidence": 0.11, "box": [[0.0, 0.0], [5.0, 0.0], [5.0, 5.0], [0.0, 5.0]]},
    ]
    assert reader.calls[0] == ((20, 40, 3), 1)

    assert processor.invoke(ctx, {"image": image, "detail": 0}) == ["Hello", "World"]

    rgba = np.zeros((20, 40, 4), dtype=np.uint8)
    processor.invoke(ctx, {"image": rgba, "detail": 1})
    assert reader.calls[-1][0] == (20, 40, 3)          # RGBA 被裁到 RGB

    with pytest.raises(ValueError):
        processor.invoke(ctx, {"image": np.zeros((20, 40), dtype=np.uint8)})

    with pytest.raises(RuntimeError):
        ocr_module.OcrProcessor().invoke(ctx, {"image": image})


def test_ocr_stop_clears_reader_and_empties_cuda_cache(tmp_path, monkeypatch):
    import faust_backend.processors.builtin.ocr as ocr_module

    calls: list[str] = []

    class _FakeTorch:
        class cuda:
            @staticmethod
            def empty_cache() -> None:
                calls.append("empty_cache")

    monkeypatch.setitem(sys.modules, "torch", _FakeTorch)

    ctx = ProcessorContext(name="OCR", data_dir=tmp_path, config={}, log_sink=lambda level, msg: None)
    processor = ocr_module.OcrProcessor()
    processor._reader = object()
    processor._gpu = True
    processor.stop(ctx)

    assert processor._reader is None
    assert calls == ["empty_cache"]
```

- [ ] **Step 2: 运行确认失败**

Run: `.runtime/python.exe -m pytest backend/tests/test_processor_manager.py -q -k ocr`
Expected: FAIL —— `ModuleNotFoundError: No module named 'faust_backend.processors.builtin.ocr'`

- [ ] **Step 3: 实现 OCR Processor**

`backend/faust_backend/processors/builtin/ocr.py`：

```python
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
```

`backend/faust_backend/processors/builtin/__init__.py`：把 Task 9 里注释掉的部分补全为最终形态（`from .ocr import OcrProcessor` + `__all__ = ["OcrProcessor", "VadProcessor"]`）。

- [ ] **Step 4: 运行确认通过**

Run: `.runtime/python.exe -m pytest backend/tests/test_processor_manager.py -q -k ocr`
Expected: PASS（4 passed）。

- [ ] **Step 5: Commit**

```bash
git add backend/faust_backend/processors/builtin/ocr.py backend/faust_backend/processors/builtin/__init__.py backend/tests/test_processor_manager.py
git commit -m "feat: 内置 OCR Processor（easyocr 子进程）"
```

---

### Task 12: `ui_operator` 改用 OCR Processor

**Files:**
- Modify: `backend/default_plugins/ui_operator/main.py:19-21,65-73,7-16,200-244`
- Test: `backend/tests/test_ui_operator_ocr.py`（新建）

**Interfaces:**
- Consumes: Task 11 的 `OcrProcessor`（`require("OCR", requirer="ui_operator", config={"langs": ..., "gpu": ...})`）。
- Produces: `screenOCRTool` 变为 **async** 工具，返回值形状不变（`{"res":[{"id","text","pos"}]}`）。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_ui_operator_ocr.py`：

```python
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pyautogui
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faust_backend.plugin_system import PluginManager

REPO_PLUGIN_DIR = Path(__file__).resolve().parents[1] / "default_plugins"


class _FakeLease:
    def __init__(self, calls: list) -> None:
        self.name = "OCR"
        self.requirer = "ui_operator"
        self.handle = SimpleNamespace(state="ACTIVE")
        self._calls = calls

    async def __aenter__(self):
        self._calls.append("enter")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._calls.append("exit")

    async def wait_until_ready(self, timeout=None):
        self._calls.append("ready")

    async def invoke(self, data, *, timeout=None):
        self._calls.append("invoke")
        return [
            {"text": "Hello", "confidence": 0.93, "box": [[10, 10], [30, 10], [30, 30], [10, 30]]},
            {"text": "noise", "confidence": 0.11, "box": [[0, 0], [5, 0], [5, 5], [0, 5]]},
        ]


class _FakeManager:
    def __init__(self, calls: list) -> None:
        self.requests: list[tuple] = []
        self._calls = calls

    def require(self, name: str, requirer: str, *, config=None):
        self.requests.append((name, requirer, config))
        return _FakeLease(self._calls)


async def _load_ui_operator(tmp_path: Path, monkeypatch, calls: list):
    monkeypatch.setattr(pyautogui, "screenshot", lambda: Image.new("RGB", (100, 50), "white"))
    monkeypatch.setattr(pyautogui, "size", lambda: (100, 50))

    manager = PluginManager(plugins_dir=REPO_PLUGIN_DIR, state_file=str(tmp_path / "state.json"))
    manager.set_plugin_enabled("ui_operator", True)
    manager.set_plugin_config_values("ui_operator", {"OCR_MIN_CONF": 0.5})
    await manager.reload(force=True)
    plugin = manager._faust_plugins["ui_operator"]

    fake_manager = _FakeManager(calls)
    plugin_module = sys.modules[type(plugin).__module__]
    monkeypatch.setattr(plugin_module, "get_processor_manager", lambda: fake_manager)
    return plugin, fake_manager


@pytest.mark.asyncio
async def test_screen_ocr_tool_calls_ocr_processor(tmp_path, monkeypatch):
    calls: list = []
    plugin, fake_manager = await _load_ui_operator(tmp_path, monkeypatch, calls)
    tools = {spec.name: spec for spec in await plugin.register_tools(plugin.ctx)}

    payload = await tools["screenOCRTool"].tool.ainvoke({"lang_list_json": ""})

    assert fake_manager.requests == [
        ("OCR", "ui_operator", {"langs": ["ch_sim", "en"], "gpu": False})
    ]
    assert calls == ["enter", "ready", "invoke", "exit"]
    result = json.loads(payload)
    assert result == {"res": [{"id": 1, "text": "Hello", "pos": [0.2, 0.4]}]}   # min_conf=0.5 过滤掉 noise
```

- [ ] **Step 2: 运行确认失败**

Run: `.runtime/python.exe -m pytest backend/tests/test_ui_operator_ocr.py -q`
Expected: FAIL —— `AttributeError: module 'faust_plugin_ui_operator' has no attribute 'get_processor_manager'`（或 `TypeError: 'str' object can not be awaited`，因为当前工具是同步函数，`ainvoke` 走同步分支返回了字符串）

- [ ] **Step 3: 改造插件**

`backend/default_plugins/ui_operator/main.py` 四处改动：

1. 第 7-16 行 import 区：在 `from faust_backend.plugin_system import ...` 之后加

```python
from faust_backend.processors import get_processor_manager
```

2. 删除模块级 OCR 缓存（第 19-20 行）：

```python
_OCR_READER = None
_OCR_READER_LOCK = threading.Lock()
```

（`_LAST_OCR_ITEMS` / `_LAST_OCR_LOCK` 保留——它们只存归一化后的结果，仍在主进程。）

3. 删除 `_load_ocr_reader`（第 65-73 行）整个函数；`import threading` 仍需保留（`_LAST_OCR_LOCK`）。

4. `screenOCRTool`（第 200-244 行）整体替换为 async 版本：

```python
        @tool
        async def screenOCRTool(lang_list_json: str = "") -> str:
            """
            Description:
                对当前屏幕进行 OCR 识别，返回文本及归一化坐标。
            Args:
                lang_list_json (str): OCR语言列表JSON字符串，可选。示例: ["ch_sim","en"]
            Returns:
                str: JSON字符串，格式:
                     {"res":[{"id":1,"text":"Hello","pos":[0.5,0.3]}, ...]}
            """
            try:
                _apply_pyautogui_runtime_settings()
                cfg_langs = self._get_config_sync("OCR_LANGS", ["ch_sim", "en"])
                langs = _parse_langs(lang_list_json, _parse_langs(cfg_langs, ["ch_sim", "en"]))
                use_gpu = bool(self._get_config_sync("OCR_GPU", False))
                min_conf = _safe_float(self._get_config_sync("OCR_MIN_CONF", 0.3), 0.3)

                # 截图留在主进程（需要屏幕上下文），识别交给 OCR 子进程
                screenshot_array = np.array(pyautogui.screenshot())

                manager = get_processor_manager()
                async with manager.require(
                    "OCR", requirer="ui_operator", config={"langs": langs, "gpu": use_gpu}
                ) as lease:
                    await lease.wait_until_ready()
                    raw_items = await lease.invoke({"image": screenshot_array, "detail": 1})

                out_items: list[dict[str, Any]] = []
                for item in raw_items:
                    confidence = _safe_float(item.get("confidence"), 0.0)
                    if confidence < min_conf:
                        continue
                    center = _extract_center_norm_from_box(item.get("box"))
                    if center is None:
                        continue
                    out_items.append(
                        {
                            "id": len(out_items) + 1,
                            "text": str(item.get("text", "")),
                            "pos": [round(center[0], 6), round(center[1], 6)],
                        }
                    )

                _set_last_ocr_items(out_items)
                return json.dumps({"res": out_items}, ensure_ascii=False)
            except Exception as e:
                print(f"OCR execution failed: {str(e)}")
                return json.dumps({"error": f"OCR执行失败: {str(e)}"}, ensure_ascii=False)
```

（`register_tools` 末尾的 `ToolSpec(name="screenOCRTool", tool=screenOCRTool, ...)` 不变——langchain 的 `@tool` 对 async 函数生成带 coroutine 的 StructuredTool。）

- [ ] **Step 4: 运行确认通过**

Run: `.runtime/python.exe -m pytest backend/tests/test_ui_operator_ocr.py -q`
Expected: PASS（1 passed）。

顺手确认 `docs/plugins/ui-operator.md:19` 的说法仍然成立（「首次识屏时会自动下载文字识别（OCR）模型」——迁移后仍由 OCR 子进程在首次 `setup` 时下载，只是位置换到了 `~/.faustbot/models/easyocr`）；若文案里提到「需要占用主程序内存」之类已不成立的描述，一并改掉。

Run（确认插件其它工具未被破坏）: `.runtime/python.exe -m pytest backend/tests/test_quick_screen_view.py backend/tests/test_fun_plugins.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/default_plugins/ui_operator/main.py backend/tests/test_ui_operator_ocr.py
git commit -m "refactor: ui_operator 的屏幕 OCR 改走 OCR Processor"
```

---

### Task 13: 插件注册 Processor（hook + manager + 文档）

**Files:**
- Modify: `backend/faust_backend/plugin_system/hooks.py:63-66` 附近
- Modify: `backend/faust_backend/plugin_system/plugin_base.py:64-68` 附近
- Modify: `backend/faust_backend/plugin_system/manager.py:1-30`（import）、`:497-512`（卸载循环）、`:565-570`（注册工具之后）
- Modify: `backend/faust_backend/processors/manager.py`（`register_plugin_processors` / `unregister_owner`）
- Modify: `docs/plugin-api-reference.md:351` 之后（新增 `register_processors` 章节）
- Test: `backend/tests/test_processor_plugins.py`（新建）

**Interfaces:**
- Produces: hookspec `register_processors(self, ctx) -> list`；`ProcessorManager.register_plugin_processors(owner, classes) -> list[str]`（原子：任一失败则回滚本次注册）；`await ProcessorManager.unregister_owner(owner) -> list[str]`。
- Consumes: Task 3 的 registry（`register_processor` / `unregister_owner`）。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_processor_plugins.py`：

```python
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import psutil
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PLUGIN_MAIN = '''
from __future__ import annotations

from faust_backend.plugin_system import PluginManifest, hookimpl
from faust_backend.plugin_system.plugin_base import FaustPlugin
from faust_backend.processors.base import Processor


class TmpPluginProcessor(Processor):
    NAME = "TMP_PLUGIN_ECHO"
    SETUP_TIMEOUT = 20.0
    START_TIMEOUT = 20.0

    def setup(self, ctx):
        pass

    def start(self, ctx):
        ctx.log("tmp plugin processor started")

    def invoke(self, ctx, data):
        return {"echo": data, "config": dict(ctx.config)}


class BrokenProcessor:
    """不是 Processor 子类，注册必须失败。"""


class Plugin(FaustPlugin):
    manifest = PluginManifest(plugin_id="tmp_processor_plugin", name="Tmp", enabled=True)

    def register_processors(self, ctx):
        return [TmpPluginProcessor]


class BrokenPlugin(FaustPlugin):
    manifest = PluginManifest(plugin_id="tmp_broken_plugin", name="Broken", enabled=True)

    def register_processors(self, ctx):
        return [BrokenProcessor]
'''

PLUGIN_JSON = {"id": "tmp_processor_plugin", "name": "Tmp", "entry": "main.py", "enabled": True}


def _write_plugin(root: Path, plugin_id: str, source: str) -> Path:
    plugin_dir = root / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "main.py").write_text(source, encoding="utf-8")
    (plugin_dir / "plugin.json").write_text(
        json.dumps({**PLUGIN_JSON, "id": plugin_id}), encoding="utf-8"
    )
    return plugin_dir


@pytest.mark.asyncio
async def test_plugin_processor_can_be_required_and_invoked(tmp_path):
    from faust_backend.plugin_system import PluginManager
    from faust_backend.processors import get_processor_manager
    from faust_backend.processors.errors import ProcessorNotFoundError
    from faust_backend.processors.registry import get_processor

    plugins_dir = tmp_path / "plugins"
    _write_plugin(plugins_dir, "tmp_processor_plugin", PLUGIN_MAIN)

    manager = PluginManager(plugins_dir=plugins_dir, state_file=str(tmp_path / "state.json"))
    summary = await manager.reload(force=True)
    assert summary["errors"] == []

    processor_manager = get_processor_manager()
    lease = await processor_manager.startRequire("TMP_PLUGIN_ECHO", requirer="t1")
    await lease.wait_until_ready(timeout=30)
    assert await lease.invoke({"n": 1}) == {"echo": {"n": 1}, "config": {}}
    assert processor_manager.handle("TMP_PLUGIN_ECHO").entry.owner == "tmp_processor_plugin"
    pid = processor_manager.handle("TMP_PLUGIN_ECHO").pid
    lease.release()

    # 卸载：停止子进程并摘除注册，同名随后可再次注册
    removed = await processor_manager.unregister_owner("tmp_processor_plugin")
    assert removed == ["TMP_PLUGIN_ECHO"]
    await asyncio.sleep(0.2)
    assert not psutil.pid_exists(int(pid))
    with pytest.raises(ProcessorNotFoundError):
        get_processor("TMP_PLUGIN_ECHO")

    await manager.reload(force=True)
    lease = await processor_manager.startRequire("TMP_PLUGIN_ECHO", requirer="t1")
    await lease.wait_until_ready(timeout=30)
    lease.release()
    await processor_manager.stop("TMP_PLUGIN_ECHO")


@pytest.mark.asyncio
async def test_broken_plugin_processor_marks_plugin_load_failed(tmp_path):
    from faust_backend.plugin_system import PluginManager

    plugins_dir = tmp_path / "plugins"
    _write_plugin(plugins_dir, "tmp_broken_plugin", PLUGIN_MAIN)

    manager = PluginManager(plugins_dir=plugins_dir, state_file=str(tmp_path / "state.json"))
    summary = await manager.reload(force=True)

    assert [item["plugin"] for item in summary["errors"]] == ["tmp_broken_plugin"]
    assert "Processor" in summary["errors"][0]["error"]


@pytest.mark.asyncio
async def test_register_plugin_processors_rolls_back_on_conflict(tmp_path):
    from faust_backend.processors import get_processor_manager
    from faust_backend.processors.base import Processor
    from faust_backend.processors.errors import ProcessorError
    from faust_backend.processors.registry import get_processor

    class Left(Processor):
        NAME = "TMP_ROLLBACK_LEFT"

        def start(self, ctx):
            pass

        def invoke(self, ctx, data):
            return data

    class Right(Processor):
        NAME = "TMP_ROLLBACK_RIGHT"

        def start(self, ctx):
            pass

        def invoke(self, ctx, data):
            return data

    processor_manager = get_processor_manager()
    assert processor_manager.register_plugin_processors("tmp_owner", [Left, Right]) == [
        "TMP_ROLLBACK_LEFT",
        "TMP_ROLLBACK_RIGHT",
    ]

    class Clash(Processor):
        NAME = "TMP_ROLLBACK_LEFT"

        def start(self, ctx):
            pass

        def invoke(self, ctx, data):
            return data

    class New(Processor):
        NAME = "TMP_ROLLBACK_NEW"

        def start(self, ctx):
            pass

        def invoke(self, ctx, data):
            return data

    with pytest.raises(ProcessorError):
        processor_manager.register_plugin_processors("tmp_owner_2", [New, Clash])
    from faust_backend.processors.errors import ProcessorNotFoundError

    with pytest.raises(ProcessorNotFoundError):
        get_processor("TMP_ROLLBACK_NEW")          # 回滚：本次注册的都没留下
    assert get_processor("TMP_ROLLBACK_LEFT").name == "TMP_ROLLBACK_LEFT"

    await processor_manager.unregister_owner("tmp_owner")
```

（删除 `test_plugin_processor_can_be_required_and_invoked` 里未使用的 `Again` 占位类。）

- [ ] **Step 2: 运行确认失败**

Run: `.runtime/python.exe -m pytest backend/tests/test_processor_plugins.py -q`
Expected: FAIL —— `AttributeError: 'ProcessorManager' object has no attribute 'register_plugin_processors'`

- [ ] **Step 3: 实现 manager 侧注册/卸载**

在 `backend/faust_backend/processors/registry.py` 末尾追加：

```python
def unregister(name: str) -> bool:
    """摘除单个注册记录，返回是否存在（回滚用）。"""
    return _REGISTRY.pop(str(name), None) is not None
```

在 `backend/faust_backend/processors/manager.py` 的 import 区补：

```python
from .registry import RegisteredProcessor, get_processor, list_processors, register_processor, unregister
from .registry import unregister_owner as registry_unregister_owner
```

在 `ProcessorManager.stop` 之前插入：

```python
    def register_plugin_processors(self, owner: str, classes: list[type[Processor]]) -> list[str]:
        """登记插件提供的 Processor 类。

        原子语义：任一失败则把本次已登记的记录全部回滚，再抛 ``ProcessorError``
        （插件加载失败由 PluginManager 记到 reload 结果的 ``errors`` 里）。
        """
        registered: list[str] = []
        try:
            for cls in classes:
                registered.append(register_processor(cls, owner=str(owner)))
        except ProcessorError:
            for name in registered:
                unregister(name)
            raise
        return registered

    async def unregister_owner(self, owner: str) -> list[str]:
        """停止并摘除该 owner 名下的全部 Processor（忽略引用计数，写日志说明）。"""
        names = registry_unregister_owner(str(owner))
        for name in names:
            handle = self._handles.pop(name, None)
            if handle is None:
                continue
            try:
                await handle.stop(reason=f"owner {owner} 已卸载")
            except Exception as exc:  # noqa: BLE001 - 单个停止失败不阻断其余
                log.error("卸载 Processor %s 失败: %s", name, exc)
        return names
```

- [ ] **Step 4: 接上 pluggy hook 与插件管理器**

`backend/faust_backend/plugin_system/hooks.py`，在 `register_tools` hookspec 之后加：

```python
    @hookspec
    def register_processors(self, ctx: Any) -> list:
        """Return list of Processor subclasses to register with ProcessorManager."""

```

`backend/faust_backend/plugin_system/plugin_base.py`，在 `register_tools` 默认实现之后加：

```python
    @hookimpl
    def register_processors(self, ctx: PluginContext) -> list:
        return []

```

`backend/faust_backend/plugin_system/manager.py`：

1. import 区加

```python
from faust_backend.processors import get_processor_manager
```

2. `reload()` 的卸载循环：在 `except Exception: pass`（原 :509-510）之后、`self._plugins = {}` 之前插入

```python
            try:
                removed = await get_processor_manager().unregister_owner(plugin_id)
                if removed:
                    log.info("插件 %s 的 Processor 已卸载: %s", plugin_id, removed)
            except Exception as exc:  # noqa: BLE001 - 卸载失败不得中断 reload
                log.error("卸载插件 %s 的 Processor 失败: %s", plugin_id, exc)
```

3. 注册工具之后（`tools = self._normalize_tool_specs(...)` 之后）插入

```python
                if hasattr(plugin, "register_processors"):
                    processors_res = plugin.register_processors(ctx)
                    if inspect.isawaitable(processors_res):
                        processors_res = await processors_res
                    registered_processors = get_processor_manager().register_plugin_processors(
                        manifest.plugin_id, list(processors_res or [])
                    )
                    if registered_processors:
                        log.info("插件 %s 注册 Processor: %s", manifest.plugin_id, registered_processors)
```

- [ ] **Step 5: 补插件文档**

`docs/plugin-api-reference.md`：在 `register_tools` 小节之后（`#### register_middlewares` 之前）插入：

````markdown
#### `register_processors(ctx: PluginContext) -> list`

注册重计算 Processor（受管子进程）。返回 `Processor` 子类列表，框架为每个类分配名字（类的 `NAME`）并登记到 `ProcessorManager`；插件在运行时通过 `ProcessorManager.require("名字", ...)` 使用它们（详见本节末尾的调用示例）。

```python
from faust_backend.processors import Processor, ProcessorContext


class MyTextProcessor(Processor):
    NAME = "MY_TEXT"                  # 全局唯一，与内置/其它插件重名会加载失败
    DATA_DIR = None                   # None = ~/.yours/data/processors/MY_TEXT
    SETUP_TIMEOUT = 300.0
    START_TIMEOUT = 120.0
    INVOKE_TIMEOUT = 60.0
    LOG_BUFFER = 500

    def setup(self, ctx: ProcessorContext) -> None:
        """一次性初始化（可选）：下载/校验模型。有 marker 时会被跳过。"""

    def start(self, ctx: ProcessorContext) -> None:
        """每次子进程启动时执行（必填）：把模型加载进内存。"""

    def invoke(self, ctx: ProcessorContext, data):
        """一次计算（必填）。入参与返回值都要能被 pickle。"""


@hookimpl
def register_processors(self, ctx: PluginContext) -> list:
    return [MyTextProcessor]
```

运行期调用：

```python
from faust_backend.processors import get_processor_manager

manager = get_processor_manager()
async with manager.require("MY_TEXT", requirer="my_plugin", config={"langs": ["en"]}) as lease:
    await lease.wait_until_ready()
    result = await lease.invoke({"text": "hello"})
```

约束：

- Processor 只能依赖 `ctx.config`（`require(..., config=...)` 下发的快照）与 `ctx.data_dir`；**不能**使用 `PluginContext` 或插件加载器注入的运行时状态。
- 插件入口模块必须能在独立子进程中导入（自包含），重依赖（torch / easyocr 等）在 `setup`/`start` 内 import。
- 同一个名字全局唯一：与内置 Processor 或其它插件冲突会导致该插件加载失败（错误出现在 reload 结果的 `errors` 里）。
- 插件被卸载/热重载时，框架会停止并摘除该插件名下的全部 Processor。
````

- [ ] **Step 6: 运行确认通过**

Run: `.runtime/python.exe -m pytest backend/tests/test_processor_plugins.py -q`
Expected: PASS（3 passed）。

Run（确认插件系统既有测试未回归）：`.runtime/python.exe -m pytest backend/tests/test_plugin_system_new_hooks.py backend/tests/test_plugin_storage.py backend/tests/test_fun_plugins.py -q`
Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add backend/faust_backend/plugin_system/hooks.py backend/faust_backend/plugin_system/plugin_base.py backend/faust_backend/plugin_system/manager.py backend/faust_backend/processors/manager.py backend/faust_backend/processors/registry.py backend/tests/test_processor_plugins.py docs/plugin-api-reference.md
git commit -m "feat: 插件可注册 Processor（register_processors hook）"
```

---

### Task 14: admin 路由与后端接线

**Files:**
- Create: `backend/faust_backend/processors/admin_api.py`
- Modify: `backend/main.py:40-52,91-116`
- Test: `backend/tests/test_processor_admin_api.py`（新建）

**Interfaces:**
- Produces: `GET /faust/admin/processors`、`GET /faust/admin/processors/{name}`、`POST /faust/admin/processors/{name}/stop`、`POST /faust/admin/processors/prune`（`timeout`、`whitelist` 查询参数，逗号分隔）。
- Consumes: Task 5-8 的 manager API、Task 8 的 `PruneReport`。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_processor_admin_api.py`：

```python
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faust_backend.processors.errors import ProcessorNotFoundError


class _StubHandle:
    def __init__(self, name: str, state: str = "ACTIVE") -> None:
        self.name = name
        self.state = state

    def status(self) -> dict:
        return {
            "name": self.name,
            "owner": "builtin",
            "state": self.state,
            "refcount": 1,
            "pid": 1234,
            "last_error": None,
        }

    async def get_log(self, level=None, limit=None):
        return [{"level": "ERROR", "message": "示例日志"}]


class _StubManager:
    def __init__(self) -> None:
        self.stopped: list[str] = []
        self.pruned: list[tuple] = []

    def status(self) -> list[dict]:
        return [_StubHandle("VAD").status()]

    def handle(self, name: str) -> _StubHandle:
        if name != "VAD":
            raise ProcessorNotFoundError(name)
        return _StubHandle("VAD")

    async def stop(self, name: str) -> None:
        if name != "VAD":
            raise ProcessorNotFoundError(name)
        self.stopped.append(name)

    async def prune(self, timeout: float = 37.0, whitelist=None):
        from faust_backend.processors.manager import PruneReport

        if whitelist:
            for name in whitelist:
                if name != "VAD":
                    raise ProcessorNotFoundError(name)
        self.pruned.append((timeout, whitelist))
        return PruneReport(timeout=timeout, items=[{"name": "VAD", "action": "stopped", "reason": "idle"}])

    def handle_names(self) -> list[str]:
        return ["VAD"]


@pytest.fixture
def client(monkeypatch):
    import faust_backend.processors.admin_api as admin_api

    stub = _StubManager()
    monkeypatch.setattr(admin_api, "get_processor_manager", lambda: stub)
    monkeypatch.setattr(admin_api.conf, "PROCESSOR_IDLE_TIMEOUT", 300.0)

    app = FastAPI()
    app.include_router(admin_api.router)
    with TestClient(app) as test_client:
        yield test_client, stub


def test_list_processors(client):
    test_client, _stub = client
    payload = test_client.get("/faust/admin/processors").json()
    assert payload["status"] == "ok"
    assert [item["name"] for item in payload["items"]] == ["VAD"]
    assert "logs" not in payload["items"][0]


def test_list_processors_with_log(client):
    test_client, _stub = client
    payload = test_client.get("/faust/admin/processors?include_log=true").json()
    assert payload["items"][0]["logs"] == [{"level": "ERROR", "message": "示例日志"}]


def test_get_single_processor_and_404(client):
    test_client, _stub = client
    payload = test_client.get("/faust/admin/processors/VAD").json()
    assert payload["item"]["name"] == "VAD"
    assert payload["item"]["logs"][0]["message"] == "示例日志"
    assert test_client.get("/faust/admin/processors/NOPE").status_code == 404


def test_stop_endpoint(client):
    test_client, stub = client
    assert test_client.post("/faust/admin/processors/VAD/stop").json()["status"] == "ok"
    assert stub.stopped == ["VAD"]
    assert test_client.post("/faust/admin/processors/NOPE/stop").status_code == 404


def test_prune_endpoint(client):
    test_client, stub = client
    payload = test_client.post("/faust/admin/processors/prune?timeout=0&whitelist=VAD").json()
    assert payload["items"][0]["action"] == "stopped"
    assert stub.pruned == [(0.0, ["VAD"])]

    payload = test_client.post("/faust/admin/processors/prune").json()
    assert payload["timeout"] == 300.0                 # 缺省取配置里的 PROCESSOR_IDLE_TIMEOUT
    assert stub.pruned[-1] == (300.0, None)

    assert test_client.post("/faust/admin/processors/prune?whitelist=NOPE").status_code == 404
```

- [ ] **Step 2: 运行确认失败**

Run: `.runtime/python.exe -m pytest backend/tests/test_processor_admin_api.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'faust_backend.processors.admin_api'`

- [ ] **Step 3: 实现 admin 路由**

`backend/faust_backend/processors/admin_api.py`：

```python
"""Processor 管理接口（由 main.py 注册，风格对齐 routes/admin_services.py）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

import faust_backend.config_loader as conf
from faust_backend.logger import get_logger

from .errors import ProcessorNotFoundError
from .manager import get_processor_manager

log = get_logger("faust.processor.admin")

router = APIRouter(tags=["processors"])
router.description = "重计算子进程（Processor）：列出/查看/强制停止/空闲回收"


@router.get("/faust/admin/processors")
async def processors_list(include_log: bool = False):
    """全部 Processor 状态快照。"""
    manager = get_processor_manager()
    items = manager.status()
    if include_log:
        for item in items:
            item["logs"] = await manager.handle(item["name"]).get_log(limit=50)
    return {"status": "ok", "items": items}


@router.get("/faust/admin/processors/{name}")
async def processors_get(name: str, include_log: bool = True):
    """单个 Processor 状态 + 日志尾巴。"""
    try:
        handle = get_processor_manager().handle(name)
    except ProcessorNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    item = handle.status()
    if include_log:
        item["logs"] = await handle.get_log(limit=50)
    return {"status": "ok", "item": item}


@router.post("/faust/admin/processors/{name}/stop")
async def processors_stop(name: str):
    """强制停止（忽略引用计数，manager 会记日志说明原因）。"""
    try:
        await get_processor_manager().stop(name)
    except ProcessorNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "name": name}


@router.post("/faust/admin/processors/prune")
async def processors_prune(
    timeout: float | None = Query(default=None, description="空闲秒数阈值，缺省用 PROCESSOR_IDLE_TIMEOUT"),
    whitelist: str | None = Query(default=None, description="逗号分隔的 Processor 名字；缺省处理全部"),
):
    """回收空闲 Processor；返回每项的处理结果。"""
    manager = get_processor_manager()
    names = [item.strip() for item in whitelist.split(",") if item.strip()] if whitelist else None
    idle_timeout = float(timeout) if timeout is not None else float(conf.PROCESSOR_IDLE_TIMEOUT or 0)
    try:
        report = await manager.prune(timeout=idle_timeout, whitelist=names)
    except ProcessorNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", **report.as_dict()}
```

- [ ] **Step 4: 在 `main.py` 注册路由**

`backend/main.py`：

1. admin 路由 import 区加一行（放在 `from faust_backend.routes.admin_providers import router as admin_providers_router` 之后）：

```python
from faust_backend.processors.admin_api import router as processors_router
```

2. `routers` 列表里 `admin_providers_router,` 之后加：

```python
    processors_router,
```

- [ ] **Step 5: 运行确认通过**

Run: `.runtime/python.exe -m pytest backend/tests/test_processor_admin_api.py -q`
Expected: PASS（5 passed）。

Run（确认 main.py 能 import 且路由已挂上）:
`.runtime/python.exe -c "import sys; sys.path.insert(0, 'backend'); from faust_backend.processors.admin_api import router; print(sorted({r.path for r in router.routes}))"`（cwd=仓库根）
Expected: 打印 `['/faust/admin/processors', '/faust/admin/processors/prune', '/faust/admin/processors/{name}', '/faust/admin/processors/{name}/stop']`。
（`main.py` 里 `app.include_router` 的接线由 Task 15 起后端实测覆盖——`import main` 会顺带启动后台服务，不适合当检查手段。）

- [ ] **Step 6: Commit**

```bash
git add backend/faust_backend/processors/admin_api.py backend/main.py backend/tests/test_processor_admin_api.py
git commit -m "feat: Processor admin 接口与后端路由注册"
```

---

### Task 15: 真机验证与收尾

**Files:**
- Create（临时，用完删除）: `backend/_processor_smoke.py`
- Modify: `docs/plan-processor.md`（把本 Task 的验收结论追加到文末「验收记录」）

**Interfaces:**
- Consumes: 前面全部 Task 的产物。

- [ ] **Step 1: 启动后端**

用 hub 启动（不要用 bash 前台阻塞）：

```
hub op="start" name="faust-backend" application=".runtime/python.exe" args=["backend/main.py"] cwd="D:/dev/faustbot/faust" ready={"log": "FAUST 后端主服务已启动", "port": 13900, "timeout": 120}
```

Expected: ready 条件（日志行 + 13900 端口）通过。若启动失败，先把日志看完再修，不要继续。

- [ ] **Step 2: 真机冒烟脚本**

创建 `backend/_processor_smoke.py`：

```python
"""一次性真机冒烟：VAD WS 概率区分度 / admin 接口 / OCR 识别 / 无残留进程。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import numpy as np
import psutil
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE = "http://127.0.0.1:13900"
WS_URL = "ws://127.0.0.1:13900/faust/audio/ws/vad"
WORKER_MARKER = "faust_backend.processors.worker"


def _worker_processes() -> list[psutil.Process]:
    found = []
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if WORKER_MARKER in cmdline:
            found.append(proc)
    return found


async def _vad_probe(client: httpx.AsyncClient) -> dict:
    silence = [np.zeros(512, dtype=np.float32).tobytes() for _ in range(30)]
    rng = np.random.default_rng(0)
    noise = [(rng.standard_normal(512) * 0.35).astype(np.float32).tobytes() for _ in range(30)]

    async with websockets.connect(WS_URL, max_size=None) as ws:
        probabilities = {"silence": [], "noise": []}
        for label, frames in (("silence", silence), ("noise", noise)):
            for frame in frames:
                await ws.send(frame)
                reply = json.loads(await asyncio.wait_for(ws.recv(), 30))
                assert "error" not in reply, reply
                probabilities[label].append(float(reply["probability"]))
        status_during = (await client.get(f"{BASE}/faust/admin/processors/VAD")).json()["item"]

    status_after = (await client.get(f"{BASE}/faust/admin/processors/VAD")).json()["item"]
    return {
        "silence_mean": float(np.mean(probabilities["silence"])),
        "noise_mean": float(np.mean(probabilities["noise"])),
        "during": {"state": status_during["state"], "refcount": status_during["refcount"], "pid": status_during["pid"]},
        "after": {"state": status_after["state"], "refcount": status_after["refcount"]},
    }


async def _ocr_probe() -> dict:
    from faust_backend.processors import get_processor_manager

    import pyautogui

    manager = get_processor_manager()
    lease = await manager.startRequire("OCR", requirer="smoke", config={"langs": ["ch_sim", "en"], "gpu": False})
    try:
        await lease.wait_until_ready()
        image = np.array(pyautogui.screenshot())
        items = await lease.invoke({"image": image, "detail": 1})
    finally:
        lease.release()
    await manager.stop("OCR")
    return {"items": len(items), "sample": [item["text"] for item in items[:5]]}


async def main() -> int:
    async with httpx.AsyncClient(timeout=180) as client:
        listed = (await client.get(f"{BASE}/faust/admin/processors")).json()
        names = [item["name"] for item in listed["items"]]
        assert {"VAD", "OCR"} <= set(names), names

        vad = await _vad_probe(client)
        assert vad["during"]["state"] == "ACTIVE", vad
        assert vad["during"]["refcount"] == 1, vad
        assert vad["after"]["refcount"] == 0, vad
        assert vad["noise_mean"] > vad["silence_mean"] + 0.3, vad

        pruned = (await client.post(f"{BASE}/faust/admin/processors/prune?timeout=0&whitelist=VAD")).json()
        assert pruned["items"][0]["action"] == "stopped", pruned

        ocr = await _ocr_probe()
        assert ocr["items"] > 0, ocr

    print(json.dumps({"vad": vad, "prune": pruned, "ocr": ocr, "workers_after": len(_worker_processes())}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

Run（cwd=`backend`）: `.runtime/python.exe _processor_smoke.py`
Expected: 打印 JSON，其中
- `vad.silence_mean` < 0.3、`vad.noise_mean` > 0.6（白噪明显高于静音）；
- `vad.during.state == "ACTIVE"`、`vad.during.pid` 是真实子进程 pid、`vad.after.refcount == 0`；
- `prune` 白名单项 `action == "stopped"`；
- `ocr.items > 0`（首次会下载 easyocr 模型，可能耗时数分钟）；
- `workers_after == 0`（VAD 已回收、OCR 被显式 stop）。

- [ ] **Step 3: 前端联动确认（人工）**

1. 保持后端运行，启动前端（`frontend/start.bat`）。
2. 打开麦克风（点「开始监听」），确认右上角 VAD 概率条随说话跳动、静音回落（说明 `/faust/audio/ws/vad` 经 lease 通路工作）。
3. 观察后端日志：`faust.processor.VAD` 子树下应出现 worker 的 `start`/`stop` 日志（说明 `ctx.log` 已接入统一日志树）。
4. 停止监听 → `GET /faust/admin/processors` 的 `refcount` 回到 0。
5. 启用 `ui_operator` 插件并在 Configer 里打开，向 Agent 说「看一下屏幕上有什么」触发的 OCR 工具调用，确认返回识别结果（`screenOCRTool`）。

记录结果（哪一步通过、哪一步失败与失败原因）。

- [ ] **Step 4: 关闭后端并确认无残留 worker**

```
hub op="stop" name="faust-backend"
```

随后检查（cwd=`backend`）:
`.runtime/python.exe -c "import psutil; print([p.pid for p in psutil.process_iter(['cmdline']) if 'worker.py' in ' '.join(p.info.get('cmdline') or []) and 'processors' in ' '.join(p.info.get('cmdline') or [])])"`

Expected: 打印 `[]`。

- [ ] **Step 5: 全量测试 + 清理**

Run: `.runtime/python.exe -m pytest backend/tests -q`
Expected: 全部 PASS（新增测试 + 既有测试无回归）。

清理：删除临时脚本 `backend/_processor_smoke.py`。

```bash
rm backend/_processor_smoke.py
git add -A
git commit -m "chore: Processor 机制真机验证完成"
```

- [ ] **Step 6: 验收对照（逐条勾）**

| 规格 §21 验收标准 | 证据 |
| --- | --- |
| 1. `Processor` + `ProcessorManager` 提供 §9 API | Task 5-8 测试全绿；`backend/tests/test_processor_manager.py` |
| 2. VAD 与 OCR 都在子进程，主进程不再加载 torch/easyocr | Task 15 Step 2 的 `vad.during.pid` 是子进程；`grep -rn "import easyocr" backend/faust_backend backend/default_plugins` 只剩 `processors/builtin/ocr.py` 钩子内一处 |
| 3. prune 与后台回收生效 | `test_prune_*` + Task 15 Step 2 的 admin prune |
| 4. `backend/tests` 全量通过 | Task 15 Step 5 |
| 5. 后端关闭后无残留 worker | Task 15 Step 4 |

---

## 计划编写期的验证记录

本计划的代码与测试在编写时已被**逐字执行验证**过一次（不修改仓库：把计划里的代码块拼装成临时包 `faust_backend/`，用 `.runtime/python.exe` 跑计划里的测试）：

| 项 | 做法 | 结果 |
| --- | --- | --- |
| 语法/导入 | 抽取全部 ```python 代码块做 `compile()`；把完整模块（errors/protocol/registry/base/worker/manager/builtin/builtin.vad/builtin.ocr/admin_api/download_vad）拼进临时包后真实 `import` | 完整模块 40/40 通过；缩进片段（方法体/插入块）按预期不是独立可编译单元 |
| 自动化测试 | 把计划里 `backend/tests/test_processor_manager.py`、`test_processor_admin_api.py`、`test_vad_ws_lease.py` 的代码块按 Task 顺序拼成文件，在临时包上跑 pytest | **49 passed**（manager/protocol/registry/base/vad/ocr 39 + admin 5 + WS 路由 5） |
| 端到端生命周期 | 临时驱动脚本：startRequire → wait_until_ready → invoke → 超时回收 → 取消计数 → 崩溃重启 → prune → shutdown | FIFO 顺序严格串行；超时 `ProcessorTimeoutError` 且子进程被回收；崩溃时在途 `ProcessorCrashedError`、排队 `ProcessorNotReadyError`；重启后调用正常；`shutdown` 后无存活 pid |
| 插件注册回滚 | 计划里 `test_register_plugin_processors_rolls_back_on_conflict` | 通过（重名冲突时本次注册全部回滚） |

编写期发现并已修进本计划的问题（都验证过修复）：

1. `python -m faust_backend.processors.worker` 在 `.runtime` 下**不可能成功**（`._pth` 忽略 `PYTHONPATH`、不加 cwd）→ 改为脚本路径启动 + `worker.py` 自举。
2. 内置 Processor 从未被注册（只声明了 `NAME`）→ 加 `@processor("VAD")` / `@processor("OCR")`，并让 `manager.py` import `builtin` 触发注册。
3. 插件/测试的 `.py` 模块在子进程里没有登记代码 → 加 `_resolve_processor_class`。
4. 握手失败时看不到子进程输出 → 加 `_drain_child_output`（terminate 后按行读，不能等 EOF）。
5. `idle_since` 混用 `loop.time()` 与 `time.time()`，导致 prune 显示 `idle 1790338517s` → 统一 `time.time()`。
6. 崩溃/超时后 `last_error` 被「worker 连接中断」覆盖 → `_on_worker_gone` 改为 async（等 OS 回收拿退出码）并**追加**原因，例如 `invoke 超时（0.7s）；worker exited with code 1`。
7. 崩溃收尾与新启动竞态（旧 teardown 会取消新 worker 的 reader task）→ `_run_start` 先等 `_cleanup_task` 结束。
8. 路由与测试的 3 处行为/期望不一致：WS 改为**连接即持有引用**（否则「连接后立刻断开」不会释放任何引用）、`startRequire` 不等就绪（测试需显式 `wait_until_ready`）、`prune_loop` 自行吞掉 `CancelledError`（`await task` 不抛）。

尚未在本轮执行验证的部分（属于 Task 15 的范围，必须在实现时真机跑）：

- `routes/audio.py` 的改动片段是在临时模块里验证的，尚未与真实文件拼接运行。
- `ui_operator` 的 `screenOCRTool` 改造、`plugin_system` 的 `register_processors` 接线（依赖真实插件系统与 `PluginManager`）。
- 真实 VAD/OCR 子进程（torch/easyocr 模型加载）与 admin HTTP 端到端。

## 验收记录

执行时间：2026-09-26，分支 `dev`（禁止 main）。基线：`backend/tests` 全量 **644 passed**；执行后全量 **701 passed**（+57）。

| Task | 状态 | 备注 |
| --- | --- | --- |
| 1-4 基础层 | ✅ 通过 | 2b0c7a7 / b29685b / 43c81b7 / 38830fc；测试 1→4→5→9 |
| 5-8 manager | ✅ 通过 | 72b4ec2（16）/ cdd6667（20）/ 79e0965（28）/ 910f11d（32）；`-k crash -s` 可见 `worker exited with code 7` |
| 9-10 VAD | ✅ 通过 | 9c5d315 / 9d56cfa；silero 缓存目录未被改动（79 文件 sha256 前后一致） |
| 11-12 OCR | ✅ 通过 | 32e143c / 8683814 |
| 13 插件注册 | ✅ 通过 | 6e49a78 |
| 14 admin 路由 | ✅ 通过 | 63d3390 |
| 15 真机验证 | ✅ 通过（前端人工步骤除外，见下） | 见下方真机数据 |

### 审查修复（独立 review 发现，逐条复现后修复）

| 编号 | 缺陷 | 状态 | 证据 |
| --- | --- | --- | --- |
| 1 | `_pump` 下发失败只记 DEBUG 且不落 future → 调用方永久挂住（违反「错误不静默」） | ✅ 6f68e11 | 新回归测试 `test_send_failure_resolves_caller` 修复前 15s 超时 |
| 2 | `_fail_pending` 只失败 `_pending`，排队中的 job 被 `_teardown` 丢弃 → 调用方永久挂住 | ✅ 6f68e11 | 新回归测试 `test_stop_fails_jobs_queued_behind_inflight`；单独回退该 hunk 复现真实挂死 |
| 3 | `acquire_lease` 的 config 冲突判定基于 state：STOPPED 但仍有引用时静默改掉 `config` | ✅ 6f68e11 | 新回归测试 `test_config_mismatch_raises_while_referenced_even_if_stopped`（修复前未抛异常） |
| 4 | `_on_start_done` 不 `_notify()` → 启动失败时最后一次状态迁移早于启动任务结束，`wait_until_ready(timeout=None)`（VAD/OCR 生产调用方式）**永久挂住** | ✅ e547adc | 真机复现：OCR setup 失败后等待者挂住 ≥120s；修复后 6s 内抛 `ProcessorStartError`。回归测试 `test_wait_until_ready_wakes_when_start_task_finishes` 修复前 TimeoutError |

复审（ReviewFix）：三项均 RESOLVED，Quality approved，0 finding。

### 与计划的偏差（均已在执行中裁定，实施以本节为准）

1. **VAD 区分度不能用白噪衡量**：silero-vad 是语音检测器，白噪不是语音。实测 silence_mean 0.0035 / noise_mean 0.0187，计划要求的 `noise_mean > silence_mean + 0.3` 不可能成立。改用仓库自带语音样本 `backend/voices/neuro.wav`（32kHz mono 抽成 16kHz）。
2. **Task 13 测试拆成两份插件源码**：`PluginManager._create_plugin_instance` 只解析 `module.get_plugin()` / `module.Plugin`，计划用同一份 `PLUGIN_MAIN` 同时承载好/坏插件时，坏插件永远不会被实例化，断言无法成立。改为 `PLUGIN_MAIN` / `BROKEN_PLUGIN_MAIN`（共享 `_PLUGIN_COMMON`），断言逐字保留。
3. **Task 12 测试的两行宿主代码按仓库真实契约修正**：`manager._plugins["ui_operator"]["plugin"]`（`_faust_plugins` 只收 `FaustPlugin` 实例，而 ui_operator 是老式 `class Plugin`）与同步调用 `plugin.register_tools(plugin.ctx)`（不是 async）。断言逐字保留。
4. **`ui_operator` 既有 bug**：dev 上 `main.py` 用 `@hookimpl`（第 171 行）却从未 import，插件整体加载失败（`NameError`）。属本 Task 的前置阻塞，按最小改动补进第 16 行 import。
5. **Task 9 的提交包含 `manager.py` 一行**：计划 Step 3 要求在 `manager.py` 加内置注册 import，Step 6 的 `git add` 列表却漏了该文件；为免被并发提交带走，随 Task 9 一并提交。

### Task 15 真机数据

- VAD（真实语音样本，285 帧）：`silence_mean=0.0038`、`silence_max=0.0120`、`speech_mean=0.7505`、`speech_max=0.9999`、**215/285 帧 > 0.5**；连接期间 `state=ACTIVE`、`refcount=1`、worker pid=31944；断开后 `refcount=0`。
- prune：`POST /faust/admin/processors/prune?timeout=0&whitelist=VAD` → `{"name":"VAD","action":"stopped","reason":"idle 0.0s >= 0.0s"}`。
- OCR：真实 easyocr 子进程（pid=5716，200s 内 setup+start 完成），对 1080×1920 截图识别出 **148** 个文本框，样例：`文件旧` / `编辑旧` / `选择[5)` / `查看0` / `转到[G)`。
- admin 端点：`GET /faust/admin/processors` → `['OCR','VAD']`（含 §19 审计字段 + `phase`）；`GET .../VAD` 200；`GET/POST .../NOPE` 404；`prune` 200。
- `/faust/audio/vad/status`：旧字段 `is_loaded`/`is_running`/`active_connections`/`sample_rate`/`window_size`/`threshold`/`unavailable_reason` 全部保留，新增 `state`/`last_error`/`pid`/`refcount`。
- 重依赖：导入 `faust_backend.routes.audio` + `faust_backend.runtime.lifecycle` 后 `sys.modules` 中**没有** `torch` / `easyocr`，`faust_backend.vad_runtime` 未加载。
- 优雅关闭（Ctrl-C，此时 VAD 仍被引用 refcount=1）：`停止 Processor VAD（原因: backend shutdown）` → `worker exited with code 0` → `全部 Processor 已回收`；关闭后 worker 进程 0 个。
- 全量测试：`.runtime/python.exe -m pytest backend/tests -q` → **701 passed**（3 个既有 warning 与本计划无关）。

### 未执行 / 待裁定

- **Task 15 Step 3（前端人工联动）未执行**：需要人工操作麦克风与 Electron 前端（右上角概率条、PTT 命中率、Configer 里触发 `screenOCRTool`）。本计划不改 `frontend/**`；WS 协议与 lease 通路已在真机上经由同一路径（`/faust/audio/ws/vad`）验证（见上），`error` 降级帧契约与 `frontend/app.js:2855` 的消费方式一致。
- **计划强制的恒真断言保留未改**（待人裁定）：`backend/tests/test_processor_manager.py` 的 `test_processor_defaults_and_fingerprint` 末行 `assert Processor.start is Demo.start or Demo.start is not Processor.start` 恒为真，不校验任何行为（独立 review 记为 P3，源自计划原文）。
