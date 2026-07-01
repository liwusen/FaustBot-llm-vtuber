// Extracted constants for config-window. Loaded before config-window.js
var META = {
  CHAT_MODEL: { label: "主对话模型", help: "Faust 主聊天与推理使用的模型名称。" },
  CHAT_API_BASE: { label: "主对话接口地址", help: "主对话模型对应的 API Base URL。" },
  EMBED_MODEL: { label: "Embedding 模型", help: "知识库文本向量化使用的 embedding 模型名称。" },
  EMBED_API_BASE: { label: "Embedding 接口地址", help: "知识库向量化使用的 API Base URL。" },
  SECURITY_SYS_ENABLED: { label: "启用安全系统", help: "开启后，部分高风险调用会先经过安全审查。" },
  KB_ENABLED: { label: "启用记忆", help: "开启后允许使用树形记忆库与向量检索能力。" },
  ARAYA_ENABLED: { label: "启用 Araya", help: "开启后允许独立记忆维护 Agent 自动触发。" },
  ARAYA_IDLE_MINUTES: { label: "Araya 空闲触发分钟", help: "主 Agent 连续空闲达到该值后允许自动运行。" },
  MODEL_TYPE: { label: "模型类型", help: "选择 Live2D（2D）或 VRM（3D）模型。" },
  LIVE2D_MODEL_PATH: { label: "Live2D 模型路径", help: "前端加载的 Live2D 模型文件路径。" },
  LIVE2D_MODEL_SCALE: { label: "模型缩放", help: "模型在前端画布中的整体缩放比例。" },
  LIVE2D_MODEL_X: { label: "Live2D 横向位置", help: "模型 X 坐标；留空时由前端自动决定。" },
  LIVE2D_MODEL_Y: { label: "Live2D 纵向位置", help: "模型 Y 坐标；留空时由前端自动决定。" },
  VRM_MODEL_PATH: { label: "VRM 模型路径", help: "前端加载的 VRM 模型文件路径" },
  TEXT_CHAT_BAR_Y_FACTOR: { label: "文字对话框 Y 轴绑定", help: "控制文字对话框绑定在模型高度上的位置，范围 0 到 1。" },
  FRONTEND_QUICK_CONTROLLER_X_OFFSET: { label: "快捷控制栏 X 偏移", help: "控制快捷控制栏横向偏移，单位像素。" },
  FRONTEND_CLICK_THROUGH: { label: "前端点击穿透", help: "开启后桌宠窗口忽略鼠标点击。" },
  FRONTEND_DEFAULT_TTS_LANG: { label: "默认 TTS 语言", help: "前端发送 TTS 请求时默认使用的语言。" },
  TTS_MODE: { label: "TTS 模式", help: "选择本地 TTS 或 OpenAI 兼容 TTS。" },
  ASR_MODE: { label: "ASR 模式", help: "选择本地 ASR 或 OpenAI 兼容 ASR。" },
  FAUSTBOT_CLOUD_BASE_URL: { label: "FaustBot Cloud 地址", help: "FaustBot Cloud 推理服务的 HTTP Base URL。" },
  FAUSTBOT_CLOUD_TIMEOUT_SECONDS: { label: "FaustBot Cloud 超时秒数", help: "调用 FaustBot Cloud TTS/ASR 时的 HTTP 超时。" },
  OPENAI_TTS_BASE_URL: { label: "OpenAI TTS 接口地址", help: "OpenAI 兼容 TTS 服务的 API Base URL。" },
  OPENAI_TTS_MODEL: { label: "OpenAI TTS 模型", help: "OpenAI 兼容 TTS 所使用的模型名称。" },
  OPENAI_TTS_VOICE: { label: "OpenAI TTS 音色", help: "OpenAI 兼容 TTS 的 voice 参数。" },
  OPENAI_TTS_RESPONSE_FORMAT: { label: "OpenAI TTS 音频格式", help: "TTS 输出音频编码格式。" },
  OPENAI_TTS_SPEED: { label: "OpenAI TTS 语速", help: "TTS 合成语速倍率。" },
  OPENAI_TTS_INSTRUCTIONS: { label: "OpenAI TTS 附加指令", help: "传给 TTS 模型的补充语气说明。" },
  OPENAI_ASR_BASE_URL: { label: "OpenAI ASR 接口地址", help: "OpenAI 兼容 ASR 服务的 API Base URL。" },
  OPENAI_ASR_MODEL: { label: "OpenAI ASR 模型", help: "OpenAI 兼容 ASR 所使用的模型名称。" },
  OPENAI_ASR_LANGUAGE: { label: "OpenAI ASR 语言", help: "可选语言提示，留空则自动判断。" },
  OPENAI_ASR_PROMPT: { label: "OpenAI ASR 提示词", help: "传给识别模型的上下文提示。" },
  OPENAI_ASR_RESPONSE_FORMAT: { label: "OpenAI ASR 返回格式", help: "识别结果返回格式。" },
  OPENAI_ASR_TEMPERATURE: { label: "OpenAI ASR 温度", help: "识别采样温度。" },
  OPENAI_ASR_TIMESTAMP_GRANULARITIES: { label: "OpenAI ASR 时间戳粒度", help: "verbose_json 模式下的时间戳粒度。" },
  CHAT_API_KEY: { label: "主对话密钥", help: "主聊天模型使用的 API Key。" },
  SEARCH_API_KEY: { label: "搜索密钥", help: "联网搜索工具使用的 API Key。" },
  EMBED_API_KEY: { label: "Embedding 密钥", help: "知识库 embedding 使用的 API Key。" },
  FAUSTBOT_CLOUD_SERVICE_KEY: { label: "FaustBot Cloud Service Key", help: "调用 FaustBot Cloud 所使用的 FSK- 前缀服务密钥。" },
  AGENT_NAME: { label: "当前 Agent", help: "指定当前加载的角色目录名称。" },
  TTS_REFER_WAV_PATH: { label: "TTS 参考音频路径", help: "本地 TTS 的参考音频文件路径。" },
  TTS_PROMPT_TEXT: { label: "TTS 参考文本", help: "参考音频对应的文本内容。" },
  TTS_PROMPT_LANGUAGE: { label: "TTS 参考语言", help: "参考音频文本语言。" },
  RERANK_ENABLED: { label: "启用 Reranker", help: "开启后对搜索结果进行重排序以提升相关性。" },
  RERANK_TOP_K: { label: "Rerank 保留数", help: "重排序后保留的 top-k 结果数量。" },
  BM25_ONLY: { label: "BM25 Only 模式", help: "开启后仅使用 BM25 关键词检索，不调用外部 Embedding API 和 Reranker。适用于离线/无 API Key 场景。" },
};

