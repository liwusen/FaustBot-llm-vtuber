# FaustBot 插件系统

## 1. 当前能力（2026-03）

插件系统现在采用**插件级总开关**模型：

1. 插件可注册 Tool 与 Middleware；启用插件时整体生效，禁用时整体失效。
2. 插件可在 Trigger append/fire 两阶段做过滤。
3. 插件可通过 `PluginContext` 直接对 Trigger 做 CRUD。
4. 插件支持手动重载与 Heartbeat（后端每 10 秒调用一次）。
5. 插件支持配置注册与持久化，配置改动后可自动重载并应用运行时。
6. 插件支持多模态桥接：工具返回统一图片契约后，可由 Middleware 自动转换为 `image_url` 多模态内容块。

> 已移除：Tool/Middleware/Trigger 的细粒度手动开关 API。

## 2. 目录与约定

- 插件目录：`~/.faustbot/plugins/<plugin_id>/`
- 必需文件：`plugin.json`、`main.py`
- 插件状态与配置持久化：`~/.faustbot/plugins/plugins.state.json`

### 2.1 写插件前的源码阅读建议

如果你是通过 Agent / Skill 自动生成插件代码，建议先读取以下源码，而不是只看文档：

- `faustbot://source//backend/faust_backend/plugin_system/interfaces.py`
- `faustbot://source//backend/faust_backend/plugin_system/manager.py`
- `faustbot://source//backend/faust_backend/plugin_system/plugin_base.py`
- `faustbot://source//backend/faust_backend/routes/admin_plugins.py`

原因：这些文件定义了当前真实生效的接口、配置行为、重载流程和前端资源收集逻辑。

## 3. Manifest（plugin.json）

示例：

```json
{
  "id": "my_plugin",
  "name": "My Plugin",
  "version": "1.0.0",
  "description": "插件描述",
  "author": "Your Name",
  "homepage": "https://example.com",
  "enabled": false,
  "entry": "main.py",
  "permissions": ["tool:demo", "trigger:control"],
  "priority": 100
}
```

## 4. 插件入口协议

`main.py` 需导出 `get_plugin()` 或 `Plugin` 类。推荐结构：

```python
from faust_backend.plugin_system import PluginManifest, ToolSpec, MiddlewareSpec, PluginContext

class Plugin:
    manifest = PluginManifest(
        plugin_id="my_plugin",
        name="My Plugin",
        version="1.0.0",
        description="插件描述",
        author="Your Name",
        homepage="https://example.com",
        enabled=False,
        permissions=["tool:demo", "trigger:control"],
        priority=100,
    )

    def startup(self, ctx: PluginContext) -> None:
        # 注册插件配置（字符串格式支持 KEY:TYPE:LABEL 与 KEY:TYPE:LABEL=DEFAULT）
        ctx.register_config("""
        API_KEY:str:接口密钥=__MAIN_CONFIG__
        ENABLED:bool:是否启用=true
        RETRY:int:重试次数=3
        """)

    def register_tools(self, ctx: PluginContext):
        return []

    def register_middlewares(self, ctx: PluginContext):
        return []

    def filter_trigger_append(self, trigger_payload: dict) -> dict | None:
        return trigger_payload

    def filter_trigger_fire(self, trigger_payload: dict) -> dict | None:
        return trigger_payload

    def Heartbeat(self, ctx: PluginContext) -> None:
        ...

def get_plugin() -> Plugin:
    return Plugin()
```

## 5. PluginContext 新增配置接口

- `ctx.register_config(schema)`：注册配置结构（字符串 / dict / list）。
- `ctx.get_config(key, default=None)`：读取配置值。
- `ctx.set_config(key, value)`：写入配置值。
- `ctx.list_configs()`：读取当前插件配置字典。

支持类型：`str/string/text`、`bool`、`int`、`float`、`json`。

### 5.1 字符串 schema 语法（推荐）

每行一个字段，支持两种写法：

- `KEY:type:label`
- `KEY:type:label=default`

示例：

```text
SEARCH_ENGINE:str:SearchAPI引擎=google
ENABLE_SEARCHAPI:bool:启用SearchAPI搜索=true
RETRY:int:重试次数=3
THRESHOLD:float:阈值=0.75
EXTRA:json:扩展参数={"mode":"fast"}
```

默认值行为：

1. 注册 schema 时，若该 key 当前无值且声明了 `default`，会自动写入默认值。
2. `ctx.get_config(key, fallback)` 的优先级：`values` > schema `default` > `fallback`。
3. 配置保存只接受 schema 中声明过的 key（未知 key 会被忽略）。

说明：

