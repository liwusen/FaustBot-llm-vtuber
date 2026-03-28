# Faust 插件目录

每个子目录即一个插件，例如：

- `plugin.json`：插件元信息（id/name/version/enabled/permissions/priority）
- `main.py`：插件入口，导出 `get_plugin()` 或 `Plugin` 类

插件能力（Phase 1 + 2）：

- 注册 Tools（可按插件/工具粒度启用禁用）
- 注册 Agent Middlewares（支持优先级）

状态文件：

- `plugins.state.json`（由系统自动维护）
