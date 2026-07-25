import os
import sys

if __package__ in {None, ""}:
    _BACKEND_ROOT = os.path.dirname(os.path.abspath(__file__))
    if _BACKEND_ROOT not in sys.path:
        sys.path.insert(0, _BACKEND_ROOT)

from faust_backend.logger import get_logger

log = get_logger("faust.main")
import datetime

log.info("当前时间: %s", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

from faust_backend.utils import PerfTimer

_t = PerfTimer()

_t.begin("fastapi")
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
_t.end("fastapi")

# ── Runtime 生命周期 ──
_t.begin("runtime_lifecycle")
from faust_backend.runtime.lifecycle import lifespan, start_services
_t.begin("runtime_lifecycle")

_t.begin("runtime_state")
from faust_backend.runtime import state
_t.end("runtime_state")

# ── 内部路由模块 ──
_t.begin("admin_routes")
from faust_backend.routes.admin_config import router as admin_config_router
from faust_backend.routes.admin_runtime import router as admin_runtime_router
from faust_backend.routes.admin_models import router as admin_models_router
from faust_backend.routes.admin_services import router as admin_services_router
from faust_backend.routes.admin_agents import router as admin_agents_router
from faust_backend.routes.admin_skills import router as admin_skills_router
from faust_backend.routes.admin_triggers import router as admin_triggers_router
from faust_backend.routes.admin_plugins import router as admin_plugins_router
from faust_backend.routes.admin_logs import router as admin_logs_router
from faust_backend.routes.admin_mcp import router as admin_mcp_router
_t.end("admin_routes")
_t.begin("chat_routes")
from faust_backend.routes.chat import router as chat_router
from faust_backend.routes.hil_nimble import router as hil_nimble_router
from faust_backend.routes.audio import router as audio_router
from faust_backend.routes.system import router as system_router
from faust_backend.routes.autocomplete import router as autocomplete_router
from faust_backend.routes.logger_ws import router as logger_ws_router
from faust_backend.routes.subagents import router as subagents_router
from faust_backend.routes.debugging import router as debugging_router
_t.end("chat_routes")

# ── 组件管理路由 ──
_t.begin("component")
from faust_backend.component_api import router as component_router
_t.end("component")

# ── 外部模块路由 ──
_t.begin("external_routes")
import faust_backend.araya_api as araya_api
import faust_backend.live_api as live_api
import faust_backend.update_api as update_api
import faust_backend.memory.api as memory_api
import faust_backend.edge_tts_api as edge_tts_api
_t.end("external_routes")

# ── FastAPI 应用 ──
app = FastAPI(
    title="FaustBot Backend Main Service",
    lifespan=lifespan,
    version="2.0",
    description="FaustBot 后端主服务，提供核心功能和 API 接口。",
)
state.fastapi_app = app

# ── CORS ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 注册内部路由 ──
routers = [
    admin_config_router,
    admin_runtime_router,
    admin_models_router,
    admin_services_router,
    admin_agents_router,
    admin_skills_router,
    admin_triggers_router,
    admin_plugins_router,
    admin_logs_router,
    admin_mcp_router,
    chat_router,
    hil_nimble_router,
    audio_router,
    system_router,
    autocomplete_router,
    component_router,
    logger_ws_router,
    subagents_router,
    debugging_router,
]
for r in routers:
    app.include_router(r)

# ── 注册外部路由 ──
araya_api.register_araya_routes(app)
app.include_router(live_api.router)
app.include_router(update_api.router)
app.include_router(memory_api.router)
edge_tts_api.register_edge_tts_routes(app)  # TODO: 以上几个路由应该使用router注册
# ── 插件前端静态资源挂载 ──
_t.begin("staticfiles")
from fastapi.staticfiles import StaticFiles
_t.end("staticfiles")

_plugin_frontend_dirs = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "default_plugins"),
    os.path.join(os.path.expanduser("~"), ".faustbot", "plugins"),
]
_mounted_frontend = set()
for _pf_dir in _plugin_frontend_dirs:
    if os.path.isdir(_pf_dir):
        for _pid in sorted(os.listdir(_pf_dir)):
            _pf_path = os.path.join(_pf_dir, _pid, "frontend")
            if os.path.isdir(_pf_path) and _pid not in _mounted_frontend:
                try:
                    app.mount(
                        f"/faust/plugins/{_pid}/frontend",
                        StaticFiles(directory=_pf_path),
                        name=f"plugin_frontend_{_pid}",
                    )
                    _mounted_frontend.add(_pid)
                except Exception as e:
                    log.warning("挂载插件前端资源失败 %s: %s", _pid, e)

# ── 环境与配置 ──
PORT = 13900
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# ── 启动后台服务 ──
_t.begin("services")
start_services()
from faust_backend.component_manager import init_component_guard

init_component_guard()
_t.end("services")

_t.log_pref(log, "启动耗时统计")

log.info("所有模块加载完成")

# ── 入口 ──
if __name__ == "__main__":
    log.info("FaustBot 后端主服务正在启动，端口 %d...", PORT)
    config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="info")
    state.uvicorn_server = uvicorn.Server(config)
    state.uvicorn_server.run()
