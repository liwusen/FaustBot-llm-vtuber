# Agent 自建插件技能

本技能指导 AI 如何为自己创建 FaustBot 插件，扩展功能、工具、中间件或定时任务。

## 强制工作流

在开始写任何插件代码前，必须先读取两类资料：

1. 先读取插件文档：`read("faustbot://tool_use.md")` 与 `read("sourceCode://document/plugin.md")`
2. 再读取源码：至少阅读以下一个或多个入口，再决定如何实现
    - `read("sourceCode://backend/faust_backend/plugin_system/interfaces.py")`
    - `read("sourceCode://backend/faust_backend/plugin_system/manager.py")`
    - `read("sourceCode://backend/faust_backend/plugin_system/plugin_base.py")`
    - `read("sourceCode://backend/faust_backend/routes/admin_plugins.py")`

不要在没有阅读 `sourceCode://...` 源码的情况下直接生成插件代码。

## 目录结构

```
~/.faustbot/plugins/my-plugin/
├── plugin.json    # 插件元数据
├── impl.py        # 插件代码（推荐）
├── data/          # 插件数据目录（可选）
└── frontend/      # 前端资源（可选）
    ├── panel.js
    ├── app-hook.js
    └── panel.css
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
  "entry": "impl.py",
  "permissions": [],
  "priority": 500
}
```

| 字段 | 类型 | 必需 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | string | 是 | - | 插件唯一标识符 |
| `name` | string | 是 | - | 显示名称 |
| `version` | string | 否 | `"0.1.0"` | 语义化版本号 |
| `description` | string | 否 | `""` | 功能描述 |
| `author` | string | 否 | `""` | 作者 |
| `enabled` | bool | 否 | `true` | 是否启用 |
| `entry` | string | 否 | `"main.py"` | 入口文件名 |
| `permissions` | list | 否 | `[]` | 所需权限声明 |
| `priority` | int | 否 | `100` | 加载优先级（越小越先） |

## 新风格插件（推荐）：FaustPlugin 基类

继承 `faust_backend.plugin_system.FaustPlugin`，覆写需要的 hook：

```python
from faust_backend.plugin_system import FaustPlugin, PluginContext, ToolSpec, hookimpl
from langchain.tools import tool

class Plugin(FaustPlugin):
    def startup(self, ctx: PluginContext) -> None:
        """插件启动时调用"""
        self.ctx = ctx
        # 注册配置
        ctx.register_config([
            {"key": "MY_KEY", "type": "str", "label": "我的配置", "default": "value"},
        ])
        # 写入 VFS 文档
        ctx.vfs_write("/plugins/my-plugin.md", "# My Plugin\n\nAgent 读取此文件了解插件能力")

    @hookimpl
    def plugin_loaded(self, ctx: PluginContext) -> None:
        """插件加载后初始化"""
        global _PLUGIN
        _PLUGIN = self

    @hookimpl
    def plugin_unloaded(self, ctx: PluginContext) -> None:
        """插件卸载前清理"""
        global _PLUGIN
        if _PLUGIN is self:
            _PLUGIN = None

    # 1. 注册工具
    def register_tools(self, ctx: PluginContext) -> list:
        @tool
        def myTool(param: str) -> str:
            """工具描述，Agent 会看到这个描述。"""
            return "结果"
        return [ToolSpec(name="myTool", tool=myTool)]

    # 2. 注册定时任务
    def register_schedules(self) -> list[dict]:
        return [
            {"id": "poll", "interval": 300, "callback": self.do_poll, "description": "每5分钟轮询"},
        ]

    async def do_poll(self):
        pass

    # 3. 心跳处理
    @hookimpl
    def heartbeat(self, ctx: PluginContext) -> None:
        """每10秒调用一次"""
        pass

    # 4. 前端资源
    def register_frontend(self) -> list[dict]:
        return [
            {"type": "js", "path": "/faust/plugins/my-plugin/frontend/panel.js"},
            {"type": "css", "path": "/faust/plugins/my-plugin/frontend/panel.css"},
        ]

    # 5. 前后端通信
    def communicate_handler(self, payload: dict, ctx: PluginContext) -> dict | None:
        action = payload.get("action")
        if action == "get_state":
            return {"status": "ok", "state": {}}
        return {"status": "error", "detail": f"unknown action: {action}"}

    # 6. 注入 Agent 提示
    def register_prompt_suffix(self) -> list[str]:
        return ["\n[My Plugin]\n本插件提供 XXX 功能...\n"]

    # 7. 健康检查
    def health_check(self) -> dict | None:
        return {"status": "ok", "plugin": "my-plugin"}

def get_plugin():
    return Plugin()
```

## 旧风格插件（兼容）：Plugin 类

