# 聊天输入框增强(Chat Composer)实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 主窗口聊天条升级为多行自适应输入,支持附件(路径交给 Agent)、剪贴板图片集成(粘贴+聚焦自动附加)与 Tab 命令补全。

**Architecture:** 新建 `frontend/libs/chat-composer.js` ES 模块承载多行/附件/剪贴板逻辑;Electron 主进程新增 3 个 IPC(读剪贴板图并落盘、读剪贴板文件路径、多选文件对话框);`app.js` 只做接线;`autocomplete.js` 增加 Tab 接受;新配置 `AUTO_IMAGE_ATTACH_ENABLED` 走既有 admin runtime 配置链路。

**Tech Stack:** Electron 30+ (ipcMain.handle / clipboard / dialog)、原生 DOM(无框架)、FastAPI(仅加一个配置默认值)。

**Spec:** `docs/superpowers/specs/2026-08-25-chat-composer-design.md`

## Global Constraints

- 附件一律只给 Agent **路径**;图片粘贴先写 `~/.faustbot/uploads/clip-YYYYMMDD-HHMMSS.png`
- Enter 发送、Shift+Enter 换行;textarea 上限 6 行(≈160px)后内部滚动
- 附件上限 10 个;发送后清空;消息末尾追加 `[附件] <路径>`(每附件一行)
- UI 为亮色玻璃风,禁紫黑;聊天条以默认高度 64px 的底边为锚向上生长
- 中文 IME 合成期间的 Enter 不得触发发送(`isComposing` 守卫)
- 每个任务完成即 commit;前端改动跑 `node scripts/check-js-syntax.js`(cwd=frontend);后端改动跑 `.runtime/python.exe -m pytest backend/tests/ -q`

---

### Task 1: Electron IPC 桥(剪贴板图片落盘 / 剪贴板文件路径 / 多选附件对话框)

**Files:**
- Modify: `frontend/electron-main.js`(在 `ipcMain.handle('get-faustbot-root', ...)` 附近,约 1446 行后插入)
- Modify: `frontend/preload.js`(在 `api` 对象内 `ensureModel3Declarations` 之后追加)

**Interfaces:**
- Produces(后续任务依赖的 `window.api` 方法,全部返回 Promise):
  - `readClipboardImage(): Promise<{ path: string } | null>` — 剪贴板有图则写盘并返回路径,否则 null
  - `readClipboardFilePaths(): Promise<string[]>` — 资源管理器复制的文件路径
  - `pickAttachments(): Promise<string[]>` — 系统多选文件对话框

- [ ] **Step 1: 确认 electron-main.js 顶部导入**

检查文件头部 electron 解构是否含 `clipboard`;`fs`、`os`、`path` 是否已 require。缺哪个补哪个:

```js
const { app, BrowserWindow, ipcMain, dialog, clipboard, /* ...原有项 */ } = require('electron');
const fs = require('fs');
const os = require('os');
const path = require('path');
```

- [ ] **Step 2: 添加 3 个 ipcMain.handle**

在 `ipcMain.handle('get-faustbot-root', ...)` 之前插入:

```js
// ── Chat composer: 剪贴板与附件 ──
const COMPOSER_UPLOADS_DIR = path.join(os.homedir(), '.faustbot', 'uploads');

ipcMain.handle('composer-read-clipboard-image', async () => {
  try {
    const img = clipboard.readImage();
    if (!img || img.isEmpty()) return null;
    await fs.promises.mkdir(COMPOSER_UPLOADS_DIR, { recursive: true });
    const d = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    const name = `clip-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}.png`;
    const filePath = path.join(COMPOSER_UPLOADS_DIR, name);
    await fs.promises.writeFile(filePath, img.toPNG());
    return { path: filePath };
  } catch (e) {
    console.warn('composer-read-clipboard-image failed', e);
    return null;
  }
});

ipcMain.handle('composer-read-clipboard-file-paths', () => {
  try {
    const buf = clipboard.readBuffer('FileNameW'); // Windows CF_HDROP
    if (!buf || !buf.length) return [];
    return buf.toString('ucs2').split('\0').map((s) => s.trim()).filter(Boolean);
  } catch (e) {
    console.warn('composer-read-clipboard-file-paths failed', e);
    return [];
  }
});

ipcMain.handle('composer-pick-attachments', async () => {
  const result = await dialog.showOpenDialog({
    title: '选择附件',
    properties: ['openFile', 'multiSelections'],
  });
  if (result.canceled || !result.filePaths) return [];
  return result.filePaths;
});
```

- [ ] **Step 3: preload.js 暴露 API**

