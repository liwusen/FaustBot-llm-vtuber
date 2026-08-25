# 聊天输入框增强(Chat Composer)设计

日期:2026-08-25
状态:已获用户批准

## 背景与目标

主窗口聊天条(`#textChatBar`)目前是单行 `<input>`,仅支持纯文本发送与 `/` 命令补全(Tab 未接)。本设计为其增加四项能力:

1. **自动多行**:textarea 随内容自动生长
2. **附件**:文件/图片以**路径**交给 Agent(不做多模态消息)
3. **剪贴板集成**:Ctrl+V 粘贴图片落临时文件;聚焦时自动附加剪贴板图片(可配置)
4. **Tab 补全**:下拉建议的接受键从 Enter 中独立

## 交互设计

### 多行输入

- `<input type="text">` → `<textarea>`,1 行起步,随内容自动长高,上限 6 行(约 160px)后内部滚动
- **Enter 发送,Shift+Enter 换行**
- 卡片以底边为锚向上生长(不遮模型下半身);高度变化接入 widget 系统 `getWidgetSize` 动态尺寸
- 底部微字提示:`Enter 发送 · Shift+Enter 换行`

### 附件

UI:附件胶囊显示在输入区上方一行(图片=绿色、文件=灰色,带 ✕ 移除);输入区左下角 `📎` 按钮打开系统文件选择;支持拖拽文件到聊天条(等同 📎)。上限 10 个,超限 toast 提示。

数据流(全部只给 Agent 路径,消息为纯文本 + 路径标注):

| 来源 | 落地策略 |
|---|---|
| 📎 / 拖拽选择文件 | 保留原路径不复制,直接附加 |
| Ctrl+V 剪贴板图片 | 写入 `~/.faustbot/uploads/clip-YYYYMMDD-HHMMSS.png` 后附加 |
| 聚焦自动附加 | 同 Ctrl+V;仅图片与文件路径,文本不自动附加;胶囊短暂高亮提示,可 ✕ 撤销 |

发送时在消息文本末尾追加 `[附件] <路径>`(每个附件一行),发送后清空附件列表。

### 剪贴板

- **Ctrl+V(始终可用)**:粘贴文本 → 正常插入;粘贴图片 → 写临时文件 + 附加
- **聚焦自动附加**(`AUTO_IMAGE_ATTACH_ENABLED` 控制,默认 **true**):输入框获得焦点(含 Ctrl+Shift+T 全局呼出)时自动读剪贴板——图片或文件路径则自动附加并提示;文本不自动
- 防重复:发送后 30 秒内不重复附加同一张剪贴板图片(防焦点抖动)

### Tab 补全(autocomplete.js 增强)

- 下拉激活:**Tab = 接受当前高亮项**(无高亮则接受首项)
- 输入以 `/` 开头但下拉未显示:Tab 主动拉起下拉
- 其余不变:↑↓ 导航、Esc 关闭、Enter 发送

## 架构

### 新模块 `frontend/libs/chat-composer.js`(ES Module)

职责:textarea 自动生长、附件状态与胶囊渲染、剪贴板(粘贴 + 聚焦检测)、文件选择接线。
接口:`initChatComposer({ textarea, chipContainer, onHeightChange })` → `{ getAttachments(), addAttachments(paths), clear() }`。
`app.js` 保留 `sendTextChatMessage`(发送前取 `getAttachments()` 拼接 `[附件]` 行)。

### Electron IPC 桥(新增 3 个 `window.api`,主进程实现 + preload 暴露)

| API | 实现 | 返回 |
|---|---|---|
| `readClipboardImage()` | 主进程 `clipboard.readImage()`(无权限弹窗) | `{ dataURL } \| null` |
| `readClipboardFilePaths()` | 读 Windows 剪贴板 `FileNameW` buffer | `string[]` |
| `pickFiles()` | 系统文件对话框 | `string[]` |

### 配置项

`AUTO_IMAGE_ATTACH_ENABLED`:bool,默认 true,归入「模型」类别。
落点:`frontend/libs/configer/constants.js`(META + LIVE2D_KEYS)+ `backend/faust_backend/admin_runtime.py` DEFAULTS。

## 错误处理

- 剪贴板读取失败 / 非图片:静默跳过
- 临时文件写入失败:toast 提示并放弃该附件
- 附件超限(>10):toast 提示
- `~/.faustbot/uploads/` 不存在则懒创建

## 测试

- 后端零改动(纯前端 + Electron);`frontend/scripts/check-js-syntax.js` 全绿
- CDP 冒烟(按仓库集成测试流程):启动后端 + 前端(开 CDP),验证多行生长、Ctrl+V 图片落盘、聚焦自动附加开关、Tab 补全、附件路径随消息发出
- composer 模块保持纯 DOM 逻辑,便于后续抽取单测

## 非目标

- 多模态消息(图片直接进视觉模型)——附件一律走路径
- 富文本输入
- 剪贴板后台监听
