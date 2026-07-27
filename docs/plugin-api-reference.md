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

```python
# 创建触发器
ctx.trigger_create({
    "id": "my_event::1234567890",
    "type": "event",
    "event_name": "my_event",
    "payload": {"summary": "事件摘要"},
    "recall_description": "Agent 可读的描述",
    "lifespan": 7200,
})

# 列出所有触发器
triggers = ctx.trigger_list()

# 获取单个触发器
trigger = ctx.trigger_get("my_event::1234567890")

# 更新触发器
ctx.trigger_update("my_event::1234567890", {"payload": {"summary": "新摘要"}})

# 删除触发器
ctx.trigger_delete("my_event::1234567890")
```

### 配置管理

```python
# 注册配置 schema（list 格式，推荐）
ctx.register_config([
    {"key": "PUSH_THRESHOLD", "type": "int", "label": "推送阈值", "default": 3},
    {"key": "QUIET_START", "type": "str", "label": "静默开始", "default": "23:00"},
    {"key": "ENABLE_FEATURE", "type": "bool", "label": "启用功能", "default": True},
])

# 注册配置 schema（string 格式，旧版兼容）
ctx.register_config("""
PUSH_THRESHOLD:int:推送阈值=3
QUIET_START:str:静默开始=23:00
""")

# 读取配置
threshold = ctx.get_config("PUSH_THRESHOLD", 3)

# 写入配置
ctx.set_config("PUSH_THRESHOLD", 5)

# 列出所有配置
configs = ctx.list_configs()
```

支持的配置类型：`str`, `string`, `int`, `float`, `bool`, `json`, `text`

### 虚拟文件系统（VFS）

```python
# 读取文本
content = ctx.vfs_read_text("/plugins/my-plugin/data.md", default="")

# 写入文本
ctx.vfs_write("/plugins/my-plugin/index.md", "# My Plugin\n\n内容...")

# 写入动态内容（符号链接，每次读取时调用函数）
ctx.vfs_write_symbolic(
    "/plugins/my-plugin/dynamic.json",
    lambda path: json.dumps(get_data()),
    should_be_included_in_search=True,
)

# 删除文件
ctx.vfs_delete("/plugins/my-plugin/old.md")

# 列出目录
files = ctx.vfs_list("/plugins/my-plugin/")
```

---

## Hook 完整参考

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

配置值变更时调用。

```python
@hookimpl
def config_changed(self, key: str, old: Any, new: Any, ctx: PluginContext) -> None:
    if key == "PUSH_THRESHOLD":
        self.threshold = int(new or 3)
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

  // 注册小组件
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

  // 布局更新循环
  function updatePosition() {
    const widget = api.getWidget('my-custom-badge');
    const bounds = api.getModelBounds();
    const editMode = api.isWidgetEditMode();

    if (!bounds || !widget) return;

    // 编辑模式下显示隐藏的小组件
    if (widget.hidden && !editMode) {
      badge.style.display = 'none';
      return;
    }
    badge.style.display = '';

    // 计算位置
    const coord = widget.coord || { x: 0.1, y: 0.1 };
    const offset = widget.offset || { x: 0, y: 0 };
    const scale = widget.scale || 1;

    const anchorX = bounds.left + bounds.width * coord.x + offset.x;
    const anchorY = bounds.top + bounds.height * coord.y + offset.y;

    badge.style.left = Math.round(anchorX) + 'px';
    badge.style.top = Math.round(anchorY) + 'px';
    badge.style.transform = `translate(-50%, -50%) scale(${scale})`;
  }

  // 持续更新位置
  function loop() {
    updatePosition();
    requestAnimationFrame(loop);
  }
  loop();
})();
```

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
| POST | `/faust/admin/plugin-market/install` | 从市场安装 |
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
