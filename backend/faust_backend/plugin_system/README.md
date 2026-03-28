# Plugin System (Phase 1 + Phase 2)

该目录提供后端插件系统核心能力：

- Phase 1
  - 插件发现/加载/卸载
  - Tool 注册与控制（插件级 + 工具级启停）
  - Agent 重建时注入插件工具
- Phase 2
  - Middleware 注册与优先级排序
  - Middleware 粒度启停
  - Agent 重建时注入插件 middleware
  - Trigger System 控制（append/fire 过滤钩子）
  - 插件热重载（文件变更自动重载）

## 约定

插件目录：`backend/plugins/<plugin_id>/`

必须文件：

- `plugin.json`
- `main.py`

`main.py` 需要导出：

- `get_plugin()` 或 `Plugin` 类

插件可实现方法：

- `on_load(ctx)`
- `on_unload(ctx)`
- `register_tools(ctx)`
- `register_middlewares(ctx)`
- `health_check()`

## 管理 API

- `GET /faust/admin/plugins`
- `POST /faust/admin/plugins/reload`
- `POST /faust/admin/plugins/{plugin_id}/enable`
- `POST /faust/admin/plugins/{plugin_id}/disable`
- `POST /faust/admin/plugins/{plugin_id}/tools/{tool_name}/enable`
- `POST /faust/admin/plugins/{plugin_id}/tools/{tool_name}/disable`
- `POST /faust/admin/plugins/{plugin_id}/middlewares/{middleware_name}/enable`
- `POST /faust/admin/plugins/{plugin_id}/middlewares/{middleware_name}/disable`
- `POST /faust/admin/plugins/{plugin_id}/trigger-control/enable`
- `POST /faust/admin/plugins/{plugin_id}/trigger-control/disable`
- `GET /faust/admin/plugins/hot-reload`
- `POST /faust/admin/plugins/hot-reload/start`
- `POST /faust/admin/plugins/hot-reload/stop`

## 状态持久化

状态保存在：`backend/plugins/plugins.state.json`
