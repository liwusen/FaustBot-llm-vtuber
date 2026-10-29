"""进程级后台服务的启动与心跳。

自 ``runtime/lifecycle.py`` 逐行挖出（P4 布局拆分），不重排任何一步。
由 ``app/lifespan.py`` 独占调用：agent/ 不反向依赖业务包。
"""

from __future__ import annotations

import asyncio
import time

import tqdm

import faust_backend.agent.runtime.state as state
import faust_backend.agent.trigger as trigger_manager
import faust_backend.services.manager as service_manager
import faust_backend.speech as speech
from faust_backend.core.config import args
from faust_backend.core.log import get_logger
from faust_backend.plugins.system import PluginManager

log = get_logger("faust.lifecycle")


def init_plugin_manager():
    if state.plugin_manager is None:
        state.plugin_manager = PluginManager()


def _sync_plugin_trigger_filters():
    pm = state.plugin_manager
    if pm is None:
        return
    trigger_manager.set_append_filters([pm.filter_trigger_on_append])
    trigger_manager.set_fire_filters([pm.filter_trigger_on_fire])


async def _plugin_heartbeat_loop():
    while True:
        try:
            await asyncio.sleep(10.0)
            pm = state.plugin_manager
            if pm:
                summary = await pm.heartbeat_tick()
                if summary.get("errors"):
                    log.error("插件心跳错误: %s", summary.get("errors"))
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error("插件心跳循环错误: %s", e)
            await asyncio.sleep(1.0)


def start_services():
    if not args.no_run_other_backend_services:
        log.info("正在启动后端服务...")
        for service in tqdm.tqdm(
            service_manager.get_service_keys(), desc="[main]Starting services"
        ):
            if service == "tts" and not speech.should_start_local_tts():
                log.info("跳过本地 TTS 服务（TTS_MODE 不是 gpt-sovits）")
                continue
            if service == "asr" and not speech.should_start_local_asr():
                log.info("跳过本地 ASR 服务（ASR_MODE 不是 whisper/funasr）")
                continue
            try:
                service_manager.start_service(service, wait=False)
            except Exception as e:
                log.error("启动服务 %s 失败: %s", service, e)
            time.sleep(0.5)
        log.info("其他后端服务已启动")
