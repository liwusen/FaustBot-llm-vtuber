// constants for config-window. Loaded before config-window.js
/*
!!!Warning!!!:

1. 这是内置功能的配置常量文件。
2. 如果你在写插件或自定义功能，请绝对不要修改此文件。
  你可以使用Plugin前端注入API
3. 也许以后的i18n可以从这里下手?

*/
// Begin of constants
var META = {
  EMBED_MODEL: { label: "Embedding 模型", help: "知识库文本向量化使用的 embedding 模型名称。" },
  EMBED_API_BASE: { label: "Embedding 接口地址", help: "知识库向量化使用的 API Base URL。" },
  SECURITY_SYS_ENABLED: { label: "启用安全系统", help: "开启后，部分高风险调用会先经过安全审查。" },
  KB_ENABLED: { label: "启用记忆", help: "开启后允许使用树形记忆库与向量检索能力。" },
  ARAYA_ENABLED: { label: "启用 Araya", help: "开启后允许独立记忆维护 Agent 自动触发。" },
  ARAYA_IDLE_MINUTES: { label: "Araya 空闲触发分钟", help: "主 Agent 连续空闲达到该值后允许自动运行。" },
  MODEL_TYPE: { label: "模型类型", help: "选择 Live2D（2D）、VRM（3D）或 Images（图片）模型。" },
  LIVE2D_MODEL_PATH: { label: "Live2D 模型路径", help: "前端加载的 Live2D 模型文件路径。" },
  LIVE2D_MODEL_SCALE: { label: "模型缩放", help: "模型在前端画布中的整体缩放比例。" },
  LIVE2D_MOUSE_TRACKING_STRENGTH: { label: "鼠标跟踪强度", help: "模型头部/视线跟随鼠标的幅度（0=完全关闭跟踪，1=原始强度）；过大时头部转动会显得夸张，建议 0.3~0.6。" },
  LIVE2D_MODEL_X: { label: "Live2D 横向位置", help: "模型 X 相对坐标（0.1-1.0，屏幕宽度比例）；留空时由前端自动决定。" },
  LIVE2D_MODEL_Y: { label: "Live2D 纵向位置", help: "模型 Y 相对坐标（0.1-1.0，屏幕高度比例）；留空时由前端自动决定。" },
  VRM_MODEL_PATH: { label: "VRM 模型路径", help: "前端加载的 VRM 模型文件路径" },
  IMAGE_MODEL_CONFIG: { label: "Images 模型配置", help: "图片模型的默认图、情绪图、点击图和嘴型图配置。" },
  TEXT_CHAT_BAR_Y_FACTOR: { label: "文字对话框 Y 轴绑定", help: "控制文字对话框绑定在模型高度上的位置，范围 -1 到 2。" },
  FRONTEND_QUICK_CONTROLLER_X_OFFSET: { label: "快捷控制栏 X 偏移", help: "控制快捷控制栏横向偏移，单位像素。" },
  FRONTEND_CLICK_THROUGH: { label: "前端点击穿透", help: "开启后桌宠窗口忽略鼠标点击。" },
  FRONTEND_DEFAULT_TTS_LANG: { label: "默认 TTS 语言", help: "前端发送 TTS 请求时默认使用的语言。" },
  TTS_MODE: { label: "TTS 模式", help: "选择 GPT-SoVITS（本地）、OpenAI 兼容、FaustBot Cloud 或 Edge TTS。" },
  ASR_MODE: { label: "ASR 模式", help: "选择 Whisper（本地，默认）、FunASR（本地）、OpenAI 兼容或 FaustBot Cloud。" },
  WHISPER_MODEL: { label: "Whisper 模型", help: "本地 Whisper 使用的模型大小，越大越准但越慢（tiny/base/small/medium/large 等）。" },
  WHISPER_LANGUAGE: { label: "Whisper 语言", help: "识别语言代码，如 zh、en、ja；留空则自动检测。" },
  WHISPER_INITIAL_PROMPT: { label: "Whisper 初始提示词", help: "传给 Whisper 的 initial_prompt，用于引导识别风格/术语，对中文识别质量影响较大。" },
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
  REASONING_CONFIG: { label: "思考强度", help: "全局思考配置：off 关闭思考，low/medium/high 为思考强度。每个 Provider 的思考格式（Thinking 类型）在下方 Provider 编辑中单独指定。" },
  MM_BRIDGE_MAX_SCAN: { label: "多模态桥接扫描条数", help: "每轮对话从最近 ToolMessage 中扫描图片输出的最大条数。" },
  MM_BRIDGE_REMOVE_SOURCE: { label: "桥接后删除源消息", help: "开启后，转换完成的源 ToolMessage 会从上下文中移除。" },
  MM_BRIDGE_KEEP_TURNS: { label: "图片消息保留轮数", help: "生成的图片多模态消息在几轮对话后自动删除；0 表示用完即删。" },
  mcp_servers: { label: "MCP 服务器 配置", help: "通过 bundled Node.js 或 SSE 连接的 MCP server 配置。" },
};

