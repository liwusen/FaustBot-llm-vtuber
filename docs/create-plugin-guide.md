# FaustBot 插件开发指南

本文档介绍如何开发 FaustBot 插件，包括插件可以做什么、技术栈选择、设计原则和发布流程。

## 目录

- [插件能做什么](#插件能做什么)
- [技术栈](#技术栈)
- [VFS 优先原则](#vfs-优先原则)
- [插件类型与模板](#插件类型与模板)
- [Plugin Market](#plugin-market)
- [开发流程](#开发流程)
- [最佳实践](#最佳实践)

---

## 插件能做什么

FaustBot 插件可以在以下层面扩展和修改系统：

### 1. Agent 行为

| 能力 | 说明 |
|------|------|
| 添加工具 | 为 Agent 注册新的可调用工具（如搜索、API 调用） |
| 注入 Prompt | 在 Agent 系统提示中注入插件专属指令 |
| 拦截消息 | 在用户消息到达 Agent 前进行修改或过滤 |
| 拦截工具调用 | 在工具调用前后修改参数或结果 |
| 抑制事件 | 阻止特定 Agent 事件发送到前端 |

### 2. 记忆系统

| 能力 | 说明 |
|------|------|
| 拦截记忆读取 | 重写查询、重排结果 |
| 拦截记忆写入 | 修改写入内容或元数据 |
| 写入 VFS | 将数据写入虚拟文件系统供 Agent 检索 |

### 3. 触发器系统

| 能力 | 说明 |
|------|------|
| 创建触发器 | 生成事件触发器让 Agent 自动响应 |
| 过滤触发器 | 在触发器追加或触发时进行过滤 |

### 4. 前端界面

| 能力 | 说明 |
|------|------|
| 注册 UI 小组件 | 在主界面添加自定义小组件（徽章、面板等） |
| 注入前端脚本 | 加载自定义 JS/CSS 修改前端行为 |
| 前后端通信 | 通过 `communicate` 接口与后端交互 |

### 5. 定时任务

| 能力 | 说明 |
|------|------|
| 定时执行 | 基于 interval 或 cron 定期执行回调 |
| 心跳钩子 | 每约 10 秒接收一次心跳通知 |

### 6. 配置与数据

| 能力 | 说明 |
|------|------|
| 注册配置项 | 在配置中心添加插件专属配置 |
| 持久化存储 | 使用 `data/` 目录或 VFS 存储数据 |
| 读写 Agent 文件 | 通过 VFS 读写 Agent 的 COREMEMORY.md 等文件 |

---

## 技术栈

### 后端（Python）

| 组件 | 技术 | 说明 |
|------|------|------|
| 插件框架 | pluggy | Hook 注册与调度 |
| 异步运行时 | asyncio | 异步任务、定时器 |
| HTTP 客户端 | aiohttp / requests | API 调用、数据拉取 |
| 数据存储 | SQLite / JSON | 本地数据持久化 |
| Agent 集成 | LangChain | 工具定义（`@tool` 装饰器） |
| 日志 | faust_backend.logger | 统一日志接口 |

### 前端（JavaScript）

| 组件 | 技术 | 说明 |
|------|------|------|
| 模块系统 | ES Module | `import`/`export` |
| UI 框架 | 原生 DOM API | 无框架依赖 |
| 通信 | window.api / window.faustAppUI | IPC + HTTP |
| 样式 | CSS | 自定义样式表 |

### 依赖管理

- 后端依赖：通过 `register_pip_deps()` 声明，加载时自动安装
- 前端依赖：无外部依赖，使用原生 API

---

## VFS 优先原则

### 核心规则

> **尽可能多使用 VFS 虚拟文件系统，而不是徒增工具。**

### 什么是 VFS

VFS（Virtual File System）是 FaustBot 的虚拟文件系统，使用 `faustbot://` 协议访问。插件可以通过 VFS 向 Agent 暴露数据，而无需注册额外工具。

### VFS 路径约定

```
faustbot://plugins/{plugin-id}/           # 插件主目录
faustbot://plugins/{plugin-id}/index.md   # 插件索引文档
faustbot://plugins/{plugin-id}/data.json  # 插件数据文件
```

### VFS vs 工具

| 场景 | 推荐方式 | 原因 |
|------|----------|------|
| Agent 需要读取插件数据 | VFS | Agent 已有 `read` 工具，无需新增 |
| Agent 需要搜索插件内容 | VFS | Agent 已有 `search` 工具，支持 VFS 范围 |
| Agent 需要执行操作 | 工具 | 需要自定义逻辑，无法通过文件读取实现 |
| 前端需要获取数据 | communicate | 前端专用接口 |
| 定时更新数据 | VFS + 心跳 | 定时写入 VFS，Agent 按需读取 |

### VFS 使用示例

```python
# 写入插件文档（Agent 可通过 read("faustbot://plugins/my-plugin/index.md") 读取）
ctx.vfs_write("/plugins/my-plugin/index.md", "# My Plugin\n\n功能说明...")

# 写入动态数据
ctx.vfs_write("/plugins/my-plugin/data.json", json.dumps({"items": [...]}))

# 写入符号链接（每次读取时动态生成）
ctx.vfs_write_symbolic(
    "/plugins/my-plugin/dynamic.md",
    lambda path: f"# 动态数据\n\n更新时间: {time.time()}"
)

# Agent 读取时自动支持
# Agent: read("faustbot://plugins/my-plugin/index.md")
# Agent: search("关键词", scopes=["/plugins/my-plugin/"])
```

### 为什么不用工具

1. **工具名冲突**：插件工具名与内置工具冲突时，插件工具会被跳过
2. **Token 消耗**：工具定义会占用 Agent 上下文窗口
3. **复杂度**：VFS 数据自动支持搜索，无需额外实现
4. **一致性**：所有插件数据统一通过 VFS 暴露，Agent 无需学习新工具

---

## 插件类型与模板

### 新风格插件（推荐）

继承 `FaustPlugin` 基类，使用 pluggy hook：

```python
from faust_backend.plugin_system import FaustPlugin, PluginContext, hookimpl

class Plugin(FaustPlugin):
    def startup(self, ctx: PluginContext) -> None:
        ctx.vfs_write("/plugins/my-plugin.md", "# My Plugin")

    @hookimpl
    def heartbeat(self, ctx: PluginContext) -> None:
        # 定期更新 VFS 数据
        pass

def get_plugin():
    return Plugin()
```

### 旧风格插件（兼容）

使用 `on_load`/`on_unload`，无 pluggy 集成：

```python
from faust_backend.plugin_system import PluginManifest, PluginContext

class Plugin:
    manifest = PluginManifest(plugin_id="my-plugin", name="My Plugin")

    def on_load(self, ctx: PluginContext) -> None: pass
    def on_unload(self, ctx: PluginContext) -> None: pass

def get_plugin():
    return Plugin()
```

### 选择建议

| 场景 | 推荐风格 |
|------|----------|
| 新开发插件 | 新风格（FaustPlugin） |
| 需要 pluggy hook | 新风格（FaustPlugin） |
| 兼容旧系统 | 旧风格 |
| 简单工具提供 | 旧风格 |

---

## Plugin Market

### 概述

Plugin Market 是 FaustBot 的插件分发平台，由独立仓库 [FaustBotPluginMarket](https://github.com/liwusen/FaustBotPluginMarket) 维护，支持从在线市场安装/更新、打包和分享插件。

### 市场地址

- **索引地址**：https://raw.githubusercontent.com/liwusen/FaustBotPluginMarket/main/plugins.json
- **GitHub 仓库**：https://github.com/liwusen/FaustBotPluginMarket
- **市场页面**：GitHub Pages（见仓库 README）

### 安装/更新插件

**通过 deeplink（市场页面"安装"按钮）：**

```
faustbot://syncPlugin?id=my-plugin
```

若插件已存在会直接覆盖更新（安装前有一次安全确认弹窗）。

**通过 API：**

```bash
# 从市场安装/更新（存在即覆盖）
curl -X POST http://localhost:13900/faust/admin/plugin-market/sync \
  -H "Content-Type: application/json" \
  -d '{"plugin_id": "my-plugin"}'

# 检查已安装插件是否有更新
curl http://localhost:13900/faust/admin/plugin-market/check-updates

# 从本地 ZIP 安装
curl -X POST http://localhost:13900/faust/admin/plugins/install-zip \
  -H "Content-Type: application/json" \
  -d '{"zip_path": "D:/path/my-plugin.zip"}'

# 打包插件为 ZIP
curl -X POST http://localhost:13900/faust/admin/plugins/package-zip \
  -H "Content-Type: application/json" \
  -d '{"plugin_id": "my-plugin"}'
```

### 发布到 Market

发布流程完全由 issue 驱动，无需提交 PR：

1. 将插件打包为 zip（顶层为 `<plugin_id>/` 目录，内含 `plugin.json`），上传到任意 https 直链（如你自己仓库的 Release）
2. 在 [FaustBotPluginMarket](https://github.com/liwusen/FaustBotPluginMarket) 用 "Submit Plugin" issue 模板提交插件元数据与 zip 链接
3. 维护者审核后为 issue 添加 `approved` 标签
4. GitHub Action 自动校验、重打包并发布到市场仓库自己的 Release，同时更新 `plugins.json` 索引
5. 更新插件时提交新 issue 并提升版本号即可

### 市场索引格式

```json
{
  "updated_at": "2026-01-01T00:00:00Z",
  "plugins": [
    {
      "id": "my-plugin",
      "name": "My Plugin",
      "description": "插件描述",
      "author": "作者",
      "version": "1.0.0",
      "download_url": "https://github.com/liwusen/FaustBotPluginMarket/releases/download/plugin-my-plugin-v1.0.0/my-plugin.zip",
      "homepage": "https://...",
      "tags": ["utility", "ai"],
      "source_issue": 1,
      "published_at": "2026-01-01T00:00:00Z"
    }
  ]
}
```

---

## 最佳实践

### 架构设计

1. **VFS 优先**：数据暴露优先使用 VFS，而非注册工具
2. **单一职责**：每个插件专注一个功能
3. **配置驱动**：通过配置项控制行为，避免硬编码

### 数据存储

1. **小数据**：使用 `data/` 目录存储 SQLite 或 JSON
2. **Agent 可读数据**：写入 VFS（`/plugins/{id}/`）
3. **动态数据**：使用 `vfs_write_symbolic` 动态生成

### 前端集成

1. **小组件**：使用 `registerWidget` 注册 UI 小组件（`managed` 缺省为 `true`，定位/显隐/缩放由小组件管理器统一处理，无需自写 rAF 布局循环；详见 plugin-api-reference.md）
2. **通信**：通过 `communicate_handler` 处理前端请求
3. **样式**：使用独立 CSS 文件，避免全局样式污染

### 安全性

1. **输入验证**：验证所有外部输入
2. **错误处理**：捕获异常，避免崩溃
3. **权限最小化**：只请求必要的权限

### 性能

1. **异步操作**：使用 `asyncio` 处理 I/O 密集任务
2. **节流心跳**：在 `heartbeat` 中避免频繁操作
3. **缓存数据**：避免重复计算或拉取

---

## 相关文档

- [插件 API Reference](plugin-api-reference.md) - 完整的 Hook 和 API 参考
- [Agent 基础](agent-basics.md) - 理解 Agent 和工具系统
- [配置说明](configuration.md) - 配置中心使用指南