在 `ensureModel3Declarations: ...` 行后追加:

```js
  readClipboardImage: () => ipcRenderer.invoke('composer-read-clipboard-image'),
  readClipboardFilePaths: () => ipcRenderer.invoke('composer-read-clipboard-file-paths'),
  pickAttachments: () => ipcRenderer.invoke('composer-pick-attachments'),
```

- [ ] **Step 4: 语法检查**

Run: `cd frontend && node scripts/check-js-syntax.js`
Expected: `All files OK.`

- [ ] **Step 5: Commit**

```bash
git add frontend/electron-main.js frontend/preload.js
git commit -m "feat: 聊天条 IPC 桥(剪贴板图片落盘/文件路径/多选附件)"
```

---

### Task 2: `libs/chat-composer.js` 模块

**Files:**
- Create: `frontend/libs/chat-composer.js`

**Interfaces:**
- Consumes: `window.api.readClipboardImage / readClipboardFilePaths / pickAttachments`(Task 1)
- Produces:
  - `initChatComposer(opts): Composer`,其中 `opts = { textarea, chipContainer, barElement, pickButton, getAutoAttachEnabled: () => boolean, onHeightChange: () => void, toast: (msg: string) => void }`
  - `Composer.getAttachments(): Array<{ path: string, isImage: boolean }>`
  - `Composer.addAttachments(paths: string[], opts?: { highlight?: boolean }): number`
  - `Composer.clear(): void`

- [ ] **Step 1: 写入完整模块**

```js
/**
 * Chat composer: 多行自适应、附件(路径制)、剪贴板集成。
 * 由 app.js 调用 initChatComposer() 装配;单实例管理自身 DOM。
 * @module chat-composer
 */

const MAX_ATTACHMENTS = 10;
const MAX_LINES = 6;
const LINE_HEIGHT = 24;
const CLIP_DEDUP_MS = 30000;

function isImagePath(p) {
  return /\.(png|jpe?g|gif|webp|bmp)$/i.test(String(p || ''));
}

export function initChatComposer(opts) {
  const {
    textarea,
    chipContainer,
    barElement,
    pickButton,
    getAutoAttachEnabled,
    onHeightChange,
    toast,
  } = opts;

  const attachments = []; // { path, isImage }
  let lastClipSignature = null;
  let lastClipAt = 0;

  /* ── autogrow ── */
  function autogrow() {
    textarea.style.height = 'auto';
    const maxH = MAX_LINES * LINE_HEIGHT;
    const next = Math.min(textarea.scrollHeight, maxH);
    textarea.style.height = next + 'px';
    textarea.style.overflowY = textarea.scrollHeight > maxH ? 'auto' : 'hidden';
    if (onHeightChange) onHeightChange();
  }

  /* ── chips ── */
  function renderChips(highlight) {
    chipContainer.textContent = '';
    attachments.forEach((a, i) => {
      const chip = document.createElement('span');
      chip.className = 'composer-chip' + (a.isImage ? ' is-image' : '');
      if (highlight && i === attachments.length - 1) chip.classList.add('just-added');
      const name = a.path.replace(/[\\/]+$/, '').split(/[\\/]/).pop();
      const label = document.createElement('span');
      label.className = 'composer-chip-label';
      label.textContent = (a.isImage ? '🖼 ' : '📄 ') + name;
      label.title = a.path;
      const x = document.createElement('span');
      x.className = 'composer-chip-x';
      x.textContent = '✕';
      x.addEventListener('click', () => {
        attachments.splice(i, 1);
        renderChips(false);
      });
      chip.append(label, x);
      chipContainer.appendChild(chip);
    });
    chipContainer.style.display = attachments.length ? 'flex' : 'none';
    if (onHeightChange) onHeightChange();
  }

  /* ── attachments ── */
  function addAttachments(paths, { highlight = false } = {}) {
    let added = 0;
    for (const raw of paths || []) {
      const p = String(raw || '').trim();
      if (!p) continue;
      if (attachments.some((a) => a.path === p)) continue;
      if (attachments.length >= MAX_ATTACHMENTS) {
        if (toast) toast(`附件最多 ${MAX_ATTACHMENTS} 个`);
        break;
      }
      attachments.push({ path: p, isImage: isImagePath(p) });
      added++;
    }
    if (added) renderChips(highlight);
    return added;
  }

  function clear() {
    attachments.length = 0;
    renderChips(false);
  }

  /* ── clipboard ── */
  function clipSignature(img) {
    return img && img.path ? img.path : null;
  }

  async function attachClipboardImage() {
    if (!window.api || !window.api.readClipboardImage) return false;
    const img = await window.api.readClipboardImage().catch(() => null);
    if (!img || !img.path) return false;
    const sig = clipSignature(img);
    if (sig && sig === lastClipSignature && Date.now() - lastClipAt < CLIP_DEDUP_MS) return false;
    lastClipSignature = sig;
    lastClipAt = Date.now();
    return addAttachments([img.path], { highlight: true }) > 0;
  }

  async function attachClipboardFilePaths() {
    if (!window.api || !window.api.readClipboardFilePaths) return false;
    const paths = await window.api.readClipboardFilePaths().catch(() => []);
    return addAttachments(paths, { highlight: true }) > 0;
  }

  /* ── events ── */
  textarea.addEventListener('paste', async (e) => {
    const items = Array.from((e.clipboardData && e.clipboardData.items) || []);
    const hasImage = items.some((it) => it.kind === 'file' && it.type.startsWith('image/'));
    if (!hasImage) return; // 文本走默认粘贴
    e.preventDefault();
    const ok = await attachClipboardImage();
    if (!ok && toast) toast('剪贴板中没有可用的图片');
  });

  textarea.addEventListener('focus', async () => {
    try {
      if (typeof getAutoAttachEnabled === 'function' && !getAutoAttachEnabled()) return;
      await attachClipboardImage();
      await attachClipboardFilePaths();
    } catch (e) { /* 静默:自动附加失败不打扰 */ }
  });

  if (pickButton) {
    pickButton.addEventListener('click', async () => {
      if (!window.api || !window.api.pickAttachments) return;
      const paths = await window.api.pickAttachments().catch(() => []);
      addAttachments(paths, { highlight: true });
      textarea.focus();
    });
  }

  if (barElement) {
    barElement.addEventListener('dragover', (e) => { e.preventDefault(); });
    barElement.addEventListener('drop', (e) => {
      e.preventDefault();
      const files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
      addAttachments(files.map((f) => f.path).filter(Boolean), { highlight: true });
    });
  }

  textarea.addEventListener('input', autogrow);
  autogrow();

  return { getAttachments: () => attachments.slice(), addAttachments, clear };
}
```