var FIELD_OPTIONS = {
  MODEL_TYPE: ["live2d", "vrm", "images"],
  TTS_MODE: ["gpt-sovits", "openai", "faustbot-cloud", "edge-tts"],
  ASR_MODE: ["whisper", "funasr", "openai", "faustbot-cloud"],
  WHISPER_MODEL: ["tiny", "base", "small", "medium", "large", "large-v2", "large-v3", "turbo"],
  WHISPER_LANGUAGE: ["zh", "en", "ja", "ko", "yue"],
  FRONTEND_DEFAULT_TTS_LANG: ["zh", "en", "ja", "ko", "yue"],
  OPENAI_TTS_VOICE: ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer"],
  OPENAI_TTS_RESPONSE_FORMAT: ["mp3", "wav", "opus", "aac", "flac", "pcm"],
  OPENAI_ASR_RESPONSE_FORMAT: ["json", "text", "srt", "verbose_json", "vtt"],
  REASONING_CONFIG: ["off", "low", "medium", "high"],
  THINKING_TYPE: ["none", "openai", "qwen", "deepseek"],
  TTS_PROMPT_LANGUAGE: ["zh", "en", "ja", "ko", "yue", "中文", "英文", "日文", "韩文", "粤语"],
};

var AGENT_FILES = ["AGENT.md", "ROLE.md", "COREMEMORY.md", "TASK.md"];
var TEXTAREA_KEYS = new Set(["OPENAI_TTS_INSTRUCTIONS", "OPENAI_ASR_PROMPT", "TTS_PROMPT_TEXT", "WHISPER_INITIAL_PROMPT"]);
var SECRET_KEYS = new Set(["SEARCH_API_KEY", "EMBED_API_KEY", "FAUSTBOT_CLOUD_SERVICE_KEY"]);

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
var AI_PUBLIC_KEYS = ["EMBED_MODEL", "EMBED_API_BASE", "SECURITY_SYS_ENABLED", "KB_ENABLED", "AGENT_NAME", "ARAYA_ENABLED", "ARAYA_IDLE_MINUTES", "RERANK_ENABLED", "RERANK_TOP_K", "BM25_ONLY", "MM_BRIDGE_MAX_SCAN", "MM_BRIDGE_REMOVE_SOURCE", "MM_BRIDGE_KEEP_TURNS", "MD_BLOCK_ENABLED", "REASONING_CONFIG"];
var AI_PRIVATE_KEYS = ["SEARCH_API_KEY", "EMBED_API_KEY"];
var LIVE2D_KEYS = ["MODEL_TYPE", "LIVE2D_MODEL_PATH", "VRM_MODEL_PATH", "IMAGE_MODEL_CONFIG", "LIVE2D_MODEL_SCALE", "LIVE2D_MODEL_X", "LIVE2D_MODEL_Y", "LIVE2D_MOUSE_TRACKING_STRENGTH", "TEXT_CHAT_BAR_Y_FACTOR", "FRONTEND_QUICK_CONTROLLER_X_OFFSET", "FRONTEND_CLICK_THROUGH", "FRONTEND_DEFAULT_TTS_LANG"];
var SPEECH_PUBLIC_KEYS = ["TTS_MODE", "ASR_MODE", "WHISPER_MODEL", "WHISPER_LANGUAGE", "WHISPER_INITIAL_PROMPT", "FAUSTBOT_CLOUD_BASE_URL", "FAUSTBOT_CLOUD_TIMEOUT_SECONDS", "OPENAI_TTS_BASE_URL", "OPENAI_TTS_MODEL", "OPENAI_TTS_VOICE", "OPENAI_TTS_RESPONSE_FORMAT", "OPENAI_TTS_SPEED", "OPENAI_TTS_INSTRUCTIONS", "OPENAI_ASR_BASE_URL", "OPENAI_ASR_MODEL", "OPENAI_ASR_LANGUAGE", "OPENAI_ASR_PROMPT", "OPENAI_ASR_RESPONSE_FORMAT", "OPENAI_ASR_TEMPERATURE", "OPENAI_ASR_TIMESTAMP_GRANULARITIES", "TTS_REFER_WAV_PATH", "TTS_PROMPT_TEXT", "TTS_PROMPT_LANGUAGE", "EDGE_TTS_VOICE", "EDGE_TTS_RATE", "EDGE_TTS_PITCH", "EDGE_TTS_TIMEOUT_SECONDS"];


