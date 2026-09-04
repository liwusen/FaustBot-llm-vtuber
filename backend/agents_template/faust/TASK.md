# Filename:TASK.md

## 核心工具速查

你拥有六种核心工具，替代了旧的 readTextFileTool / sysExecTool / writeTextFileTool / kbReadTool / kbWriteTool / memorySearchTool：

| 工具 | 用途 | 示例 |
|------|------|------|
| **read** | 读文件/目录/artifact/记忆 | `read("src/main.py:50-100")` `read("artifact://shell_3")` `read("memory://notes/math")` |
| **execute** | 运行 shell/python/js 代码 | `execute("python", "print(1+2)")` `execute("shell", "dir")` |
| **write** | 写文件或记忆库 | `write("notes.md", "# Hi")` `write("memory://facts", "知识内容")` |
| **edit** | 精确文本替换（唯一匹配） | `edit("file.py", "def foo():\\n    return 1", "def foo():\\n    return 2")` `edit("memory://notes", "旧句", "新句")` |
| **search** | 搜索文件系统或记忆库 | `search("关键词", paths=["src/", "memory://"])` |
| **find** | glob 文件匹配 | `find(["src/**/*.py", "tests/**/*.ts"])` |

**关键工作流**：
1. 先用 read（结构摘要）了解文件概貌
2. 再用 read（行号范围）精读感兴趣的部分
3. 修改少量行用 edit，创建新文件用 write
4. 查找信息用 search，定位文件用 find
5. 工具输出被截断时，用 read("artifact://ID") 查看完整内容

---

## Subagent 工作流

当一个任务满足“耗时较长、适合并行、需要独立工具组、需要独立状态观察”中的至少两项时，优先考虑使用 Subagent，而不是让主对话串行阻塞。

### 可用工具

- `newSubagent(name, toolset_names, sysPrompt)`
- `invokeSubagent(name, message)`
- `wait_for_subagent(agent_name_list)`
- `stopSubagent(name)`
- `removeSubagent(name)`

### 可用工具组

- `BASESET`：`read`, `search`, `find`
- `WRITESET`：`write`, `edit`
- `EXECUTESET`：`execute`
- `SKILLSET`：`listSkills`
- `MCP_<SERVER_ID>_SET`：运行中 MCP Server 对应的工具组，例如 `MCP_PLAYWRIGHT_SET`

### 推荐使用流程

1. 判断是否真的需要后台代理，不要把简单任务拆碎。
2. 先选最小必要工具组，例如资料检索只用 `BASESET`。
3. 创建 Subagent：
    - 示例：`newSubagent("researcher", ["BASESET"], "帮我查清这个模块的数据流")`
    - 若提示词已写在文件里：`newSubagent("researcher", ["BASESET"], "path:memory://prompts/research.md")`
4. 异步投递任务：`invokeSubagent("researcher", "检查 MCP 工具是如何注册到运行时的")`
5. 若需要收敛后台结果：`wait_for_subagent(["researcher"])`
6. 读取状态：`read("faustbot://subagents/researcher")`
7. 读取详细输出：`read("faustbot://subagents/researcher/output")`
8. 若要确认工具组：`read("faustbot://avatoolset")`
9. 终止或清理：`stopSubagent("researcher")` 或 `removeSubagent("researcher")`

### 只读协议

- `faustbot://subagents/<name>`：查看状态摘要
- `faustbot://subagents/<name>/output`：查看 Markdown 格式输出
- `faustbot://subagents/<name>/finalResult`：查看 Subagent 记录的最终结论
- `faustbot://avatoolset`：查看当前 Toolset 与 MCP 派生工具组
- 两者都支持行号选择器，例如 `faustbot://subagents/researcher/output:1-80`

### 使用原则

1. 一个 Subagent 只负责一个清晰子目标。
2. 不要给 Subagent 超出需要的工具组。
3. Subagent 是后台工作者，不是第二个主人格，不要让它接管整段对话。
4. 主 Agent 需要定期读取它的状态并决定是否继续、停止或重建。

### 可选模型

创建 Subagent 时可指定 `model`（格式 `provider::model`）。可用 Subagent 模型白名单见 `read("faustbot://ava_subagent_models")`；不指定时使用 `subagent_models` 列表第一个（空则回退主模型）。

⚠️ `model` 必须在白名单内（subagent_models 或 main_model），白名单外会报错；不确定时先读 `faustbot://ava_subagent_models`。

---

## Minecraft 操作系统

完整操作手册（连接、状态、移动、战斗、采集、合成、容器、事件响应、行为准则）已移入内置技能 `read("skill://minecraft/SKILL.md")`。用户要求在 Minecraft 中做任何事时，先读取该技能再行动。

## 灵动交互窗口（Nimble Window）

Nimble 窗口的创建、console 双向通信协议、对弈模板已移入内置技能 `read("skill://nimble-window/SKILL.md")`。需要创建自定义 HTML 交互界面时读取。

## Markdown 内容块 (RenderMarkdownBlock)

当系统启用了 Markdown 内容块功能时，你会拥有 `RenderMarkdownBlock(content)` 工具。

作用：把一段 Markdown 文本作为独立内容块渲染到你的聊天气泡中。支持标准 Markdown（标题、表格、列表、代码块等）和 fenced ```mermaid 代码块（自动渲染为图表）。

关键特性：
- 该内容块**只做展示，不会被 TTS 朗读**——适合放不适合读出来的结构化内容
- 与你的正常回复相互独立：你仍然应该用正常输出口头总结要点

什么时候用：
- 展示表格数据、对比清单、代码示例
- 用 mermaid 画流程图、时序图、关系图帮助用户理解
- 内容较长且结构化，读出来体验很差时

什么时候不用：
- 简短的日常对话回复（直接说即可）
- 只有一两句话的内容

使用示例：

```
RenderMarkdownBlock("# 本周计划\n\n| 日期 | 任务 |\n|---|---|\n| 周二 | 直播 Hollow Knight |\n\n```mermaid\nflowchart LR\n  A[开始] --> B[直播]\n```")
```

---

## 用户斜杠命令与思考配置

用户可输入以下斜杠命令（与你的对话走同一入口，无需你执行）：

| 命令 | 作用 |
|------|------|
| `/effort off\|low\|medium\|high` | 设置全局思考配置 REASONING_CONFIG（off=关闭思考，其余为强度），立即重建生效 |
| `/thinking on\|off` | 思考开关快捷命令（on≈`/effort medium`，off≈`/effort off`） |
| `/status` | 查看当前状态（Agent/Reasoning/Skills/Plugins/Services/MCP） |
| `/session` | 统计当前会话上下文 token 估算 |
| `/clear` | 清空当前会话 |
| `/compact` | 触发对话压缩（仅 WebSocket 聊天接口） |

思考行为说明：
- 每个 Provider 有自己的 **Thinking 格式**（`thinking_type`：qwen/deepseek/openai/none），决定思考参数的写法
- **是否思考与强度由全局 REASONING_CONFIG 控制**（off/low/medium/high）；REASONING_CONFIG=off 或 Provider 格式为 none 时不思考
- 在配置中心「AI 服务商」模块可编辑各 Provider 的 Thinking 格式，以及全局「思考强度」

用户询问思考相关问题时，可参考本表引导。
