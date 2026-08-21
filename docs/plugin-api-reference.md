# FaustBot 插件系统 API Reference

本文档完整描述 FaustBot 插件系统的架构、Hook 接口、数据结构和最佳实践。

## 目录

- [架构概览](#架构概览)
- [插件目录结构](#插件目录结构)
- [plugin.json 字段](#pluginjson-字段)
- [PluginContext API](#plugincontext-api)
- [Hook 完整参考](#hook-完整参考)
- [数据结构](#数据结构)
- [前后端通信](#前后端通信)
- [UI 小组件 API](#ui-小组件-api)
- [Admin API](#admin-api)
- [默认插件示例](#默认插件示例)

---

## 架构概览

FaustBot 插件系统基于 **pluggy** 框架实现，支持两种插件风格：

| 风格 | 基类 | 特点 |
|------|------|------|
| **新风格** | `FaustPlugin` | 继承基类，自动注册 `@hookimpl`，推荐使用 |
| **旧风格** | 自定义 `Plugin` 类 | 手动实现 `on_load`/`on_unload`，兼容旧插件 |

插件加载流程：

1. `PluginManager` 扫描 `~/.faustbot/plugins/` 目录
2. 读取 `plugin.json` 清单
3. 动态导入入口文件（`importlib`）
4. 调用 `get_plugin()` 获取插件实例
5. 注册到 pluggy 管理器
6. 依次调用 `plugin_loaded` → `startup` 等生命周期 Hook

---

## 插件目录结构

```
~/.faustbot/plugins/my-plugin/
├── plugin.json              # 插件元数据（必需）
├── impl.py                  # 入口文件（推荐）
├── data/                    # 插件数据目录（可选）
│   └── data.db
└── frontend/                # 前端资源（可选）
    ├── panel.js
    ├── app-hook.js
    └── panel.css
```

---

## plugin.json 字段

```json
{
  "id": "my-plugin",
  "name": "My Plugin",
  "version": "1.0.0",
  "description": "插件功能描述",
  "author": "FaustBot",
  "homepage": "https://github.com/...",
  "enabled": true,
  "entry": "impl.py",
  "permissions": ["tool:myTool", "middleware:myMiddleware"],
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
| `homepage` | string | 否 | `""` | 项目主页 |
| `enabled` | bool | 否 | `true` | 是否启用 |
| `entry` | string | 否 | `"main.py"` | 入口文件名 |
| `permissions` | list | 否 | `[]` | 所需权限声明 |
| `priority` | int | 否 | `100` | 加载优先级（越小越先） |

---

## PluginContext API

`PluginContext` 是每个 Hook 方法接收的上下文对象，提供以下能力：

### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `plugin_id` | `str` | 插件 ID |
| `plugin_dir` | `Path` | 插件安装目录 |
| `plugin_data_dir` | `Path \| None` | 插件数据目录（`data/` 子目录） |

### 触发器管理

> 💡 **重要说明**：所有的 `PluginContext` 方法均为原生异步函数，调用时必须使用 `await` 关键字。

```python
# 创建触发器
await ctx.trigger_create({
    "id": "my_event::1234567890",
    "type": "event",
    "event_name": "my_event",
    "payload": {"summary": "事件摘要"},
    "recall_description": "Agent 可读的描述",
    "lifespan": 7200,
})

# 列出所有触发器
triggers = await ctx.trigger_list()

# 获取单个触发器
trigger = await ctx.trigger_get("my_event::1234567890")

# 更新触发器
await ctx.trigger_update("my_event::1234567890", {"payload": {"summary": "新摘要"}})

# 删除触发器
await ctx.trigger_delete("my_event::1234567890")
```

### 配置管理

```python
# 注册配置 schema（list 格式，推荐）
await ctx.register_config([
    {"key": "PUSH_THRESHOLD", "type": "int", "label": "推送阈值", "default": 3},
    {"key": "QUIET_START", "type": "str", "label": "静默开始", "default": "23:00"},
    {"key": "ENABLE_FEATURE", "type": "bool", "label": "启用功能", "default": True},
])

# 注册配置 schema（string 格式，旧版兼容）
await ctx.register_config("""
PUSH_THRESHOLD:int:推送阈值=3
QUIET_START:str:静默开始=23:00
""")

# 读取配置
threshold = await ctx.get_config("PUSH_THRESHOLD", 3)

# 写入配置
await ctx.set_config("PUSH_THRESHOLD", 5)

# 列出所有配置
configs = await ctx.list_configs()
```

支持的配置类型：`str`, `string`, `int`, `float`, `bool`, `json`, `text`

### 虚拟文件系统（VFS）

```python
# 读取文本
content = await ctx.vfs_read_text("/plugins/my-plugin/data.md", default="")

# 写入文本
await ctx.vfs_write("/plugins/my-plugin/index.md", "# My Plugin\n\n内容...")

# 写入动态内容（符号链接，每次读取时调用函数）
await ctx.vfs_write_symbolic(
    "/plugins/my-plugin/dynamic.json",
    lambda path: json.dumps(get_data()),
    should_be_included_in_search=True,
)

# 删除文件
await ctx.vfs_delete("/plugins/my-plugin/old.md")

# 列出目录
files = await ctx.vfs_list("/plugins/my-plugin/")
```

---

## Hook 完整参考

> 💡 **异步 Hook 支持**：所有 Hook 既可以是同步函数，也可以是 `async def` 原生异步函数。
> PluginManager 在分发时会自动识别并等待协程结果。
>
> - **异步上下文**（如配置保存、聊天流、工具调用、记忆读写）中，异步 Hook 会被直接 `await`；
> - **同步上下文**（如 trigger watchdog 线程、同步工具包装器）中，异步 Hook 会被自动桥接执行，插件无需感知调用方上下文；
> - 需要异步能力时（如调用 `await ctx.get_config(...)`、`await ctx.vfs_write(...)`），请将 Hook 声明为 `async def`。

### 生命周期

#### `plugin_loaded(ctx: PluginContext) -> None`

插件加载完成后调用。用于初始化全局状态、注册配置等。

```python
@hookimpl
def plugin_loaded(self, ctx: PluginContext) -> None:
    global _PLUGIN
    _PLUGIN = self
    self.ctx = ctx
    self.store = MyStore(ctx.plugin_data_dir or (ctx.plugin_dir / "data"))
```

#### `plugin_unloaded(ctx: PluginContext) -> None`

插件卸载前调用。用于清理资源、关闭连接等。

```python
@hookimpl
def plugin_unloaded(self, ctx: PluginContext) -> None:
    global _PLUGIN
    if _PLUGIN is self:
        _PLUGIN = None
```

#### `startup(ctx: PluginContext) -> None`

插件启动时调用（在 `plugin_loaded` 之后）。用于执行初始化逻辑。

```python
def startup(self, ctx: PluginContext) -> None:
    self.ctx = ctx
    ctx.register_config([...])
    ctx.vfs_write("/plugins/my-plugin.md", "...")
```

#### `heartbeat(ctx: PluginContext) -> None`

周期性心跳（约每 10 秒）。用于检查状态、触发推送等。

```python
@hookimpl
def heartbeat(self, ctx: PluginContext) -> None:
    pending = self.store.count_pending()
    if pending >= self.threshold:
        self.push_digest()
```

#### `health_check() -> dict | None`

返回健康状态。第一个非 None 结果生效。

```python
@hookimpl
def health_check(self) -> dict | None:
    return {
        "status": "ok",
        "plugin": "my-plugin",
        "items": len(self.store.list_items()),
    }
```

---

### 前端

#### `register_frontend() -> list[dict]`

声明前端 JS/CSS 资源。路径会被挂载到 `/faust/plugins/{plugin_id}/frontend/`。

```python
@hookimpl
def register_frontend(self) -> list[dict]:
    return [
        {"type": "js", "path": "/faust/plugins/my-plugin/frontend/panel.js"},
        {"type": "js", "path": "/faust/plugins/my-plugin/frontend/app-hook.js"},
        {"type": "css", "path": "/faust/plugins/my-plugin/frontend/panel.css"},
    ]
```

#### `communicate_handler(payload: dict, ctx: PluginContext) -> dict | None`

处理前端 POST 请求。详见 [前后端通信](#前后端通信)。

```python
@hookimpl
def communicate_handler(self, payload: dict, ctx: PluginContext) -> dict | None:
    action = str((payload or {}).get("action") or "").strip().lower()
    if action == "get_state":
        return {"status": "ok", "state": self.get_state()}
    return {"status": "error", "detail": f"unknown action: {action}"}
```

---

### 定时任务

#### `register_schedules() -> list[dict]`

注册定时任务。支持 `interval`（秒）或 `cron` 表达式。

```python
@hookimpl
def register_schedules(self) -> list[dict]:
    return [
        {
            "id": "my-plugin-fetch",
            "interval": 300,
            "callback": self.fetch_data,
            "description": "每5分钟拉取数据",
        },
    ]

async def fetch_data(self):
    # 异步任务
    pass
```

---

### 依赖

#### `register_pip_deps() -> list[str]`

声明 pip 依赖包。插件加载时自动安装。

```python
@hookimpl
def register_pip_deps(self) -> list[str]:
    return ["pandas>=2.0", "httpx"]
```

---

### 工具与中间件

#### `register_tools(ctx: PluginContext) -> list`

注册 Agent 可用工具。

```python
from langchain.tools import tool
from faust_backend.plugin_system import ToolSpec

@tool
def my_search(query: str) -> str:
    """搜索指定内容。"""
    return f"搜索结果: {query}"

@hookimpl
def register_tools(self, ctx: PluginContext) -> list:
    return [
        ToolSpec(
            name="mySearch",
            tool=my_search,
            enabled_by_default=True,
            description="自定义搜索工具",
        ),
    ]
```

#### `register_middlewares(ctx: PluginContext) -> list`

注册 Agent 中间件。

```python
from faust_backend.plugin_system import MiddlewareSpec

@hookimpl
def register_middlewares(self, ctx: PluginContext) -> list:
    return [
        MiddlewareSpec(
            name="my_middleware",
            middleware=MyMiddlewareClass(),
            priority=260,
            enabled_by_default=True,
            description="上下文裁剪中间件",
        ),
    ]
```

#### `tool_call_pre(name: str, args: dict, ctx: PluginContext) -> dict | None`

工具调用前拦截。返回修改后的 args 或 None 阻止调用。

```python
@hookimpl
def tool_call_pre(self, name: str, args: dict, ctx: PluginContext) -> dict | None:
    if name == "execute" and "dangerous" in args.get("command", ""):
        return None  # 阻止危险命令
    return args
```

#### `tool_call_post(name: str, args: dict, result: Any, ctx: PluginContext) -> Any`

工具调用后处理。可修改返回结果。

```python
@hookimpl
def tool_call_post(self, name: str, args: dict, result: Any, ctx: PluginContext) -> Any:
    if name == "read":
        return f"[已缓存] {result}"
    return None  # 不修改
```

---

### 消息

#### `message_received(msg: Any, history: list, ctx: PluginContext) -> str | None`

拦截/修改用户消息。返回 None 不修改。

```python
@hookimpl
def message_received(self, msg: Any, history: list, ctx: PluginContext) -> str | None:
    # 记录用户活动时间
    self.last_user_activity_ts = time.time()
    return None  # 不修改消息
```

#### `agent_event_sent(event: dict, current_history: list, ctx: PluginContext) -> dict | None`

Agent 事件发送到前端前拦截。返回 None 可抑制事件。

```python
@hookimpl
def agent_event_sent(self, event: dict, current_history: list, ctx: PluginContext) -> dict | None:
    # 抑制 EmotionInvoke 工具事件
    if event.get("tool_name") == "EmotionInvoke":
        return None
    return None  # 不修改
```

---

### LLM 请求

#### `llm_request_pre(messages: list, ctx: PluginContext) -> list | None`

每次 Agent 调用 LLM（`ainvoke` / `astream_events`）之前调用，可改写发送给模型的 messages 列表（system/user/assistant/tool 全量可见）。返回改写后的列表则替换 payload；返回 `None` 则透传。首个返回非空列表的插件生效。

用途：动态注入环境/状态文本、改写用户消息、LLM 请求观测（抓包）。

```python
@hookimpl
def llm_request_pre(self, messages: list, ctx: PluginContext) -> list | None:
    # 往 system 消息追加一段动态协议
    sys_msg = next((m for m in messages if m.get("role") == "system"), None)
    if sys_msg:
        sys_msg = {**sys_msg, "content": sys_msg.get("content", "") + "\n\n当前时间: 下午3点"}
        return [sys_msg] + [m for m in messages if m.get("role") != "system"]
    return None
```

调用点：`backend/faust_backend/runtime/lifecycle.py` 的 `invoke_agent_locked` 与 `stream_chat_agent_events`（`_apply_llm_request_pre`）。

---

### TTS

#### `tts_text(text: str, ctx: PluginContext) -> str | None`

TTS 合成前改写送入语音合成的文本（**只影响语音，不影响字幕**）。`firstresult` 语义：首个非 `None` 结果生效。

```python
@hookimpl
def tts_text(self, text: str, ctx: PluginContext) -> str | None:
    # 同声传译：把语音替换为译文，字幕保持原文
    return translate(text, target="en")
```

调用点：`backend/faust_backend/speech/tts/synthesize.py` 的 `synthesize_tts`（`_apply_tts_text_hook`）。

#### `tts_start(text: str, ctx: PluginContext) -> None`

一段语音合成完成、即将交付前端播放时调用。用于同步动画、状态标记等。

```python
@hookimpl
def tts_start(self, text: str, ctx: PluginContext) -> None:
    self.state.mark_tts_playing()
```

调用点：`synthesize_tts` 成功返回前（`_fire_tts_start`）。

#### `tts_end(text: str, ctx: PluginContext) -> None`

语音播放结束。当前后端无法感知前端播放完成，此 hook 为 API 预留；接入前端播放上报通道后启用。

---

### 记忆

#### `memory_read_pre(query: str, filters: dict | None, ctx: PluginContext) -> str | None`

记忆读取前重写查询。

```python
@hookimpl
def memory_read_pre(self, query: str, filters: dict | None, ctx: PluginContext) -> str | None:
    # 追加关键词
    return f"{query} 最近更新"
```

#### `memory_read_post(query: str, results: list, ctx: PluginContext) -> list | None`

记忆读取后重排/过滤结果。

```python
@hookimpl
def memory_read_post(self, query: str, results: list, ctx: PluginContext) -> list | None:
    # 只保留前5条
    return results[:5]
```

#### `memory_write_pre(content: str, metadata: dict | None, ctx: PluginContext) -> str | None`

记忆写入前拦截/修改内容。

```python
@hookimpl
def memory_write_pre(self, content: str, metadata: dict | None, ctx: PluginContext) -> str | None:
    # 追加标签
    if metadata is None:
        metadata = {}
    metadata["tags"] = ["my-plugin"]
    return content
```

#### `memory_write_post(content: str, metadata: dict | None, id: str, ctx: PluginContext) -> None`

记忆写入后通知。

```python
@hookimpl
def memory_write_post(self, content: str, metadata: dict | None, id: str, ctx: PluginContext) -> None:
    log.info("记忆已写入: %s", id)
```

---

### 触发器

#### `trigger_append(payload: dict, ctx: PluginContext) -> dict | None`

触发器追加前过滤/修改。

```python
@hookimpl
def trigger_append(self, payload: dict, ctx: PluginContext) -> dict | None:
    # 过滤特定类型
    if payload.get("type") == "blocked":
        return None
    return payload
```

#### `trigger_fire(payload: dict, ctx: PluginContext) -> dict | None`

触发器触发前过滤/修改。

```python
@hookimpl
def trigger_fire(self, payload: dict, ctx: PluginContext) -> dict | None:
    # 修改触发内容
    payload["payload"]["summary"] = f"[已审核] {payload['payload'].get('summary', '')}"
    return payload
```

---

### Prompt

#### `register_prompt_suffix() -> list[str]`

注入 Agent 系统提示后缀。

```python
@hookimpl
def register_prompt_suffix(self) -> list[str]:
    return [
        "\n[My Plugin]\n"
        "本插件提供 XXX 功能。\n"
        "当用户请求 XXX 时，请使用 mySearch 工具。\n"
    ]
```

---

### 配置

#### `config_changed(key: str, old: Any, new: Any, ctx: PluginContext) -> None`

配置值变更时调用。可在内部使用 `await ctx.list_configs()` 刷新配置缓存，因此推荐声明为异步实现：

```python
@hookimpl
async def config_changed(self, key: str, old: Any, new: Any, ctx: PluginContext) -> None:
    if key == "PUSH_THRESHOLD":
        self.threshold = int(new or 3)
    # 刷新插件配置缓存（同步/异步调用方均会正确等待）
    self._configs_cache = await ctx.list_configs()
```

---

## 数据结构

### ToolSpec

```python
@dataclass
class ToolSpec:
    name: str                    # 工具名称
    tool: Any                    # 工具函数或 StructuredTool
    enabled_by_default: bool = True
    description: str = ""
```

### MiddlewareSpec

```python
@dataclass
class MiddlewareSpec:
    name: str                    # 中间件名称
    middleware: Any              # 中间件实例
    priority: int = 100          # 优先级（越小越先执行）
    enabled_by_default: bool = True
    description: str = ""
```

### PluginManifest

```python
@dataclass
class PluginManifest:
    plugin_id: str
    name: str
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    homepage: str = ""
    enabled: bool = True
    entry: str = "main.py"
    permissions: list[str] = field(default_factory=list)
    priority: int = 100
```

---

## 前后端通信

### 后端接口

**POST** `/faust/plugins/{plugin_id}/communicate`

请求体：JSON 对象，通常包含 `action` 字段。

响应：JSON 对象，推荐统一格式：

```json
{"status": "ok", ...}
```

或错误：

```json
{"status": "error", "detail": "错误描述"}
```

错误语义：

| HTTP 状态码 | 说明 |
|------------|------|
| 404 | 插件不存在 |
| 503 | 插件禁用或未加载 |
| 400 | 插件未实现 `communicate_handler` |

### 前端调用

**配置窗口插件页面：**

```javascript
const result = await window.pluginUI.communicate('my-plugin', {
  action: 'get_state'
});
```

**主前端插件脚本：**

```javascript
const result = await window.faustAppUI.communicate('my-plugin', {
  action: 'get_context'
});
```

### SSE 流式通道（sse-communicate）

插件可实现 `sse_communicate_handler` hook，通过固定路由向前端持续推送事件（如任务进度）。

**后端接口：**

**GET** `/faust/plugins/{plugin_id}/sse-communicate?key=value&...`

- 查询参数以 `dict` 形式传给 hook
- Hook 必须返回一个 **async generator**，每次 `yield` 的 dict 作为一条 SSE 事件（`data: <json>`）发出
- 生成器抛出异常时发送 `event: error` 后断开
- 空闲时每 15 秒发送 keep-alive 注释保持连接
- 插件重载（reload）时所有活动 SSE 连接会被强制断开；允许多个并发连接

```python
class Plugin(FaustPlugin):
    def sse_communicate_handler(self, params: dict, ctx: PluginContext):
        async def stream():
            while True:
                yield {"progress": get_progress(params.get("job_id"))}
                await asyncio.sleep(1)
        return stream()
```

错误语义：404 插件不存在；503 插件禁用/未加载；409 未实现 hook 或返回值不是 async generator。

**前端调用（两个窗口均可）：**

```javascript
// 返回原生 EventSource
const es = window.pluginUI.communicateSSE('my-plugin', { job_id: 'abc' });
// 或主窗口: window.faustAppUI.communicateSSE(...)
es.onmessage = (event) => {
  const data = JSON.parse(event.data);
  // ...
};
es.close(); // 使用完毕后关闭
```

### 演唱通道（SING / SINGSTOP）

后端 `FrontendBridge` 提供 `sing(payload)` / `sing_stop()`，向主前端推送 `SING <json>` / `SINGSTOP` 命令。`payload` 格式：`{"title": str, "url": str, "lyrics": str | null}`。song-studio 插件的 app-hook 通过 `faustAppUI.registerCommandHandler` 消费这两个命令并负责播放、口型同步与 TTS 闪避。

主窗口 `faustAppUI` 相关辅助 API：

| 方法 | 说明 |
|------|------|
| `attachLipSyncAnalyser(analyser)` | 用任意 WebAudio `AnalyserNode` 驱动模型口型（VRM/Live2D 均支持） |
| `detachLipSyncAnalyser()` | 停止插件口型驱动并归零嘴型 |
| `holdChat(flag)` | `true` 时将 `sendToChat` 的消息排队；`false` 时按序补发（演唱期间使用） |
| `isChatHeld()` | 查询当前是否处于消息暂挂状态 |

TTS 播放开始/结束时，主窗口会派发 `faust-tts-start` / `faust-tts-end` 全局事件（`window`），插件可据此做音量闪避（ducking）。

### 设计约束

- 不要通过 `register_routes` 注册插件 Router（已废弃）
- 所有前后端交互都收敛到 `communicate` 接口
- 插件前端静态资源仍通过 `/faust/plugins/{plugin_id}/frontend/...` 提供

---

## UI 小组件 API

插件可以通过前端脚本注册自定义 UI 小组件（Widget），并在主界面上显示和交互。

### 概览

- **管理器**：`UiWidgetManager` 管理所有小组件的注册、更新和布局
- **编辑器**：`UiWidgetEditor` 提供可视化编辑模式（拖拽、缩放、属性面板）
- **持久化**：小组件配置保存在 `~/.faustbot/ui-settings.json`
- **API 暴露**：通过 `window.faustAppUI` 暴露给插件前端脚本

### 前端 API（window.faustAppUI）

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `registerWidget(spec)` | `WidgetSpec` | `Widget` | 注册新小组件 |
| `updateWidget(id, patch)` | `id: string, patch: object` | `Widget` | 更新小组件属性 |
| `getWidget(id)` | `id: string` | `Widget \| null` | 获取单个小组件 |
| `listWidgets()` | - | `Widget[]` | 列出所有小组件 |
| `isWidgetEditMode()` | - | `boolean` | 是否处于编辑模式 |
| `getModelBounds()` | - | `DOMRect \| null` | 获取模型视口边界 |
| `registerSidePanelGroup(spec)` | `GroupSpec` | `Group` | 在布景台注册配置组 |
| `setSidePanelRender(groupId, fn)` | `groupId: string, fn: function` | - | 设置组的渲染函数 |

### WidgetSpec 数据结构

```javascript
{
  id: 'my-widget',              // 必需，唯一标识符
  element: HTMLElement,          // 必需，DOM 元素
  bindingType: 'model' | 'screen',  // 绑定类型
  coord: { x: 0.5, y: 0.5 },   // 坐标（model: 相对模型比例, screen: 相对窗口比例，均为 0~1）
  offset: { x: 0, y: 0 },      // 偏移量（像素）
  scale: 1,                     // 缩放比例（最小 0.2）
  hidden: false,                // 是否隐藏
  managed: true,                // 缺省 true，由小组件管理器统一负责显隐/定位/缩放
  onLayout: (el, anchor, widget, ctx) => {},  // 可选，managed=true 时的自定义布局回调
  schema: {                     // 属性 schema（用于编辑器）
    bindingType: 'model',
    coord: 'point',
    scale: 'number',
    hidden: 'boolean',
    props: {
      dynamicBackground: 'boolean',
      fontSize: 'number',
    }
  },
  props: {                      // 自定义属性值
    dynamicBackground: true,
    fontSize: 14,
  }
}
```

### managed 与统一布局

`managed` 是小组件的核心属性，缺省为 `true`。

- **`managed: true`（推荐，默认）**：小组件管理器接管该组件的通用逻辑——每帧统一驱动定位（根据 `bindingType` + `coord` + `offset` 计算锚点）、缩放、编辑模式显隐与预览。**插件无需再自己写 `requestAnimationFrame` 布局循环，也无需手动读取 `getModelBounds()`/`getWidget()` 计算像素坐标**。默认布局效果等价于：

  ```javascript
  el.style.left = anchor.x + 'px';
  el.style.top = anchor.y + 'px';
  el.style.transform = `translate(-50%, -50%) scale(${anchor.scale})`;
  ```

- **`onLayout(el, anchor, widget, ctx)`**：当默认的居中定位不满足需求（例如需要 `transform-origin: top left`、全屏态、或额外样式）时，提供此回调即可覆盖默认布局。管理器仍负责调用时机（每帧）与隐藏判定，回调内只需写定位逻辑。`ctx` 含 `{ editMode }`。

- **`managed: false`**：管理器只记录状态、参与编辑器拖拽，但**不**自动定位/显隐。适用于自身用 CSS `position: fixed` + `top/left/right` 固定、或有独立显隐逻辑的面板（如 `subagent-panel`、`log-panel`、`vrm-config-panel`）。此时定位完全由插件/样式自理。

> 迁移提示：旧插件若自带 `updatePosition()` + `loop()` 循环，改为 `managed: true` 后应删除该循环，交由管理器统一驱动，避免重复计算与抖动。

### bindingType 说明

| 类型 | 坐标系 | 说明 |
|------|--------|------|
| `model` | 相对比例 (0-1) | 相对于模型视口的位置，`coord.x` 和 `coord.y` 是比例值 |
| `screen` | 相对比例 (0-1) | 相对于窗口尺寸的比例位置，实际像素 = `coord * window.innerWidth/innerHeight` |

### 注册小组件示例

```javascript
// 在插件的 app-hook.js 中
(function() {
  const api = window.faustAppUI;
  if (!api) return;

  // 创建 DOM 元素
  const badge = document.createElement('div');
  badge.className = 'my-custom-badge';
  badge.innerHTML = '<span>My Widget</span>';
  document.body.appendChild(badge);

  // 注册小组件 —— managed 缺省为 true，定位/显隐/缩放全部交给管理器
  api.registerWidget({
    id: 'my-custom-badge',
    element: badge,
    bindingType: 'model',
    coord: { x: 0.1, y: 0.1 },
    offset: { x: 0, y: 0 },
    scale: 1,
    hidden: false,
    schema: {
      bindingType: 'model',
      coord: 'point',
      scale: 'number',
      hidden: 'boolean',
      props: { showLabel: 'boolean' },
    },
    props: { showLabel: true },
  });

  // 无需自己写 requestAnimationFrame 布局循环：
  // 管理器每帧自动根据模型/窗口锚点定位并处理编辑模式显隐。
  // 插件只需专注业务数据的刷新，例如：
  setInterval(async () => {
    const state = await api.communicate('my-plugin', { action: 'get_state' });
    badge.querySelector('span').textContent = state.label || '';
  }, 5000);
})();
```

> 若需要覆盖默认的居中布局，可在注册时传入 `onLayout(el, anchor, widget, ctx)` 回调，见上文「managed 与统一布局」。

### 编辑模式

- 快捷键：`Ctrl+Shift+E` 切换编辑模式
- 编辑模式下可以：
  - 拖拽小组件调整位置
  - 右键打开属性面板
  - 拖拽右下角手柄调整缩放
  - 切换小组件可见性
- 配置自动保存到 `~/.faustbot/ui-settings.json`

### 后端 API

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/faust/ui-setting` | 获取所有小组件配置 |
| POST | `/faust/ui-setting` | 保存所有小组件配置 |

**请求/响应格式：**

```json
{
  "status": "ok",
  "settings": {
    "widgets": {
      "my-widget": {
        "bindingType": "model",
        "coord": { "x": 0.1, "y": 0.1 },
        "offset": { "x": 0, "y": 0 },
        "scale": 1,
        "hidden": false,
        "props": {}
      }
    }
  }
}
```

### 内置小组件

| ID | 说明 | bindingType |
|----|------|-------------|
| `quick-controller` | 快捷控制器 | model |
| `text-chat-bar` | 文本输入栏 | model |
| `asr-bubble` | 语音识别气泡 | model |
| `vrm-config-panel` | VRM 配置面板 | screen |
| `subagent-panel` | 子代理面板 | screen |
| `log-panel` | 日志面板 | screen |

### emotion-engine 示例

emotion-engine 插件注册了一个情感徽章小组件：

```javascript
api.registerWidget({
  id: 'emotion-badge',
  element: badge,
  bindingType: 'model',
  coord: { x: 0.08, y: 0.1 },
  offset: { x: 0, y: 0 },
  scale: 1,
  hidden: false,
  managed: true,
  schema: {
    bindingType: 'model',
    coord: 'point',
    scale: 'number',
    hidden: 'boolean',
    props: { dynamicBackground: 'boolean' },
  },
  props: { dynamicBackground: true },
});
```

### 布景台（LayoutSidePanel）

进入小组件编辑模式（Ctrl+Shift+E）后，窗口左侧会滑出「布景台」面板——UI 的集中设置入口。主程序与插件都可以注册配置组，每组有可点击展开/收起的标题栏。

#### GroupSpec 数据结构

```javascript
{
  id: 'my-plugin',       // 必需，组唯一标识符
  label: 'My Plugin',    // 组标题，缺省用 id
  plugin: 'my-plugin',   // 归属插件 id（元信息）
  order: 100,            // 排序，越小越靠上（系统组件为 0，缺省 100）
  collapsed: false,      // 初始是否收起
}
```

同 `id` 重复注册视为合并更新（保留展开状态与已设置的渲染函数）。

#### 渲染函数契约

```javascript
api.setSidePanelRender('my-plugin', (container, ctx) => {
  // container: 该组内容区 DOM 节点，每次渲染前已被清空，直接 append 即可
  // ctx: { groupId, refresh }  — refresh() 触发该组重新渲染
});
```

- `registerSidePanelGroup` 与 `setSidePanelRender` 的调用顺序无关，任意先后均可。
- 渲染函数在每次该组重渲染时被调用，必须每次全量重建 DOM，状态以 `getWidget()` 等实时读取为准。
- 可复用公共样式类：`lsp-row`（一行左标签右控件）、`lsp-switch` + `lsp-switch-slider`（开关）。
- 不要对布景台内的元素调用 `registerWidget`。

#### 示例：浮窗显隐开关

```javascript
if (typeof api.registerSidePanelGroup === 'function') {
  api.registerSidePanelGroup({ id: 'desktop-mood', label: 'Desktop Mood', plugin: 'desktop-mood' });
  api.setSidePanelRender('desktop-mood', (container) => {
    const widget = api.getWidget('desktop-weather');
    const row = document.createElement('div');
    row.className = 'lsp-row';
    row.append('显示天气浮窗');
    const wrap = document.createElement('label');
    wrap.className = 'lsp-switch';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = !(widget && widget.hidden);
    const slider = document.createElement('span');
    slider.className = 'lsp-switch-slider';
    input.addEventListener('change', () => {
      api.updateWidget('desktop-weather', { hidden: !input.checked });
      api.saveWidgetSettings();   // 持久化到 ~/.faustbot/ui-settings.json
    });
    wrap.append(input, slider);
    row.append(wrap);
    container.appendChild(row);
  });
}
```

---

## Admin API

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/faust/admin/plugins` | 列出所有插件 |
| POST | `/faust/admin/plugins/reload` | 强制重载所有插件 |
| POST | `/faust/admin/plugins/heartbeat` | 触发一次心跳 |
| POST | `/faust/admin/plugins/{id}/enable` | 启用插件 |
| POST | `/faust/admin/plugins/{id}/disable` | 禁用插件 |
| GET | `/faust/admin/plugins/{id}/config` | 获取插件配置 |
| POST | `/faust/admin/plugins/{id}/config` | 设置插件配置 |
| POST | `/faust/plugins/{id}/communicate` | 插件通信 |
| GET | `/faust/admin/plugin-market/catalog` | 获取插件市场目录 |
| GET | `/faust/admin/plugin-market/check-updates` | 检查已安装插件更新 |
| POST | `/faust/admin/plugin-market/sync` | 从市场安装/更新（存在即覆盖） |
| POST | `/faust/admin/plugins/install-zip` | 从 ZIP 安装 |
| POST | `/faust/admin/plugins/package-zip` | 打包插件为 ZIP |
| DELETE | `/faust/admin/plugins/{id}` | 删除插件 |

---

## 默认插件示例

### rss-watcher（新风格，完整示例）

RSS 订阅监控、摘要生成、横幅注入。

**关键模式：**
- SQLite 存储
- 定时抓取（`register_schedules`）
- 心跳推送（`heartbeat`）
- VFS 索引写入
- 触发器创建
- 记忆集成

### emotion-engine（新风格，工具示例）

情感状态追踪（6维向量）。

**关键模式：**
- `register_tools` 注册 `EmotionInvoke` 工具
- `agent_event_sent` 抑制工具事件
- `register_prompt_suffix` 注入情感状态
- `communicate_handler` 前端通信

### desktop-mood（新风格，环境感知示例）

桌面环境感知（CPU/内存/电量/窗口/天气/媒体）。

**关键模式：**
- Windows API 调用
- 规则引擎系统
- `communicate_handler` 前端通信
- `register_prompt_suffix` 注入环境上下文