META.EDGE_TTS_VOICE = { label: "Edge TTS 音色", help: "Microsoft Edge TTS 使用的 voice 名称，例如 en-US-AriaNeural 或 zh-CN-shaanxi-XiaoniNeural。" };
META.EDGE_TTS_RATE = { label: "Edge TTS 语速", help: "Edge TTS 的速率设置，例如 0% 或 -10% 或 20%。" };
META.EDGE_TTS_PITCH = { label: "Edge TTS 音高", help: "Edge TTS 的音高设置，例如 0% 或 -5% 或 10%。" };
META.EDGE_TTS_TIMEOUT_SECONDS = { label: "Edge TTS 超时(秒)", help: "调用 Edge TTS 的超时秒数。" };
META.MD_BLOCK_ENABLED = { label: "启用 Markdown 内容块", help: "开启后主 Agent 可使用 RenderMarkdownBlock 工具向气泡推送 Markdown 内容块（支持 mermaid 图表），内容仅展示不朗读。保存后立即生效。" };

var SPEECH_PRIVATE_KEYS = ["FAUSTBOT_CLOUD_SERVICE_KEY"];
var MODULES = [
  { id: "overview", title: "概览", desc: "当前角色、模型、状态概览。" },
  { id: "ai", title: "AI 服务商", desc: "模型、接口地址与密钥配置。" },
  { id: "live2d", title: "模型", desc: "模型切换与显示控制。" },
  { id: "speech", title: "语音", desc: "ASR/TTS 模式与参数配置。" },
  { id: "agent", title: "角色", desc: "角色文件编辑、切换与创建。" },
  { id: "memory", title: "记忆", desc: "知识库一体化管理。" },
  { id: "araya", title: "Araya", desc: "Araya 记忆库自动维护管理。" },
  { id: "components", title: "组件", desc: "管理 ASR/TTS/MC控制器 组件。" },
  { id: "mcp", title: "MCP 服务器", desc: "管理 MCP 服务器配置、启停与日志。" },
  // runtime 从侧边栏移除，入口在高级页面
  // { id: "runtime", title: "运行时", desc: "服务状态与运行时控制。" },
  { id: "triggers", title: "触发器", desc: "计划AI任务列表与编辑。" },
  { id: "skills", title: "技能", desc: "Skill 安装与管理" },
  { id: "plugins", title: "插件", desc: "FaustBot 插件" },
  { id: "advanced", title: "高级", desc: "未归类项目与高级配置。" },
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

const SCALE_PRESETS = {
    LIVE2D_MODEL_SCALE: { type: "range", min: 0.1, max: 1, step: 0.01, unit: "x" },
    LIVE2D_MOUSE_TRACKING_STRENGTH: { type: "range", min: 0, max: 1, step: 0.05, unit: "" },
    LIVE2D_MODEL_X: { type: "range", min: 0.1, max: 1, step: 0.01, unit: "" },
    LIVE2D_MODEL_Y: { type: "range", min: 0.1, max: 1, step: 0.01, unit: "" },
    TEXT_CHAT_BAR_Y_FACTOR: { type: "range", min: -1, max: 2, step: 0.05, unit: "x" },
    OPENAI_TTS_SPEED: { type: "range", min: 0.25, max: 4, step: 0.05, unit: "x" },
    DECAY_PER_MINUTE: { type: "range", min: 0, max: 1, step: 0.01, unit: "" },
    OVERLAY_INTENSITY: { type: "range", min: 0, max: 100, step: 1, unit: "%" },
  };

// ── 交互式功能引导 ──
// 注意：动作函数必须先于 CONFIG_ONBOARDING_STEPS 定义（数组字面量求值时会立即调用）。
// switchModule 在 state-core.js 定义（本文件之后加载），此处只延迟引用，点击时才解析。
function onboardingActionSwitch(moduleId) {
  return function () {
    try {
      const btn = document.querySelector('[data-module="' + moduleId + '"]');
      if (btn) btn.click();
      else switchModule(moduleId);
    } catch (e) { console.warn("switchModule failed", e); }
  };
}

function onboardingActionOpenStage() {
  if (window.api && typeof window.api.toggleWidgetEditMode === "function") {
    window.api.toggleWidgetEditMode().catch(function (e) { console.warn("toggleWidgetEditMode failed", e); });
  }
}

var CONFIG_ONBOARDING_STEPS = [
  { title: "欢迎使用配置中心", body: "这里是 FaustBot 的所有设置入口：模型、语音、记忆、插件都在这里配置。\n接下来带你认识核心功能，只需几步。" },
  { title: "配置模块", body: "左侧是全部配置模块：\nAI 服务商 · 模型 · 语音 · 角色 · 记忆 · Araya · 组件 · MCP · 触发器 · 技能 · 插件 · 高级\n点击模块即可切换到对应配置区。" , target: "#moduleNav" },
  { title: "AI 服务商", body: "配置模型接口、API 密钥与主模型选择。", target: '[data-module="ai"]', action: onboardingActionSwitch("ai"), actionLabel: "切换到 AI 服务商", actionHint: "这里配置模型与密钥，改完记得点右上角保存。" },
  { title: "记忆", body: "知识库一体化管理：向量检索、图谱、文件都在这里。", target: '[data-module="memory"]', action: onboardingActionSwitch("memory"), actionLabel: "切换到记忆", actionHint: "这里是记忆模块，可以管理知识库与检索。" },
  { title: "Araya", body: "Araya 记忆库自动维护：空闲时自动整理记忆。", target: '[data-module="araya"]', action: onboardingActionSwitch("araya"), actionLabel: "切换到 Araya", actionHint: "这里是 Araya 模块，可以查看自动维护状态。" },
  { title: "插件", body: "FaustBot 插件：安装、启用与管理扩展能力。", target: '[data-module="plugins"]', action: onboardingActionSwitch("plugins"), actionLabel: "切换到插件", actionHint: "这里是插件模块，可以浏览已安装插件。" },
  { title: "保存与应用", body: "修改配置后：\n保存 = 写入配置文件；应用 = 让后端立即生效。\n右上角会实时显示未保存的修改数。", target: "#saveBtn" },
  { title: "调整 UI 布局", body: "主窗口的聊天栏、快捷控制器等位置都可以调整,右键组件打开菜单：\n点击下方按钮，会自动打开主窗口的布景台编辑模式，直接拖拽即可。\n 拖拽完成后按Esc退出编辑模式", action: onboardingActionOpenStage, actionLabel: "进入布景台", actionHint: "主窗口已进入布景台编辑模式，拖拽调整完,按Esc退出,然后,点击“下一步”继续。" },
  { title: "引导完成!", body: "配置中心的核心功能都介绍完了。\n祝你使用愉快！" }
];
// End of constants