var FIELD_OPTIONS = {
  MODEL_TYPE: ["live2d", "vrm"],
  TTS_MODE: ["local", "openai", "faustbot-cloud", "edge-tts"],
  ASR_MODE: ["local", "openai", "faustbot-cloud"],
  FRONTEND_DEFAULT_TTS_LANG: ["zh", "en", "ja", "ko", "yue"],
  OPENAI_TTS_VOICE: ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer"],
  OPENAI_TTS_RESPONSE_FORMAT: ["mp3", "wav", "opus", "aac", "flac", "pcm"],
  OPENAI_ASR_RESPONSE_FORMAT: ["json", "text", "srt", "verbose_json", "vtt"],
  TTS_PROMPT_LANGUAGE: ["zh", "en", "ja", "ko", "yue", "中文", "英文", "日文", "韩文", "粤语"],
};

var AGENT_FILES = ["AGENT.md", "ROLE.md", "COREMEMORY.md", "TASK.md"];
var TEXTAREA_KEYS = new Set(["OPENAI_TTS_INSTRUCTIONS", "OPENAI_ASR_PROMPT", "TTS_PROMPT_TEXT"]);
var SECRET_KEYS = new Set(["CHAT_API_KEY", "SEARCH_API_KEY", "EMBED_API_KEY", "FAUSTBOT_CLOUD_SERVICE_KEY"]);

var ADVANCED_KEYS = new Set([
  // OpenAI TTS 推理参数
  "OPENAI_TTS_RESPONSE_FORMAT", "OPENAI_TTS_SPEED", "OPENAI_TTS_INSTRUCTIONS",
  // OpenAI ASR 推理参数
  "OPENAI_ASR_LANGUAGE", "OPENAI_ASR_PROMPT", "OPENAI_ASR_RESPONSE_FORMAT",
  "OPENAI_ASR_TEMPERATURE", "OPENAI_ASR_TIMESTAMP_GRANULARITIES",
  // Edge TTS 细调
  "EDGE_TTS_RATE", "EDGE_TTS_PITCH", "EDGE_TTS_TIMEOUT_SECONDS",
  // 本地 TTS 参考
  "TTS_PROMPT_LANGUAGE",
  // Cloud 超时
  "FAUSTBOT_CLOUD_TIMEOUT_SECONDS",
  // AI 安全/高级
  "ARAYA_IDLE_MINUTES",
  // Reranker
  "RERANK_TOP_K",
  // BM25 Only
  "BM25_ONLY",
  // Live2D 微调
  "TEXT_CHAT_BAR_Y_FACTOR", "FRONTEND_QUICK_CONTROLLER_X_OFFSET",
  "FRONTEND_CLICK_THROUGH", "FRONTEND_DEFAULT_TTS_LANG",
]);

