# MCP Server

FaustBot 内置 MCP（Model Context Protocol）客户端，支持连接外部 MCP 服务器扩展工具能力。

## 什么是 MCP

MCP 是 Anthropic 提出的开放协议，让 AI 模型以标准化方式访问外部工具和数据源。FaustBot 作为 MCP 客户端，可以连接任意兼容的 MCP server。

## 传输模式

### Stdio（本地进程）

MCP server 以子进程运行，通过标准输入输出通信：

### SSE（远程服务）

MCP server 运行在远程服务器，通过 HTTP SSE 通信：

### Streamable HTTP

MCP server 运行在远程服务器，通过 Streamable HTTP 通信：

## 使用 npx 启动

FaustBot 内置了便携版 Node.js（`.nodejs/node.exe`），`npx` 命令会自动解析到 `.nodejs/npx.cmd`：

## 配置界面

在 FaustBot 的配置中心（Configer）中，MCP 服务器 页面提供：

- Stdio / SSE 传输模式切换
- 命令 / URL 内联编辑
- 运行日志查看
- 连接状态指示

## 更多

- [MCP 官方规范](https://spec.modelcontextprotocol.io/)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
