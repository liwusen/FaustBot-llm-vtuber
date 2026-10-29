"""FastAPI 应用生命周期（startup / shutdown）。

自 ``runtime/lifecycle.py`` 整体挖出（P4 布局拆分）。启停**顺序**是行为敏感区：
这里逐行搬运，一步都不重排、一步都不合并。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress as contextlib_suppress

from fastapi import FastAPI

import faust_backend.agent.araya.runtime as araya_runtime
import faust_backend.agent.runtime.state as state
import faust_backend.agent.trigger as trigger_manager
import faust_backend.live.manager as blive_manager
import faust_backend.core.frontend.api as backend2frontend
import faust_backend.api.live as live_api
import faust_backend.integrations.minecraft as minecraft_client
import faust_backend.integrations.nimble as nimble
import faust_backend.services.manager as service_manager
from faust_backend.agent.runtime.rebuild import rebuild_runtime
from faust_backend.app.services import _plugin_heartbeat_loop, init_plugin_manager
from faust_backend.core.config import args
from faust_backend.core.log import get_logger
from faust_backend.processors import get_processor_manager

log = get_logger("faust.lifecycle")


async def _graceful_shutdown_task():
    log.info("正在优雅关闭...")
    await asyncio.sleep(0.1)
    if state.uvicorn_server:
        state.uvicorn_server.should_exit = True
        log.info("Uvicorn 关闭标志已设置")


async def _start_araya_runtime() -> bool:
    """启动 Araya 运行时；失败只降级 Araya 自己。

    Araya 是辅助 Agent（空闲时整理记忆）。它的模型构建 / 模板同步 / 状态读写
    出错，不应该连带跳过主 Agent 与插件的初始化：插件加载在 rebuild_runtime
    里，一旦被跳过，插件工具、hook、中间件、前端资源会整体缺席，界面上看起来
    就是"所有插件都没被加载"。
    """
    try:
        await araya_runtime.get_araya_runtime(refresh=True).startup()
        return True
    except Exception as e:
        log.warning("Araya 运行时启动失败（主 Agent 与插件继续启动）: %s", e)
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_plugin_manager()
    from faust_backend.integrations.mcp import get_mcp_manager

    mcp_manager = get_mcp_manager()

    # ── startup ──
    backend2frontend.set_main_loop(asyncio.get_running_loop())
    # Processor 的 worker 控制通道绑在主循环上：登记后插件/Agile 模块可从自己的循环调用
    get_processor_manager().bind_loop()
    araya_runtime.get_araya_runtime(refresh=True)
    # Araya 是辅助 Agent：它失败只降级自身，不能把下面的主运行时（含插件加载）带走
    await _start_araya_runtime()
    try:
        from faust_backend.memory import get_memory

        get_memory(refresh=True)
        startup_info = await rebuild_runtime(
            reset_dialog=False, no_initial_chat=bool(args.no_startup_chat)
        )
        log.info("启动运行时摘要: %s", startup_info)
    except Exception as e:
        state.agent = None
        state.set_runtime_state(ready=False, status="waiting_for_config", error=str(e))
        log.warning("启动运行时降级: %s", e)
    log.info("触发器看门狗线程正在启动...")
    trigger_manager.start_trigger_watchdog_thread()
    try:
        await nimble.restore_persistent_sessions()
    except Exception as e:
        log.warning("恢复持久化 Nimble 窗口失败: %s", e)
    try:
        await minecraft_client.ensure_started()
    except Exception as e:
        log.warning("Minecraft 桥启动时未连接: %s", e)

    if state.plugin_heartbeat_task is None:
        state.plugin_heartbeat_task = asyncio.create_task(_plugin_heartbeat_loop())
    if state.processor_prune_task is None:
        state.processor_prune_task = asyncio.create_task(get_processor_manager().prune_loop())
    live_api.set_rebuild_callback(
        lambda: rebuild_runtime(reset_dialog=False, no_initial_chat=True)
    )
    try:
        blm = blive_manager.get_blive_manager(refresh=True)
        await blm.start()
    except Exception as e:
        log.warning("B站直播客户端启动失败: %s", e)
    log.info("FAUST 后端主服务已启动")

    yield

    # ── shutdown ──
    log.info("开始关闭 Agent...")
    from faust_backend.agent.runtime import scheduler as scheduler_module

    # 调度器 worker：cancel + 有界等待，不 drain（未执行的回合直接终结）
    with contextlib_suppress(Exception):
        await scheduler_module.get_scheduler().shutdown()
    if state.subagent_manager is not None:
        try:
            await state.subagent_manager.aclose()
        except Exception as exc:
            log.warning("关闭 SubagentManager 失败: %s", exc)
        state.subagent_manager = None
    if not args.save_in_memory:
        if state.conn:
            await state.conn.commit()
            await state.conn.close()
    trigger_manager.stop_trigger_watchdog_thread()
    if state.plugin_heartbeat_task is not None:
        state.plugin_heartbeat_task.cancel()
        try:
            await state.plugin_heartbeat_task
        except Exception:
            pass
        state.plugin_heartbeat_task = None
    if state.processor_prune_task is not None:
        state.processor_prune_task.cancel()
        try:
            await state.processor_prune_task
        except Exception:
            pass
        state.processor_prune_task = None
    await get_processor_manager().shutdown()
    await araya_runtime.get_araya_runtime(refresh=True).shutdown()
    await mcp_manager.stop_all()
    trigger_manager.exitflag = True
    for service in service_manager.get_service_keys():
        try:
            log.info("正在停止服务: %s", service)
            service_manager.stop_service(service)
        except Exception as e:
            log.error("停止服务 %s 失败: %s", service, e)
    log.info("正在关闭 FAUST 后端主服务...")