- [ ] **Step 2: 语法检查**

Run: `cd frontend && node scripts/check-js-syntax.js`
Expected: `All files OK.`

- [ ] **Step 3: Commit**

```bash
git add frontend/libs/chat-composer.js
git commit -m "feat: chat-composer 模块(多行生长/附件/剪贴板)"
```

---

### Task 3: DOM/CSS 改造与 app.js 接线

**Files:**
- Modify: `frontend/index.html:96-100`(#textChatBar 结构)
- Modify: `frontend/styles.css:271-300` 附近(#textChatBar 及新增子元素样式)
- Modify: `frontend/app.js`(DOM 引用 ~47-48、`sendTextChatMessage` 2495-2550、`updateTextChatBarPosition` 3151-3193、composer 装配 ~3393)

**Interfaces:**
- Consumes: `initChatComposer`(Task 2)、`window.api.pickAttachments`(Task 1)
- Produces: `composer` 实例(app.js 内部);发送文本自动追加 `[附件] <路径>` 行

- [ ] **Step 1: index.html 结构**

替换:

```html
  <div id="textChatBar">
    <input id="textChatInput" type="text" placeholder="输入文字与 Faust 对话" />
    <button id="textChatSendBtn">发送</button>
  </div>
```

为:

```html
  <div id="textChatBar">
    <div id="textChatAttachments"></div>
    <textarea id="textChatInput" rows="1" placeholder="输入文字与 Faust 对话"></textarea>
    <div id="textChatBarFooter">
      <button id="textChatAttachBtn" type="button" title="添加附件">📎</button>
      <span id="textChatHint">Enter 发送 · Shift+Enter 换行</span>
      <button id="textChatSendBtn">发送</button>
    </div>
  </div>
```

- [ ] **Step 2: styles.css**

`#textChatBar` 规则(271 行起)中把 `align-items: center;` 改为 `flex-direction: column; align-items: stretch;`,并在其后追加:

```css
#textChatAttachments {
  display: none;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
}
.composer-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: #eef1f5;
  border: 1px solid #dfe5ec;
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 12px;
  color: #5c6d7d;
  max-width: 220px;
}
.composer-chip.is-image { background: #e5f7ef; border-color: #bfe8d6; color: #0b8a60; }
.composer-chip.just-added { animation: chip-pop 0.6s ease; }
@keyframes chip-pop { 0% { transform: scale(0.85); opacity: 0.3; } 100% { transform: scale(1); opacity: 1; } }
.composer-chip-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.composer-chip-x { cursor: pointer; color: #8aa0b2; flex-shrink: 0; }
.composer-chip-x:hover { color: #d9534f; }
#textChatInput {
  width: 100%;
  resize: none;
  overflow-y: hidden;
  line-height: 24px;
  border: none;
  outline: none;
  background: transparent;
  font: inherit;
  color: inherit;
}
#textChatBarFooter {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}
#textChatAttachBtn {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 14px;
  padding: 2px 4px;
}
#textChatHint {
  font-size: 10px;
  color: #8aa0b2;
  margin-right: auto;
}
```

- [ ] **Step 3: app.js DOM 引用与 composer 装配**

47-48 行区域追加引用:

```js
  const textChatAttachments = document.getElementById('textChatAttachments');
  const textChatAttachBtn = document.getElementById('textChatAttachBtn');
```

在 `initAutocomplete(textChatInput, sendTextChatMessage);`(约 3393 行)之前插入:

```js
  // ── Chat composer: 多行/附件/剪贴板 ──
  let composerToastTimer = null;
  const composer = initChatComposer({
    textarea: textChatInput,
    chipContainer: textChatAttachments,
    barElement: document.getElementById('textChatBar'),
    pickButton: textChatAttachBtn,
    getAutoAttachEnabled: () => String((runtimeLive2DConfig || {}).AUTO_IMAGE_ATTACH_ENABLED ?? 'true').toLowerCase() !== 'false',
    onHeightChange: () => updateTextChatBarPosition(),
    toast: (msg) => {
      if (textChatStatus) {
        textChatStatus.textContent = msg;
        clearTimeout(composerToastTimer);
        composerToastTimer = setTimeout(() => { if (textChatStatus) textChatStatus.textContent = '文字待命'; }, 2500);
      }
    },
  });
```

同文件顶部 import 区(与 `initAutocomplete` 的 import 同处)加:

```js
import { initChatComposer } from './libs/chat-composer.js';
```

- [ ] **Step 4: sendTextChatMessage 附加路径**

`sendTextChatMessage()`(2495 行)中,`const text = (textChatInput.value || '').trim();` 之后插入:

```js
    const atts = composer ? composer.getAttachments() : [];
    const fullText = atts.length
      ? text + '\n' + atts.map((a) => `[附件] ${a.path}`).join('\n')
      : text;
```

函数体内后续所有用到 `text` 的地方(`showResultBubble('user', text)`、`sendToChat(text)`)改传 `fullText`;发送成功后(`textChatInput.value = '';` 处)追加:

```js
      if (composer) composer.clear();
```

- [ ] **Step 5: 定位锚定底边(向上生长)**

`updateTextChatBarPosition()`(3151 行)两处分支(vrm 与 live2d)中,把 `const size = uiWidgetManager.getWidgetSize('text-chat-bar', { width: 420, height: 64 });` 之后各加一行,并用 `centerY` 替换传入 `clampToViewport` 的 `waistY`:

```js
      const barHeight = (textChatBar.offsetHeight || size.height) * chatScale;
      const centerY = waistY + (size.height * chatScale) / 2 - barHeight / 2;
```

`clampToViewport(clientX, waistY, ...)` → `clampToViewport(clientX, centerY, ...)`;`textChatBar.style.top = Math.round(clamped.top) + 'px';` 不变(默认高度 64px 时 centerY === waistY,布局与现状完全一致)。

- [ ] **Step 6: 语法检查**

Run: `cd frontend && node scripts/check-js-syntax.js`
Expected: `All files OK.`

- [ ] **Step 7: Commit**

```bash
git add frontend/index.html frontend/styles.css frontend/app.js
git commit -m "feat: 聊天条多行输入与附件接线"
```

---

### Task 4: Tab 补全(autocomplete.js)

**Files:**
- Modify: `frontend/libs/autocomplete.js`(keydown 处理器与 input 处理器)

**Interfaces:**
- Consumes: 无新依赖
- Produces: Tab = 接受当前高亮项(无高亮则首项);文本以 `/` 开头且下拉未显示时 Tab 拉起下拉;`isComposing` 守卫

- [ ] **Step 1: 抽出防抖拉取并加 Tab/isComposing**

把 `input` 事件监听器中 `clearTimeout(acPending); acPending = setTimeout(...)` 的逻辑抽为模块内函数:

```js
function acTrigger(inputEl) {
  clearTimeout(acPending);
  acPending = setTimeout(async () => {
    acPending = null;
    const items = await acFetch(inputEl.value, inputEl.selectionStart || inputEl.value.length);
    acRender(items, inputEl);
  }, 200);
}
```

`input` 监听器改为:`if (!val.startsWith('/')) { acRemoveDropdown(); return; } acTrigger(inputEl);`

`keydown` 监听器开头加 IME 守卫,并在 `if (acDropdown) {` 块内、`Escape` 分支后加 Tab 分支:

```js
    if (e.isComposing) return;
```

```js
      if (e.key === 'Tab') {
        e.preventDefault();
        acSelect(acIndex >= 0 ? acIndex : 0, inputEl);
        return;
      }
```

`keydown` 末尾(Enter 发送判断之前)加:

```js
    if (e.key === 'Tab' && (inputEl.value || '').startsWith('/')) {
      e.preventDefault();
      acTrigger(inputEl);
      return;
    }
```

- [ ] **Step 2: 语法检查**

Run: `cd frontend && node scripts/check-js-syntax.js`
Expected: `All files OK.`

- [ ] **Step 3: Commit**

```bash
git add frontend/libs/autocomplete.js
git commit -m "feat: Tab 接受补全建议并支持拉起下拉;IME 合成守卫"
```

---

### Task 5: 配置项 AUTO_IMAGE_ATTACH_ENABLED

**Files:**
- Modify: `backend/faust_backend/admin_runtime.py:88` 附近(RUNTIME 默认值字典)
- Modify: `frontend/libs/configer/constants.js`(META 与 LIVE2D_KEYS)

**Interfaces:**
- Produces: runtime public config 键 `AUTO_IMAGE_ATTACH_ENABLED`(bool,默认 true),经 `loadRuntimeLive2DConfig()`(app.js)到达 Task 3 的 `getAutoAttachEnabled`

- [ ] **Step 1: 后端默认值**

`admin_runtime.py` 的 RUNTIME 默认值字典中 `"FRONTEND_DEFAULT_TTS_LANG": "zh",` 一行后加:

```python
    "AUTO_IMAGE_ATTACH_ENABLED": True,
```

- [ ] **Step 2: 前端 META 与分组**

`constants.js` META 中 `LIVE2D_MOUSE_TRACKING_STRENGTH` 条目后加:

```js
  AUTO_IMAGE_ATTACH_ENABLED: { label: "剪贴板图片自动附加", help: "聊天输入框获得焦点时,自动把剪贴板中的图片/文件作为附件(路径)附到消息;关闭后仅 Ctrl+V 手动粘贴生效。" },
```

`LIVE2D_KEYS` 数组末尾(`"FRONTEND_DEFAULT_TTS_LANG"` 后)加 `"AUTO_IMAGE_ATTACH_ENABLED"`。

- [ ] **Step 3: 跑后端全量测试**

Run: `.runtime/python.exe -m pytest backend/tests/ -q`
Expected: 全部 PASS(新增默认值不影响既有断言)

- [ ] **Step 4: 语法检查**

Run: `cd frontend && node scripts/check-js-syntax.js`
Expected: `All files OK.`

- [ ] **Step 5: Commit**

```bash
git add backend/faust_backend/admin_runtime.py frontend/libs/configer/constants.js
git commit -m "feat: AUTO_IMAGE_ATTACH_ENABLED 配置项(剪贴板图片自动附加开关)"
```

---

### Task 6: CDP 冒烟验证(集成)

**Files:** 无新改动;验证 Task 1-5 的端到端行为。

- [ ] **Step 1: 启动后端与前端**

```bash
.runtime/python.exe -m uvicorn backend.main:app --port 13900  # 或项目惯用启动方式
cd frontend && npx electron . --remote-debugging-port=9222
```

- [ ] **Step 2: 浏览器连接验证**

用 browser/agent-browser 连接 `http://127.0.0.1:9222`,逐项验证:

1. 输入多行文本(Shift+Enter 换行)→ 聊天条向上生长、6 行封顶滚动
2. 系统剪贴板放一张截图 → 聚焦输入框 → 出现绿色图片胶囊,`~/.faustbot/uploads/` 出现 `clip-*.png`
3. 关闭配置中心「剪贴板图片自动附加」→ 重新聚焦 → 不自动附加;Ctrl+V 仍附加
4. 输入 `/` → 下拉出现;Tab 接受首项;↑↓ 可换选
5. 带附件发送 → 消息气泡与后端收到的文本末尾含 `[附件] <路径>`;发送后胶囊清空

- [ ] **Step 3: 收尾**

关闭前端与后端;若验证失败,回到对应任务修复后重跑本任务。

- [ ] **Step 4: 最终提交**

```bash
git add -A
git commit -m "feat: 聊天输入框增强完成(冒烟通过)"
```
