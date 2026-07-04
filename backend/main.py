from faust_backend.logger import get_logger
log = get_logger("faust.main")
import datetime
log.info("当前时间: %s", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# ── Runtime 生命周期 ──
from faust_backend.runtime.lifecycle import lifespan, start_services
from faust_backend.runtime import state

# ── 内部路由模块 ──
from faust_backend.routes.admin_config import router as admin_config_router
from faust_backend.routes.admin_runtime import router as admin_runtime_router
from faust_backend.routes.admin_models import router as admin_models_router
from faust_backend.routes.admin_services import router as admin_services_router
from faust_backend.routes.admin_agents import router as admin_agents_router
from faust_backend.routes.admin_skills import router as admin_skills_router
from faust_backend.routes.admin_triggers import router as admin_triggers_router
from faust_backend.routes.admin_plugins import router as admin_plugins_router
from faust_backend.routes.admin_logs import router as admin_logs_router
from faust_backend.routes.chat import router as chat_router
from faust_backend.routes.hil_nimble import router as hil_nimble_router
from faust_backend.routes.audio import router as audio_router
from faust_backend.routes.system import router as system_router
from faust_backend.routes.autocomplete import router as autocomplete_router
from faust_backend.routes.logger_ws import router as logger_ws_router

# ── 组件管理路由 ──
from faust_backend.component_api import router as component_router

# ── 外部模块路由 ──
import faust_backend.araya_api as araya_api
import faust_backend.live_api as live_api
import faust_backend.update_api as update_api
import faust_backend.memory.api as memory_api
import faust_backend.edge_tts_api as edge_tts_api

# ── FastAPI 应用 ──
app = FastAPI(title="FaustBot Backend Main Service",lifespan=lifespan,version="1.4")

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
    admin_config_router, admin_runtime_router, admin_models_router,
    admin_services_router, admin_agents_router, admin_skills_router,
    admin_triggers_router, admin_plugins_router, admin_logs_router,
    chat_router, hil_nimble_router, audio_router, system_router, autocomplete_router, logger_ws_router,
]
for r in routers:
    app.include_router(r)

# ── 注册外部路由 ──
araya_api.register_araya_routes(app)
app.include_router(live_api.router)
app.include_router(update_api.router)
app.include_router(memory_api.router)
edge_tts_api.register_edge_tts_routes(app)

# ── 环境与配置 ──
PORT = 13900
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# ── 启动后台服务 ──
start_services()
from faust_backend.component_manager import init_component_guard
init_component_guard()

log.info("所有库加载完成")

# ── 入口 ──
if __name__ == "__main__":
    log.info("FAUST 后端主服务正在启动，端口 %d...", PORT)
    config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="info")
    state.uvicorn_server = uvicorn.Server(config)
    state.uvicorn_server.run()