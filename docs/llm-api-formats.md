# 多 API 格式的 LLM Provider

FaustBot 支持四种 LLM API 格式，并且**一个 Provider 条目可以同时承载多种格式的模型**：

| 格式标识 | 协议 | 端点形态 |
|---|---|---|
| `openai_chat` | OpenAI Chat Completions | `{base}/chat/completions` |
| `openai_responses` | OpenAI Responses（无状态） | `{base}/responses` |
| `anthropic_messages` | Anthropic Messages | `{base}/v1/messages` |
| `google_gemini` | Google Gemini generateContent | `{base}/v1beta/models/...` |

典型场景是聚合网关：例如一个订阅端点在同一个 base URL 下同时提供 Claude、GPT、Gemini、
DeepSeek、Qwen 等模型，它们必须分别走 Anthropic / Responses / Gemini / Chat 协议。
FaustBot 会**按模型**自动选择格式，而不是按 Provider 一刀切。

## 格式是怎么决定的

优先级从高到低：

1. `model_api_formats[模型]` —— 用户在「编辑模型」里显式指定（配置器里叫「API 格式（覆盖）」）；
2. `api_format` —— Provider 级声明（配置器里的「API 格式」下拉，默认 `auto`）；
3. `model_catalog_routes[模型].format` —— **models.dev 目录**推断出的格式；
4. `base_url` 路径启发式：`/anthropic`、`/messages` → Anthropic；`/google`、`/v1beta` → Gemini；
   `/responses` → Responses；
5. 模型族回退（仅当目录显示该 Provider 确实有多种格式）：`claude-*` → Anthropic，
   `gemini-*` → Gemini，`gpt-5*`/`gpt-6*`/`o3*`/`o4*`/`codex*` → Responses；
6. 最终回退 `openai_chat`（日志里会给出 warning）。

端点来源：`format_base_urls[格式]` > models.dev 目录端点（仅 host 相同时）> `base_url`。
**显式指定了格式却没有可用端点时会直接报错**，不会静默把请求发到别的主机。

## models.dev 目录

