import os
import sys

if __package__ in {None, ""}:
    _BACKEND_ROOT = os.path.dirname(os.path.abspath(__file__))
    if _BACKEND_ROOT not in sys.path:
        sys.path.insert(0, _BACKEND_ROOT)

from faust_backend.core.log import get_logger

log = get_logger("faust.main")
import datetime

log.info("当前时间: %s", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

from faust_backend.core.utils import PerfTimer

_t = PerfTimer()

_t.begin("fastapi")
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
_t.end("fastapi")

# ── Runtime 生命周期 ──
_t.begin("runtime_lifecycle")
from faust_backend.app.lifespan import lifespan
from faust_backend.app.services import start_services
_t.begin("runtime_lifecycle")

_t.begin("runtime_state")
from faust_backend.agent.runtime import state
_t.end("runtime_state")

# ── 路由注册入口 ──
from faust_backend.api import register_routes

# ── FastAPI 应用 ──
app = FastAPI(
    title="FaustBot Backend Main Service",
    lifespan=lifespan,
    version="3",
    description="FaustBot 后端主服务",
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

# ── 注册路由 ──
register_routes(app)

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
from faust_backend.services.components import init_component_guard

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
