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