- 若默认值包含 `=`，建议改用 `dict/list` schema，以避免歧义。
- `SEARCHAPI_API_KEY` 这类字段可约定特殊值（如 `__MAIN_CONFIG__`）表示继承主配置。

## 6. 多模态工具契约（mm_bridge + vision_tools）

当前推荐的多模态链路：

1. Tool 返回 JSON 字符串，`kind` 固定为 `multimodal_tool_result`。
2. `images[].url` 放置可被模型读取的 URL（`data:image/...` 或 `file:///...`）。
3. `mm_bridge` 中间件在 `before_model` 阶段将 ToolMessage 转换为 HumanMessage 多模态块（`text` + `image_url`）。

### 6.1 契约字段

最小字段：

```json
{
    "kind": "multimodal_tool_result",
    "text": "请基于这张图片完成任务",
    "images": [
        {"url": "data:image/jpeg;base64,..."}
    ]
}
```

可选字段：

```json
{
    "meta": {
        "source": "screen_capture",
        "mode": "data_url"
    }
}
```

说明：

1. `kind` 不匹配时，桥接中间件会忽略该 ToolMessage。
2. `images` 不是数组或数组内没有有效 `url` 时，不会生成多模态消息。
3. `text` 会作为第一段文本块，建议明确任务指令。

### 6.2 相关插件配置

`mm_bridge`：

- `ENABLE_MM_BRIDGE:bool:启用多模态桥接=true`
- `REMOVE_SOURCE_TOOL_MESSAGE:bool:桥接后删除源ToolMessage=false`
- `MAX_SCAN_TOOL_MESSAGES:int:每轮扫描最近ToolMessage条数=6`

`vision_tools`：

- `ENABLE_CAPTURE_SCREEN:bool:启用屏幕截图工具=true`
- `ENABLE_READ_IMAGE_FILE:bool:启用本地读图工具=true`
- `IMAGE_RETURN_MODE:str:图片返回模式(data_url/file_url)=data_url`
- `MAX_IMAGE_EDGE:int:图片最长边像素上限=1600`
- `JPEG_QUALITY:int:JPEG压缩质量=85`

### 6.3 Tool 返回示例

在工具函数中，返回值建议直接是契约 JSON 字符串：

```python
import json

def my_image_tool() -> str:
        return json.dumps(
                {
                        "kind": "multimodal_tool_result",
                        "text": "请描述图片中的关键信息",
                        "images": [{"url": "data:image/jpeg;base64,..."}],
                        "meta": {"source": "custom_tool"},
                },
                ensure_ascii=False,
        )
```

## 7. Trigger CRUD 接口（插件内）

- `ctx.trigger_create(payload)`
- `ctx.trigger_list()`
- `ctx.trigger_get(trigger_id)`
- `ctx.trigger_update(trigger_id, payload)`
- `ctx.trigger_delete(trigger_id)`

## 8. 管理 API（后端）

### 插件基础

- `GET /faust/admin/plugins`
- `POST /faust/admin/plugins/reload`
- `POST /faust/admin/plugins/{plugin_id}/enable`
- `POST /faust/admin/plugins/{plugin_id}/disable`

### 插件配置

- `GET /faust/admin/plugins/{plugin_id}/config`
- `POST /faust/admin/plugins/{plugin_id}/config`

`POST` body 示例：

```json
{
  "values": {
    "API_KEY": "xxx",
    "ENABLED": true,
    "RETRY": 3
  },
  "apply_runtime": true,
  "reset_dialog": false,
  "no_initial_chat": true
}
```

### 手动重载与心跳

- `POST /faust/admin/plugins/reload`

- `GET /faust/admin/plugins/hot-reload`
- `POST /faust/admin/plugins/hot-reload/start`
- `POST /faust/admin/plugins/hot-reload/stop`
- `POST /faust/admin/plugins/heartbeat`

> 说明：当前默认采用**手动重载**流程；`/hot-reload/*` 接口保留兼容响应，不再启用自动轮询重载。

### 本地 ZIP 安装 / 打包 / 删除

- `POST /faust/admin/plugins/install-zip`
- `POST /faust/admin/plugins/package-zip`
- `DELETE /faust/admin/plugins/{plugin_id}`

## 9. Runtime 重建行为

`rebuild_runtime` 支持参数：

- `reset_dialog: bool`
- `no_initial_chat: bool`

当 `no_initial_chat=True` 且 checkpoint DB 已存在时，会跳过初始化对话，避免重复注入“继续角色设定”消息。

## 10. 参考源码

- `backend/faust_backend/plugin_system/interfaces.py`
- `backend/faust_backend/plugin_system/manager.py`
- `backend/faust_backend/trigger_manager.py`
- `backend/backend-main.py`