格式与端点的默认数据来自 [models.dev](https://models.dev) 的 `api.json`（MIT License）：

- 下载地址：`https://models.dev/api.json?type=all`；
- 缓存位置：`~/.faustbot/cache/models_dev/api.json`，TTL 24 小时，原子写入，上限 32MB；
- 启动时后台刷新（`refresh_models_dev_catalog`），**不阻塞启动**；失败时继续用陈旧缓存，
  没有缓存则退回通用启发式；
- 手动刷新：配置器 AI 服务商页的「刷新 models.dev 目录」，或
  `POST /faust/admin/models-dev/refresh`；状态见 `GET /faust/admin/models-dev/status`。

### Provider 匹配

目录里的 provider 按「host + 模型覆盖率」匹配。覆盖率 = 目录能解释你配置的模型比例
（`|用户模型 ∩ 目录模型| / |用户模型|`），不用 Jaccard——你通常只启用目录里的一部分模型，
Jaccard 会因为目录条目很大而把重合度压到 0.1 以下。

- host 相同：`2 + 覆盖率 + 0.2 × 名称相似度`（host 是强信号）；
- host 不同：只有「覆盖率 ≥ 0.8 且至少 2 个模型重合」才作为候选，
  `覆盖率 + 0.3 × 名称相似度`。

这一类（`host_matched = false`）**只采用目录推断出的格式，端点仍用你的 `base_url`**，
避免把请求发到没有配置密钥的主机上。

匹配结果不对时可以手动 **pin**：在 Provider 编辑框里填「目录 Provider ID」再点「重新匹配目录」，
或调用 `POST /faust/admin/providers/{name}/catalog-match`，body `{"provider_id": "..."}`。

### 模型路由字段

刷新目录后，Provider 会持久化一份路由缓存（构建模型时不再联网）：

```json
"model_catalog_routes": {
  "claude-sonnet-4-5": {
    "format": "anthropic_messages",
    "base_url": "https://example.com/zen/go",
    "host_matched": true,
    "npm": "@ai-sdk/anthropic",
    "shape": null,
    "reasoning": true
  }
}
```

同时会用目录的 `limit.context` / `limit.output` **只填空缺**的
`model_context_lengths` / `model_max_output_tokens`（用户手填的值永远优先）。

## 各格式的构建差异

| 格式 | LangChain 类 | 关键参数 |
|---|---|---|
| `openai_chat` | `ChatOpenAI` / `ReasoningChatOpenAI` | `stream_usage=True`（可在 Provider 上关闭） |
| `openai_responses` | `ChatOpenAI` | `use_responses_api=True`、`output_version="responses/v1"`、`store=False`、`use_previous_response_id=False` |
| `anthropic_messages` | `ChatAnthropic` | **必须显式 `max_tokens`**（默认 8192，思考时 `budget+1024`，摘要专用上限） |
| `google_gemini` | `ChatGoogleGenerativeAI` | `max_output_tokens`、`thinking_budget`、`include_thoughts` |

思考参数按格式翻译（`thinking.get_format_thinking_params`）：

- Chat：沿用 qwen/deepseek/glm… 的 `reasoning_effort` / `extra_body` 预设；
- Responses：`reasoning={"effort": ...}`；
- Anthropic：`thinking={"type": "enabled", "budget_tokens": N}`；
- Gemini：`thinking_budget=N` + `include_thoughts=True`。

`stream_usage` 三态：`null` = 按格式默认；`true`/`false` = 强制开关。某些 OpenAI 兼容网关
拒绝 `stream_options.include_usage`，此时关掉即可（关掉后拿不到用量，自动压缩不会触发）。

## 服务端工具（Provider 托管）

服务端工具由**服务商自己执行**（例如联网搜索），不需要本地 ToolNode，也不会产生
`tool_calls`。在「编辑模型」里勾选后生效：

| 格式 | 请求里的工具规格 |
|---|---|
| `openai_responses` | `{"type": "web_search"}` |
| `anthropic_messages` | `{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}` |
| `google_gemini` | `{"google_search": {}}` |
| `openai_chat` | **不支持**（勾选会明确报错） |

启用后 Agent 就已经具备联网搜索能力，**不要**再调用本地 `search` 工具做重复检索。

## 会话压缩与用量口径

Token 用量一律取 API 上报值（`runtime/usage.py`），不做本地估算：

- **Anthropic**：真实输入 = `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`；
  上下文占用 = 输入 + 输出。只看 `input_tokens` 会让 prompt 缓存命中率高的长会话永远不触发压缩。
- **OpenAI Chat / Responses / Gemini**：缓存的 token 是输入的子集，**不重复相加**。

压缩请求会复用与主请求**逐字节相同**的前缀（System + 工具 schema + messages）以命中前缀缓存：

- 客户端工具替换成同 schema 的 noop mock；
- 服务端工具规格原样保留，并用 `tool_choice` 禁止本次真的调用
  （Anthropic 必须用 `{"type": "none"}`，字符串 `"none"` 会被理解成「调用名为 none 的工具」）；
- Anthropic 摘要必须显式 `max_tokens`；一旦因上限截断（`stop_reason=max_tokens`），
  本次**不写回** checkpoint，避免摘要没覆盖到的历史被永久删除；
- 写回时把多条 SystemMessage 合并成唯一一条（Gemini 只认第一条 system instruction）。

## 内部功能的限制

以下功能仍假定 OpenAI Chat 兼容协议：GUI 操作模型、QA eval、rerank、以及任何直接拼
`/chat/completions` 的调用。主模型不是 OpenAI 兼容格式时，它们会**明确报错**并提示
为该 Provider 设置 `format_base_urls["openai_chat"]`，或把主模型指向 OpenAI 兼容的 Provider。

## 故障排查

| 现象 | 原因与处理 |
|---|---|
| 某模型 400 `invalid_request_error` | 格式选错。在「编辑模型」里手动指定 API 格式，或检查目录匹配是否 pin 到了错误的 provider |
| 请求打到了错误主机 | `host_matched=false` 时不会用目录端点；检查 `format_base_urls` 是否填错 |
| 上下文占用一直很小、从不压缩 | 网关没返回 usage（`stream_usage` 关闭/不支持），或 Anthropic 缓存口径问题（已修复） |
| 目录加载失败 | 手动点「刷新 models.dev 目录」；无网络时只能手动指定格式与端点 |
| Gemini 报工具 schema 非法 | 该模型不支持服务端工具，取消勾选即可 |
