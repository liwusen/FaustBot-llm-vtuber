# 开发者环境指南

本文档面向希望在本地进行 FaustBot 开发的开发者。

## 前置准备

- **操作系统**: Windows 10/11 或 Windows Server 2019+
- **Python**: 3.11+（推荐 3.11.9）
- **Node.js**: 20 +
- **Git**: 任意版本

## 克隆仓库

```bat
git clone https://github.com/liwusen/FaustBot-llm-vtuber.git
cd FaustBot-llm-vtuber
```

## 一键初始化

项目提供 `setup-runtime.bat` 自动完成所有依赖安装，支持以下参数：

| 参数                                 | 必选  | 说明                                                                                       |
| ---------------------------------- | --- | ---------------------------------------------------------------------------------------- |
| `--torch <版本>`                     | 是   | PyTorch 版本。`cu128`=GPU CUDA 12.8，`cu121`=GPU CUDA 12.1，`cu130`=GPU CUDA 13.0，`cpu`=CPU 版 |
| `--tts yes\|no`                    | 否   | 是否下载 TTS 模型。yes=下载，no=不下载                                                                |
| `--source cn\|official`            | 否   | 依赖源。cn=国内镜像，official=官方源                                                                 |
| `--install-python yes\|no`         | 否   | 是否安装 Python + pip + wheel 等基础环境                                                          |
| `--install-node yes\|no`           | 否   | 是否安装 frontend 和 mc-operator 的 Node.js 依赖                                                 |
| `--install-bundle-node yes\|no`    | 否   | 是否安装 .nodejs 便携版 Node.js 与 MCP server 依赖                                                 |
| `--tts-variant standard\|nvidia50` | 否   | TTS 包类型。standard=普通显卡，nvidia50=50 系显卡                                                    |

## 项目结构

```
FaustBot-llm-vtuber/
├── backend/                    # Python 后端
│   ├── main.py                 # 入口
│   ├── faust_backend/          # 核心代码
│   │   ├── runtime/            # 运行时：状态、生命周期、中间件
│   │   ├── tools/              # Agent 工具集（18+ 工具）
│   │   ├── memory/             # 记忆系统（向量+图谱+BM25）
│   │   ├── routes/             # FastAPI 路由
│   │   ├── speech/             # TTS/ASR
│   │   ├── frontend/           # 前端桥接
│   │   └── events/             # 事件总线
│   └── tests/                  # pytest 测试
├── frontend/                   # Electron 前端
│   ├── electron-main.js        # 主进程
│   ├── app.js                  # 渲染进程（Live2D/VRM）
│   ├── config-window.js        # 配置中心
│   └── libs/configer/          # 配置中心常量
├── agents_template/            # Agent 角色模板
├── __dev__/                    # 开发文档/工具
│   └── website/                # 项目网站
├── .runtime/                   # 嵌入式 Python 运行时（不在版本控制）
└── .nodejs/                    # 便携版 Node.js（不在版本控制）
```

## 启动项目

### 后端

```bat
./backend/MAIN.bat
```

服务监听 `127.0.0.1:13900`。CLI 参数：

| 参数                                | 说明                      |
| --------------------------------- | ----------------------- |
| `--agent <name>`                  | 指定 Agent 角色（默认 `faust`） |
| `--no-run-other-backend-services` | 不启动辅助服务                 |
| `--no-startup-chat`               | 跳过启动对话                  |

### 前端

```bat
cd frontend
npm start
```

前端 Electron 窗口自动连接后端 WebSocket。

## 运行测试

```bat
./backend/TEST.bat
```

## 常用开发流程

### 添加新工具

1. 在 `backend/faust_backend/tools/` 下创建模块
2. 使用 `@register` + `@tool` 装饰器注册
3. 在 `tools/__init__.py` 中 import
4. `tools/_registry.py` 中 `get_tools_for_agent()` 做 Agent 过滤

### 修改前端

- `app.js` 是 ES module（`type="module"`），支持 `import`/`export`
- 前端构建使用 `esbuild`
- 与后端通信通过 WebSocket + `window.api` IPC（preload.js 暴露）

### 调试提示

- 后端日志输出到控制台，WebSocket 通信可见
- Agent 工具调用在中介层自动截断（500 字符摘要 + `artifact://` 引用）
- 配置中心 Configer 通过 IPC (`window.api.configRequest`) 与后端通信

## 常见问题

**Q: 启动后端提示缺少模块？**  
A: 确认已执行 `pip install -r requirements.txt`，注意必须使用 `.runtime` 下的 Python。

**Q: setup-runtime.bat 脚本出错怎么办？**  
A: 扔给 AI（bushi），让它帮你装