```python
from faust_backend.plugin_system import PluginManifest, PluginContext, ToolSpec

class Plugin:
    manifest = PluginManifest(plugin_id="my-plugin", name="My Plugin")

    def on_load(self, ctx: PluginContext) -> None: pass
    def on_unload(self, ctx: PluginContext) -> None: pass
    def startup(self, ctx: PluginContext) -> None: pass

    def register_tools(self, ctx):
        return [ToolSpec(name="myTool", tool=my_function)]

    def register_middlewares(self, ctx):
        return []

    def health_check(self):
        return {"status": "ok"}

def get_plugin():
    return Plugin()
```

## 支持的 Hook 一览

### 生命周期

| Hook | 用途 |
|------|------|
| `plugin_loaded(ctx)` | 插件加载后初始化 |
| `plugin_unloaded(ctx)` | 插件卸载前清理 |
| `startup(ctx)` | 插件启动时调用 |
| `heartbeat(ctx)` | 周期性心跳（约每10秒） |
| `health_check()` | 返回健康状态 |

### 前端

| Hook | 用途 |
|------|------|
| `register_frontend()` | 声明前端 JS/CSS 资源 |
| `communicate_handler(payload, ctx)` | 处理前端 POST 请求 |

### 工具与中间件

| Hook | 用途 |
|------|------|
| `register_tools(ctx)` | 注册 Agent 可用工具 |
| `register_middlewares(ctx)` | 注册 Agent 中间件 |
| `tool_call_pre(name, args, ctx)` | 工具调用前拦截 |
| `tool_call_post(name, args, result, ctx)` | 工具调用后处理 |

### 消息与记忆

| Hook | 用途 |
|------|------|
| `message_received(msg, history, ctx)` | 拦截/修改用户消息 |
| `agent_event_sent(event, current_history, ctx)` | 拦截/抑制 Agent 事件 |
| `memory_read_pre(query, filters, ctx)` | 记忆读取前重写查询 |
| `memory_read_post(query, results, ctx)` | 记忆读取后重排结果 |
| `memory_write_pre(content, metadata, ctx)` | 记忆写入前拦截 |
| `memory_write_post(content, metadata, id, ctx)` | 记忆写入后通知 |

### 触发器

| Hook | 用途 |
|------|------|
| `trigger_append(payload, ctx)` | 触发器追加前过滤 |
| `trigger_fire(payload, ctx)` | 触发器触发前过滤 |

### Prompt 与配置

| Hook | 用途 |
|------|------|
| `register_prompt_suffix()` | 注入 Agent 系统提示后缀 |
| `config_changed(key, old, new, ctx)` | 配置值变更时调用 |

### 定时任务

| Hook | 用途 |
|------|------|
| `register_schedules()` | 注册定时任务（cron/interval） |
| `register_pip_deps()` | 声明 pip 依赖 |

## PluginContext API

### 触发器管理

```python
ctx.trigger_create(payload)      # 创建触发器
ctx.trigger_list()               # 列出所有触发器
ctx.trigger_get(trigger_id)      # 获取单个触发器
ctx.trigger_update(trigger_id, payload)  # 更新触发器
ctx.trigger_delete(trigger_id)   # 删除触发器
```

### 配置管理

```python
ctx.register_config(schema)      # 注册配置 schema
ctx.get_config(key, default)     # 读取配置
ctx.set_config(key, value)       # 写入配置
ctx.list_configs()               # 列出所有配置
```

### 虚拟文件系统（VFS）

```python
ctx.vfs_read_text(path, default)  # 读取文本
ctx.vfs_write(path, content)      # 写入文本
ctx.vfs_write_symbolic(path, func, search=True)  # 写入动态内容
ctx.vfs_delete(path)              # 删除文件
ctx.vfs_list(path)                # 列出目录
```

## 实现要求

- 优先使用新风格 `FaustPlugin` 基类，不要新建旧风格示例
- 若插件需要工具、配置、路由、前端资源，请逐项对照源码接口后再实现
- 如果要调用现有后端能力，优先通过 `sourceCode://backend/faust_backend/...` 阅读真实实现（目录可直接列出）
- 如果要暴露前端资源，必须同时检查插件管理路由与前端资源收集逻辑
- 如果要落地配置，必须先注册 schema，再读写配置值

## 安装/重载流程

```bash
# 1. 写文件
write("plugins/my-plugin/plugin.json", json_content)
write("plugins/my-plugin/impl.py", python_content)

# 2. 重载插件
execute("curl -X POST http://localhost:13900/faust/admin/plugins/reload")

# 3. 检查错误
read("plugins/my-plugin/__error__")  # 如有错误

# 4. 启用插件
execute("curl -X POST http://localhost:13900/faust/admin/plugins/my-plugin/enable")
```

## 注意事项

- 插件代码在 Agent 进程内执行，无沙箱隔离
- 工具名冲突时跳过插件工具（内置优先）
- 定时任务 interval 单位为秒
- 路由挂载路径自动加 `/faust/plugins/{plugin_id}/` 前缀
- 插件真实存储目录位于 `~/.faustbot/plugins/`，不是仓库内 `backend/plugins/`
- 写插件前必须先读取 `document/plugin.md` 和 `plugin_system` 源码，确认 API 后再编码