var AI_PUBLIC_KEYS = ["CHAT_MODEL", "CHAT_API_BASE", "EMBED_MODEL", "EMBED_API_BASE", "SECURITY_SYS_ENABLED", "KB_ENABLED", "AGENT_NAME", "ARAYA_ENABLED", "ARAYA_IDLE_MINUTES", "RERANK_ENABLED", "RERANK_TOP_K", "BM25_ONLY"];
var AI_PRIVATE_KEYS = ["CHAT_API_KEY", "SEARCH_API_KEY", "EMBED_API_KEY"];
var LIVE2D_KEYS = ["MODEL_TYPE", "LIVE2D_MODEL_PATH", "VRM_MODEL_PATH", "LIVE2D_MODEL_SCALE", "LIVE2D_MODEL_X", "LIVE2D_MODEL_Y", "TEXT_CHAT_BAR_Y_FACTOR", "FRONTEND_QUICK_CONTROLLER_X_OFFSET", "FRONTEND_CLICK_THROUGH", "FRONTEND_DEFAULT_TTS_LANG"];
var SPEECH_PUBLIC_KEYS = ["TTS_MODE", "ASR_MODE", "FAUSTBOT_CLOUD_BASE_URL", "FAUSTBOT_CLOUD_TIMEOUT_SECONDS", "OPENAI_TTS_BASE_URL", "OPENAI_TTS_MODEL", "OPENAI_TTS_VOICE", "OPENAI_TTS_RESPONSE_FORMAT", "OPENAI_TTS_SPEED", "OPENAI_TTS_INSTRUCTIONS", "OPENAI_ASR_BASE_URL", "OPENAI_ASR_MODEL", "OPENAI_ASR_LANGUAGE", "OPENAI_ASR_PROMPT", "OPENAI_ASR_RESPONSE_FORMAT", "OPENAI_ASR_TEMPERATURE", "OPENAI_ASR_TIMESTAMP_GRANULARITIES", "TTS_REFER_WAV_PATH", "TTS_PROMPT_TEXT", "TTS_PROMPT_LANGUAGE", "EDGE_TTS_VOICE", "EDGE_TTS_RATE", "EDGE_TTS_PITCH", "EDGE_TTS_TIMEOUT_SECONDS"];


META.EDGE_TTS_VOICE = { label: "Edge TTS 音色", help: "Microsoft Edge TTS 使用的 voice 名称，例如 en-US-AriaNeural 或 zh-CN-shaanxi-XiaoniNeural。" };
META.EDGE_TTS_RATE = { label: "Edge TTS 语速", help: "Edge TTS 的速率设置，例如 0% 或 -10% 或 20%。" };
META.EDGE_TTS_PITCH = { label: "Edge TTS 音高", help: "Edge TTS 的音高设置，例如 0% 或 -5% 或 10%。" };
META.EDGE_TTS_TIMEOUT_SECONDS = { label: "Edge TTS 超时(秒)", help: "调用 Edge TTS 的超时秒数。" };

var SPEECH_PRIVATE_KEYS = ["FAUSTBOT_CLOUD_SERVICE_KEY"];
  { id: "overview", title: "概览", desc: "当前 Agent、模型、运行时状态摘要。" },
  { id: "ai", title: "AI Provider", desc: "模型、接口地址与密钥配置。" },
  { id: "live2d", title: "模型", desc: "模型类型切换、位置、缩放与显示行为。" },
  { id: "speech", title: "语音", desc: "ASR/TTS 模式与参数配置。" },
  { id: "agent", title: "Agent", desc: "角色文件编辑、切换与创建。" },
  { id: "memory", title: "记忆", desc: "知识库树、图谱、搜索一体化管理。" },
  { id: "araya", title: "Araya", desc: "Araya 状态监控与触发。" },
  { id: "runtime", title: "Runtime", desc: "服务状态与运行时控制。" },
  { id: "triggers", title: "Triggers", desc: "计划任务列表与编辑。" },
  { id: "skills", title: "Skills", desc: "Skill 安装、启停、删除。" },
  { id: "plugins", title: "Plugins", desc: "插件启停、重载、配置。" },
  { id: "advanced", title: "高级", desc: "未归类字段与扩展配置。" },
];

// ── 图谱常量 ──

var GRAPH_COLORS = {
  person: "#4a90d9", place: "#5cb85c", event: "#d9534f",
  concept: "#9b59b6", object: "#f0ad4e", document: "#5bc0de",
  custom: "#95a5a6",
  chat_record: "#27ae60", diary: "#e91e63",
  file: "#c47f3c", dir: "#8B7355",
};
var GRAPH_EDGE_COLORS = {
  has_child: "#aaa", references: "#666", next: "#1abc9c",
  relates_to: "#3498db", part_of: "#e67e22",
  located_at: "#2ecc71", created_by: "#9b59b6", mentions: "#e74c3c",
};
var GRAPH_NODE_RADIUS = 18;
var GRAPH_EXPAND_DEPTH = 1;

// End of constants
