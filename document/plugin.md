# FaustBot 插件系统

---

## 1. 目标与能力范围

Faust 插件系统用于在不改动主业务代码的前提下，扩展 Agent 能力并进行运行时控制。

当前已支持：

1. **Tool 扩展与开关控制**
   - 插件可注册 Tool
   - 可按“插件级 / Tool 级”启停
2. **Middleware 注入**
   - 插件可注入 Middleware
   - 支持优先级排序与启停
3. **Trigger System 控制**
   - 插件可在 append/fire 两阶段过滤 trigger
   - 插件上下文可直接 CRUD triggers
4. **热重载**
   - 插件目录变更自动检测并重载
   - 重载后自动同步到运行时（rebuild runtime）
5. **Heartbeat 周期调用**
   - 后端每 10 秒调用一次插件心跳函数

---

## 2. 目录与文件约定

插件目录在：

- `backend/plugins/<plugin_id>/`

最小文件集：

- `plugin.json`
- `main.py`

示例：

- `backend/plugins/example_echo/plugin.json`
- `backend/plugins/example_echo/main.py`

---

## 3. 插件 Manifest（plugin.json）

示例：

```json
{
  "id": "example_echo",
  "name": "Example Echo Plugin",
  "version": "1.0.0",
  "enabled": false,
  "entry": "main.py",
  "permissions": [
    "tool:echo",
    "agent:middleware",
    "trigger:control"
  ],
  "priority": 200
}
```

字段说明：

- `id`: 插件唯一标识（建议与目录名一致）
- `name`: 显示名称
- `version`: 版本号
- `enabled`: 默认启用状态（最终以 `plugins.state.json` 为准）
- `entry`: 入口文件，默认 `main.py`
- `permissions`: 能力声明（用于管理可视化与安全审计）
- `priority`: 默认优先级（主要用于 middleware）

---

## 4. 插件入口协议

`main.py` 需要导出：

- `get_plugin()`，或
- `Plugin` 类

推荐结构：

```python
from faust_backend.plugin_system import PluginManifest, ToolSpec, MiddlewareSpec, PluginContext

class Plugin:
    manifest = PluginManifest(
        plugin_id="your_plugin",
        name="Your Plugin",
        version="1.0.0",
        enabled=False,
        permissions=["tool:xxx", "agent:middleware", "trigger:control"],
        priority=100,
    )

    def on_load(self, ctx: PluginContext) -> None:
        ...

    def on_unload(self, ctx: PluginContext) -> None:
        ...

    def register_tools(self, ctx: PluginContext):
        return []

    def register_middlewares(self, ctx: PluginContext):
        return []

    def health_check(self) -> dict:
        return {"status": "ok"}

    # 可选：触发器过滤
    def filter_trigger_append(self, trigger_payload: dict) -> dict | None:
        return trigger_payload

    def filter_trigger_fire(self, trigger_payload: dict) -> dict | None:
        return trigger_payload

    # 可选：心跳
    def Heartbeat(self, ctx: PluginContext) -> None:
        ...

def get_plugin() -> Plugin:
    return Plugin()
```

---

## 5. Tool 编写与注册

可直接返回 `ToolSpec` 列表，或返回可调用对象（会自动归一化）。

推荐使用 `ToolSpec`：

```python
from langchain.tools import tool
from faust_backend.plugin_system import ToolSpec

@tool
def my_tool(text: str) -> str:
    return f"plugin says: {text}"


def register_tools(self, ctx):
    return [
        ToolSpec(
            name="my_tool",
            tool=my_tool,
            enabled_by_default=True,
            description="示例工具"
        )
    ]
```

注意：

- Tool 名称冲突时，插件 Tool 会被跳过（避免覆盖内置）
- Tool 启停状态持久化在 `backend/plugins/plugins.state.json`

---

## 6. Middleware 编写与注册

可返回 `MiddlewareSpec` 或对象列表。

```python
from faust_backend.plugin_system import MiddlewareSpec

class MyMiddleware:
    pass


def register_middlewares(self, ctx):
    return [
        MiddlewareSpec(
            name="MyMiddleware",
            middleware=MyMiddleware(),
            priority=150,
            enabled_by_default=True,
            description="示例中间件"
        )
    ]
```

说明：

- 插件 middleware 按 `(priority, plugin_id:name)` 排序注入
- 通过后台 API 可单独开关某个 middleware

---

## 7. Trigger System 控制能力

### 7.1 过滤钩子（可选）

插件可实现：

- `filter_trigger_append(trigger_payload)`：在 trigger 写入前执行
- `filter_trigger_fire(trigger_payload)`：在 trigger 入队触发前执行

返回约定：

- 返回 `dict`：允许并可改写 payload
- 返回 `None`：拒绝/拦截 trigger

### 7.2 在插件中直接 CRUD Trigger

`PluginContext` 已内置：

- `ctx.trigger_create(payload)`
- `ctx.trigger_list()`
- `ctx.trigger_get(trigger_id)`
- `ctx.trigger_update(trigger_id, payload)`
- `ctx.trigger_delete(trigger_id)`

示例：

```python
def Heartbeat(self, ctx):
    tid = "PLUGIN_SELF_CHECK"
    existing = ctx.trigger_get(tid)
    if not existing:
        ctx.trigger_create({
            "id": tid,
            "type": "interval",
            "interval_seconds": 300,
            "recall_description": "插件自检触发器"
        })
```

---

## 8. Heartbeat（每 10 秒）

后端会每 10 秒调用一次插件心跳函数。

支持的方法名（按优先顺序）：

1. `Heartbeat(ctx)` / `Heartbeat()`
2. `heartbeat(ctx)` / `heartbeat()`
3. `on_heartbeat(ctx)` / `on_heartbeat()`

建议：

- 保持心跳逻辑轻量、幂等
- 不要在心跳里执行长阻塞任务
- 复杂任务可通过 trigger 或后台异步方式派发

---

## 参考源码

- `backend/faust_backend/plugin_system/interfaces.py`
- `backend/faust_backend/plugin_system/manager.py`
- `backend/faust_backend/trigger_manager.py`
- `backend/backend-main.py`
- `backend/plugins/example_echo/main.py`
