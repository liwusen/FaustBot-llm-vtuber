# 贡献指南

感谢你考虑为 FaustBot 贡献代码或文档！本文档同时面向外部贡献者和仓库维护者，涵盖从环境搭建到提交 PR 的全流程。

- 项目地址：[liwusen/FaustBot-llm-vtuber](https://github.com/liwusen/FaustBot-llm-vtuber)
- 完整开发环境搭建：[docs/devinstall.md](docs/devinstall.md)
- 仓库核心规则：[AGENTS.md](AGENTS.md)
- 文档站：[https://faustbot.readthedocs.io/](https://faustbot.readthedocs.io/)

---

## 目录

- [开发环境搭建摘要](#开发环境搭建摘要)
- [PR 工作流](#pr-工作流)
- [提交规范](#提交规范)
- [代码规范](#代码规范)
- [项目特有规则](#项目特有规则)
- [编写和修改提示词](#编写和修改提示词)
- [测试要求](#测试要求)
- [插件开发指引](#插件开发指引)
- [文档贡献](#文档贡献)
- [FAQ / 常见问题](#faq--常见问题)

---

## 开发环境搭建

> ⚠️ 完整安装步骤请查阅 [docs/devinstall.md](docs/devinstall.md)。

---

## PR 工作流

1. **Fork 仓库** → 点击 GitHub 页面右上角的 Fork 按钮
2. **创建分支** — 从 `main` 切出，分支名建议：
   - `feat/xxx` — 新功能
   - `fix/xxx` — 修复
   - `refactor/xxx` — 重构
   - `docs/xxx` — 文档
3. **提交改动** — 遵从[提交规范](#提交规范)写 commit message
4. **本地验证** — 确保通过所有测试（见[测试要求](#测试要求)）
5. **推送分支** → `git push origin your-branch`
6. **创建 Pull Request** — 描述变更内容、动机、测试情况。如果涉及 UI 变动，建议附带截图
7. **等待 Review** — 维护者可能会要求修改，请持续跟进

> 如果是小的修复（typo、注释、文档），可以直接提 PR，无需先开 Issue。

---

## 提交规范

### Commit Message 格式

建议符合 [Conventional Commits](https://www.conventionalcommits.org/) 规范：

### PR 标题

与 Commit 格式一致，例如 `feat(agent): add web-search parallel tool calling`。

---

## 代码规范

### Python（后端）

- **Python 3.11+**，类型注解尽量完整
- **异步优先**：网络 / 耗时操作使用 `async`/`await`
- **资源管理**：文件打开 / Lock 使用 `with` 或 `async with`
- **测试框架**：`pytest`，测试文件放在 `backend/tests/` 下
- **风格**：遵循 PEP 8，推荐 `ruff` 或 `black` 格式化
- **非必要不要添加新的Websockets端点**: 可以考虑使用SSE

### JavaScript / HTML / CSS（前端）

- `app.js` 是 ES module
- 前端构建使用 `esbuild`

---

## 编写和修改提示词/Skills

> 这是项目的一个独特要求：Agent 的能力和角色提示词需要保持同步。

当你 **新增 / 修改了 Agent 的能力**（例如添加工具、调整行为），必须遵循：

1. 找到模板 prompt 文件（位于`agents_template/faust` 下）
2. 更新 prompt 介绍新能力的使用方式、触发条件
3. 在 PR 描述中说明 prompt 同步情况

如果这个能力不叫复杂,请

## 关于AI使用

**鼓励**任何人使用AI修改FaustBot 代码,但**要求至少能够读懂和解释提交中代码逻辑**

**建议**对于后端核心代码人工审查

---

## 测试要求

### 单元测试

- 后端测试使用 `pytest`，执行：

```bat
:: 使用 .runtime 下的 Python
backend/TEST.bat

:: 或手动指定
.runtime\python.exe -m pytest backend\tests
```

- 所有新功能 / 修复应当附带对应的单元测试
- 测试文件以 `test_` 开头，放在 `backend/tests/` 下

### 静态检查

- 提交前检查前端 JS 语法：`cd frontend && npm run check-js`

---

## 插件开发指引

FaustBot 的插件系统基于 `pluggy` 实现，支持：

- 工具注入（`register_tools` hook）
- 路由注册（`register_routes` hook）
- 前端资源（前端面板、配置界面）
- 心跳 / 事件中间件

**快速开始** 请阅读 [docs/create-plugin-guide.md](docs/create-plugin-guide.md)
**插件 API 详情** 请阅读 [docs/plugin-api-reference.md](docs/plugin-api-reference.md)

内置插件位于 `backend/default_plugins/`，可以作为参考实现。

### 插件开发重要提示:

FaustBot 插件仓库不在这里!

本仓库的插件都是内置插件!

如果想要公开你自己的插件,请移步[FaustBot Plugin Market](https://github.com/liwusen/FaustBotPluginMarket)

如果将新的插件提交到这个仓库而不是`FaustBot Plugin Market`,可能不予合并。

---

## 文档贡献

项目文档使用 **MkDocs** 构建，源文件位于 `docs/` 下。

然后访问 `http://127.0.0.1:8000`。

### 写作规范

- 使用 Markdown 语法
- 不要让 AI 使用 ASCII Art 画图，推荐使用 **Mermaid** 语法
- 中文文档为主
- 新增页面后记得更新 `mkdocs.yml` 的 `nav` 部分

---

再次感谢您的贡献