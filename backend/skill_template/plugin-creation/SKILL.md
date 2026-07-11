# Agent 自建插件技能

本技能指导 AI 如何为自己创建 FaustBot 插件，扩展功能、API 路由或定时任务。

## 强制工作流

在开始写任何插件代码前，必须先读取两类资料：

1. 先读取插件文档：`read("faustbot://tool_use.md")` 与 `read("faustbot://source//document/plugin.md")`
2. 再读取源码：至少阅读以下一个或多个入口，再决定如何实现
    - `read("faustbot://source//backend/faust_backend/plugin_system/interfaces.py")`
    - `read("faustbot://source//backend/faust_backend/plugin_system/manager.py")`
    - `read("faustbot://source//backend/faust_backend/plugin_system/plugin_base.py")`
    - `read("faustbot://source//backend/faust_backend/routes/admin_plugins.py")`

不要在没有阅读 `faustbot://source//...` 源码的情况下直接生成插件代码。

## 目录结构

```
~/.faustbot/plugins/my-plugin/
├── plugin.json    # 插件元数据
└── main.py        # 插件代码（FaustPlugin 子类或旧风格 Plugin 类）
```

## plugin.json 字段

```json
{
  "id": "my-plugin",
  "name": "My Plugin",
  "version": "1.0.0",
  "description": "插件功能描述",
  "author": "FaustBot",
  "enabled": true,
  "entry": "main.py",
  "permissions": [],
  "priority": 500
}
```

## 新风格插件（推荐）：FaustPlugin 基类

继承 `faust_backend.plugin_system.FaustPlugin`，覆写需要的 hook：

```python
from faust_backend.plugin_system import FaustPlugin, PluginContext
from langchain.tools import tool

class MyPlugin(FaustPlugin):
    # 1. 注册工具
    def register_tools(self, ctx: PluginContext) -> list:
        return [myTool]

    # 2. 注册 FastAPI 路由
    def register_routes(self) -> list:
        router = APIRouter()
        @router.get("/info")
        async def info():
            return {"msg": "hello"}
        return [router]

    # 3. 注册定时任务
    def register_schedules(self) -> list[dict]:
        return [
            {"id": "poll", "interval": 300, "callback": self.do_poll, "description": "每5分钟轮询"},
        ]

    async def do_poll(self):
        pass

def get_plugin():
    return MyPlugin()
```

## 旧风格插件（兼容）：Plugin 类

```python
from faust_backend.plugin_system import PluginManifest, PluginContext, ToolSpec

class Plugin:
    manifest = PluginManifest(plugin_id="my-plugin", ...)

    def register_tools(self, ctx):
        return [ToolSpec(name="myTool", tool=my_function)]

    def register_middlewares(self, ctx):
        return []

def get_plugin():
    return Plugin()
```

## 支持的 Hook 一览

| Hook | 用途 |
|------|------|
| `register_tools(ctx)` | 注册 Agent 可用工具 |
| `register_routes()` | 注册 FastAPI 路由（挂载到 /faust/plugins/{id}/） |
| `register_schedules()` | 注册定时任务（cron / interval） |
| `register_frontend()` | 声明前端 JS/CSS 资源 |
| `register_pip_deps()` | 声明 pip 依赖 |
| `plugin_loaded(ctx)` | 插件加载后初始化 |
| `plugin_unloaded(ctx)` | 插件卸载前清理 |
| `health_check()` | 返回健康状态 |
| `tool_call_pre(name, args, ctx)` | 工具调用前拦截 |
| `tool_call_post(name, args, result, ctx)` | 工具调用后处理 |
| `config_changed(key, old, new, ctx)` | 配置变更时响应 |

## 实现要求

- 优先使用新风格 `FaustPlugin` 基类，不要新建旧风格示例，除非明确要兼容旧插件。
- 若插件需要工具、配置、路由、前端资源，请逐项对照源码接口后再实现，不要凭空猜字段名。
- 如果要调用现有后端能力，优先通过 `faustbot://source//backend/faust_backend/...` 阅读真实实现，再决定是否复用。
- 如果要暴露前端资源，必须同时检查插件管理路由与前端资源收集逻辑。
- 如果要落地配置，必须先注册 schema，再读写配置值。

## 安装/重载流程

```bash
# 1. 写文件
write("plugins/my-plugin/plugin.json", json_content)
write("plugins/my-plugin/main.py", python_content)

# 2. 重载插件
execute("curl -X POST http://localhost:13900/faust/admin/plugins/reload")

# 3. 检查错误
read("plugins/my-plugin/__error__")  # 如有错误

# 4. 启用插件
execute("curl -X POST http://localhost:13900/faust/admin/plugins/example/enable")
```

## 注意事项

- 插件代码在 Agent 进程内执行，无沙箱隔离
- 工具名冲突时跳过插件工具（内置优先）
- 定时任务 interval 单位为秒
- 路由挂载路径自动加 `/faust/plugins/{plugin_id}/` 前缀
- 插件真实存储目录位于 `~/.faustbot/plugins/`，不是仓库内 `backend/plugins/`
- 写插件前必须先读取 `document/plugin.md` 和 `plugin_system` 源码，确认 API 后再编码
