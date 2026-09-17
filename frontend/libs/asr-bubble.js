// ASR/结果气泡模块 — 承载结果气泡（#asrBubble / #asrText）的增量渲染、mermaid 水合与平滑定位。
// 用法: const bubble = initAsrBubble({ uiWidgetManager, onLayoutDirty, onHideSubagentSummary,
//                                      getModelType, getVrmScene, getCurrentModel, getApp,
//                                      live2DToClient, updateHilPosition });
// 说明：气泡动画坐标、滚动意图、mermaid 初始化状态全部内聚在本模块；
// 模型/场景等外部状态一律通过注入的 getter 读取，模块内不引用 app.js 闭包变量。
//
// 显隐（DOM 节点常驻）：#asrBubble 的 display 只由 applyBubbleVisibility() 决定
//   visible = widget.hidden ? 编辑态幽灵预览 : 气泡内有可渲染内容（asrText 条目 / subagent 摘要）
// hidden 是 uiWidget 的运行时状态：× 按钮置 true；chat 流 start、Faust Command SAY / MD_BLOCK
// 各调一次 reveal() 置 false。hidden 不落盘（见 app.js 的 TRANSIENT_HIDDEN_WIDGETS）。
//
// 滚动：只有「贴底」时才跟随流式输出，并用 rAF 指数骑近逼近；用户上滚后保持 scrollTop 不变，
// 视口稳定停在用户滚到的内容上。

import { renderResultBubbleHtml, cloneBubbleEntries, entryKey, entryHash, renderBubbleEntryHtml, patchBubbleEntryNode } from './bubble-utils.js';

const ASR_SNAP_THRESHOLD = 0.5;
const SCROLL_PIN_THRESHOLD = 24;    // 距底多少 px 内算「贴底」
const SCROLL_EASE = 0.25;           // 每帧走剩余距离的比例
const SCROLL_MIN_STEP = 1;          // 每帧最小步长(px)
const SCROLL_EPSILON = 0.5;         // 距目标 <= 该值直接吸附
const SCROLL_INTENT_GRACE_MS = 120; // 程序化滚动后忽略 scroll 事件的时长

export function initAsrBubble({
  uiWidgetManager,
  onLayoutDirty,
  onHideSubagentSummary,
  getModelType,
  getVrmScene,
  getCurrentModel,
  getApp,
  live2DToClient,
  updateHilPosition,
}) {
  const asrBubbleEl = document.getElementById('asrBubble');
  const asrTextEl = document.getElementById('asrText');
  const subagentSummaryEl = document.getElementById('subagentSummary');

  let asrBubbleCurrentX = 0;
  let asrBubbleCurrentY = 0;
  let asrBubbleTargetX = 0;
  let asrBubbleTargetY = 0;
  let asrBubbleInitialized = false;
  let asrBubbleSource = 'ai';
  let asrBubbleState = { source: 'ai', entries: [] };
  let asrTextHasContent = false;
  let asrTextPinnedToBottom = true;
  let mermaidInitialized = false;
  let mermaidSeq = 0;
  let scrollAnimRafId = null;
  let lastAutoScrollTs = 0;
  let pendingToggleAnchor = null;

  // ── 显隐 ───────────────────────────────────────────────

  function summaryHasContent(){
    if (!subagentSummaryEl) return false;
    if (subagentSummaryEl.style.display === 'none') return false;
    return subagentSummaryEl.childElementCount > 0;
  }

  function computeBubbleVisible(widget){
    const w = widget || uiWidgetManager.getWidget('asr-bubble');
    const hidden = !!(w && w.hidden);
    if (hidden) return uiWidgetManager.isEditMode();
    return asrTextHasContent || summaryHasContent();
  }

  // #asrBubble 的 display 唯一写入点（隐藏态非编辑模式下整体不显示）
  function applyBubbleVisibility(widget){
    if (!asrBubbleEl) return false;
    const w = widget || uiWidgetManager.getWidget('asr-bubble');
    const hidden = !!(w && w.hidden);
    const editMode = uiWidgetManager.isEditMode();
    const visible = computeBubbleVisible(w);
    asrBubbleEl.classList.toggle('ui-widget-hidden-preview', hidden && editMode);
    asrBubbleEl.style.display = visible ? 'flex' : 'none';
    return visible;
  }

  function isVisible(){
    return computeBubbleVisible();
  }

  // ── 滚动 ───────────────────────────────────────────────

  function maxScrollTop(){
    if (!asrTextEl) return 0;
    return Math.max(0, asrTextEl.scrollHeight - asrTextEl.clientHeight);
  }

  function cancelScrollAnim(){
    if (scrollAnimRafId === null) return;
    cancelAnimationFrame(scrollAnimRafId);
    scrollAnimRafId = null;
  }

  function writeScrollTop(value){
    asrTextEl.scrollTop = value;
    lastAutoScrollTs = performance.now();
  }

  function snapScrollToBottom(){
    cancelScrollAnim();
    writeScrollTop(maxScrollTop());
  }

  // 指数骑近：每帧走剩余距离的固定比例，目标每帧重取（流式持续输出时可稳定跟随且终点平滑）
  function followScrollToBottom(){
    if (!asrTextEl || scrollAnimRafId !== null) return;
    const step = () => {
      scrollAnimRafId = null;
      const delta = maxScrollTop() - asrTextEl.scrollTop;
      if (delta <= SCROLL_EPSILON) {
        writeScrollTop(maxScrollTop());
        return;
      }
      writeScrollTop(asrTextEl.scrollTop + Math.min(delta, Math.max(SCROLL_MIN_STEP, delta * SCROLL_EASE)));
      scrollAnimRafId = requestAnimationFrame(step);
    };
    scrollAnimRafId = requestAnimationFrame(step);
  }

  function rememberAsrScrollIntent(){
    if (!asrTextEl) return;
    // 程序化滚动（吸附/骑近）自己触发的 scroll 事件不代表用户意图
    if (performance.now() - lastAutoScrollTs < SCROLL_INTENT_GRACE_MS) return;
    asrTextPinnedToBottom = (maxScrollTop() - asrTextEl.scrollTop) <= SCROLL_PIN_THRESHOLD;
    if (asrTextPinnedToBottom) cancelScrollAnim();
  }

  // 用户主动滚动输入：立即停掉骑近动画，并让紧随其后的 scroll 事件重新判定贴底状态
  function noteUserScrollInput(){
    cancelScrollAnim();
    lastAutoScrollTs = 0;
  }

  // ── 展开/折叠 ──────────────────────────────────────────

  // click 阶段（details 默认动作之前）记录卡片位置，toggle 之后据此抵消布局位移
  function rememberToggleAnchor(ev){
    const target = ev.target;
    const details = target && target.closest ? target.closest('details.thinking-details') : null;
    if (!details || !details.isConnected) return;
    pendingToggleAnchor = { details, top: details.getBoundingClientRect().top };
  }

  function restoreToggleAnchor(details){
    const anchor = pendingToggleAnchor;
    pendingToggleAnchor = null;
    if (!anchor || anchor.details !== details || !asrTextEl) return;
    const delta = details.getBoundingClientRect().top - anchor.top;
    if (Math.abs(delta) < 1) return;
    writeScrollTop(asrTextEl.scrollTop + delta);
  }

  function handleResultBubbleToggle(ev){
    // 折叠/展开改变气泡尺寸 → 几何重绘（置于最前，任何 details toggle 都覆盖）
    onLayoutDirty();
    const details = ev.target;
    if (!details || !details.classList) return;
    if (details.dataset && details.dataset.callId) {
      const callId = String(details.dataset.callId || '');
      if (!callId || !Array.isArray(asrBubbleState.entries)) return;
      for (const entry of asrBubbleState.entries){
        if (entry && entry.type === 'tool' && String(entry.callId || '') === callId) {
          entry.expanded = details.open;
          break;
        }
      }
      restoreToggleAnchor(details);
      return;
    }
    if (details.dataset && details.dataset.r !== undefined) {
      const rIdx = parseInt(details.dataset.r, 10);
      if (!isNaN(rIdx) && Array.isArray(asrBubbleState.entries)) {
        let count = -1;
        for (const entry of asrBubbleState.entries) {
          if (entry && entry.type === 'reasoning') {
            count++;
            if (count === rIdx) {
              entry.expanded = details.open;
              break;
            }
          }
        }
      }
      restoreToggleAnchor(details);
    }
  }

  // ── 生命周期 ───────────────────────────────────────────

  // × 按钮：隐藏气泡（uiWidget 的 hidden 是唯一开关）
  function hideResultBubble(){
    if (!asrBubbleEl) return;
    uiWidgetManager.updateWidget('asr-bubble', { hidden: true });
    if (typeof onHideSubagentSummary === 'function') onHideSubagentSummary();
    asrBubbleInitialized = false;
    cancelScrollAnim();
    applyBubbleVisibility();
  }

  // 新消息（chat 流 start / SAY / MD_BLOCK）：唯一把 hidden 置回 false 的入口
  function reveal(){
    uiWidgetManager.updateWidget('asr-bubble', { hidden: false });
    asrBubbleInitialized = false;
    asrTextPinnedToBottom = true;
    const visible = applyBubbleVisibility();
    if (visible) {
      uiWidgetManager.applyLayout('asr-bubble');
      snapScrollToBottom();
    }
    onLayoutDirty();
  }

  // AsrBubble 用户可调属性（布景台可编辑，持久化到 /faust/ui-setting）
  function getAsrBubbleProps(){
    const widget = uiWidgetManager.getWidget('asr-bubble');
    const p = (widget && widget.props) || {};
    return {
      fontSize: Number(p.fontSize) > 0 ? Number(p.fontSize) : 20,
      textColor: String(p.textColor || '#000000'),
      whiteBackground: p.whiteBackground !== false,
      aspectRatio: String(p.aspectRatio || '').trim(),
      showReasoning: p.showReasoning !== false,
      showTools: p.showTools !== false,
      showSubagents: p.showSubagents !== false,
      hideScrollbar: p.hideScrollbar === true,
    };
  }

  // 把 AsrBubble 属性应用到元素（字体大小 / 白色背景 / 长宽比 / 滚动条）
  function applyAsrBubbleProps(){
    if (!asrBubbleEl || !asrTextEl) return;
    const props = getAsrBubbleProps();
    asrTextEl.style.fontSize = props.fontSize + 'px';
    asrTextEl.style.color = props.textColor;
    asrBubbleEl.classList.toggle('asr-bubble-no-bg', !props.whiteBackground);
    asrBubbleEl.classList.toggle('asr-bubble-hide-scrollbar', props.hideScrollbar);
    // 长宽比：CSS aspect-ratio 对由内容撑开的 flex 容器不生效，
    // 改为按固定宽度(350px)显式计算高度
    const parts = String(props.aspectRatio || '').split('/').map((s) => parseFloat(s.trim()));
    if (parts.length === 2 && parts[0] > 0 && parts[1] > 0) {
      const w = asrBubbleEl.offsetWidth || 350;
      asrBubbleEl.style.height = Math.round(w * parts[1] / parts[0]) + 'px';
      asrBubbleEl.style.aspectRatio = 'auto';
    } else {
      asrBubbleEl.style.height = '';
      asrBubbleEl.style.aspectRatio = '';
    }
  }

  // 按 entry key/hash 对齐更新气泡子元素：
  // 同键 → 就地打补丁（保留 <details> 与 open 状态）；键变化 → 从该位起重建；多余节点移除
  function applyBubbleEntriesDiff(source, entries) {
    const children = Array.from(asrTextEl.children);
    let reasoningIdx = 0;
    for (let i = 0; i < entries.length; i++) {
      const entry = entries[i];
      const key = entryKey(source, entry, i);
      const hash = entryHash(entry, source);
      const isReasoning = !!(entry && entry.type === 'reasoning');
      const entryReasoningIdx = isReasoning ? reasoningIdx++ : reasoningIdx;
      let el = children[i];
      if (el && el.dataset.entryKey !== key) {
        for (let j = i; j < children.length; j++) children[j].remove();
        children.length = i;
        el = undefined;
      }
      if (!el) {
        el = document.createElement('div');
        el.dataset.entryKey = key;
        el.dataset.entryHash = hash;
        el.innerHTML = renderBubbleEntryHtml(source, entry, i, entryReasoningIdx);
        asrTextEl.appendChild(el);
        children.push(el);
        hydrateNewBubbleNode(el);
        continue;
      }
      if (el.dataset.entryHash === hash) continue;
      if (!patchBubbleEntryNode(el, source, entry, i, entryReasoningIdx)) {
        const node = document.createElement('div');
        node.dataset.entryKey = key;
        node.dataset.entryHash = hash;
        node.innerHTML = renderBubbleEntryHtml(source, entry, i, entryReasoningIdx);
        el.replaceWith(node);
        el = node;
        children[i] = node;
      }
      el.dataset.entryHash = hash;
      hydrateNewBubbleNode(el);
    }
    for (let i = entries.length; i < children.length; i++) children[i].remove();
  }

  // 只 hydrate 新插入/更新的 md-block（局部），不再全量
  function hydrateNewBubbleNode(node) {
    const isMdBlock = !!(node.classList && node.classList.contains('md-block'));
    if (!isMdBlock && !node.querySelector('.md-block')) return;
    try {
      hydrateMermaidBlocks(node, { rootIsMdBlock: isMdBlock });
    } catch (e) {
      console.warn('[md-block] hydrate failed（不影响显示）', e);
    }
  }

  function showResultBubble(source, entries){
    if (!asrTextEl || !asrBubbleEl) return;
    asrBubbleSource = source || 'ai';
    asrBubbleEl.dataset.source = asrBubbleSource;
    const normalizedEntries = Array.isArray(entries)
      ? entries
      : (String(entries || '').trim() ? [{ type: 'text', text: String(entries || '') }] : []);
    asrBubbleState = {
      source: asrBubbleSource,
      entries: cloneBubbleEntries(normalizedEntries),
    };
    // 按用户属性过滤渲染内容（推理 / 工具调用）
    const props = getAsrBubbleProps();
    let renderEntries = asrBubbleState.entries;
    if (props.showReasoning === false) renderEntries = renderEntries.filter((e) => e.type !== 'reasoning');
    if (props.showTools === false) renderEntries = renderEntries.filter((e) => e.type !== 'tool');
    // 渲染结果同时充当「是否有可显示内容」的判据（空文本 / 全被过滤 → 空串）
    const html = renderResultBubbleHtml(asrBubbleSource, renderEntries);
    // 先按当前 DOM 判定滚动意图，再改内容
    rememberAsrScrollIntent();
    asrTextHasContent = html !== '';
    if (html) {
      applyBubbleEntriesDiff(asrBubbleSource, renderEntries);
    } else {
      asrTextEl.innerHTML = '';
      asrTextPinnedToBottom = true;
    }
    onLayoutDirty();
    if (!applyBubbleVisibility()) return;
    // 定位/属性统一走 onLayout 钩子（updateAsrTextPosition 内会 applyAsrBubbleProps）
    uiWidgetManager.applyLayout('asr-bubble');
    // 贴底才跟随（未贴底时保持 scrollTop 不变，视口停在用户滚到的内容上）
    if (asrTextPinnedToBottom) followScrollToBottom();
  }

  // 把备份的 <pre><code> 元素重新插回 DOM（mermaid 渲染失败/节点脱离时恢复原文）
  function restoreMermaidCodeBlock(holder, backupHtml){
    if (!holder || !backupHtml) return;
    try {
      const tmp = document.createElement('div');
      tmp.innerHTML = backupHtml;
      const restored = tmp.firstElementChild;
      if (restored) holder.replaceWith(restored);
    } catch (e) {
      console.warn('[md-block] restore code block failed', e);
    }
  }

  function hydrateMermaidBlocks(root, opts = {}){
    const fm = window.FaustMarkdown;
    if (!root || !fm || !fm.mermaid) return;
    // md entry 节点本身即 .md-block（增量路径传入单节点）：直接查 code.language-mermaid；
    // 全量路径传入容器：查 .md-block 后代
    const codes = opts.rootIsMdBlock
      ? root.querySelectorAll('code.language-mermaid')
      : root.querySelectorAll('.md-block code.language-mermaid');
    if (!codes.length) return;
    if (!mermaidInitialized) {
      fm.mermaid.initialize({ startOnLoad: false, theme: 'neutral' });
      mermaidInitialized = true;
    }
    const nodes = [];
    const backups = [];
    for (const code of codes) {
      const holder = document.createElement('div');
      holder.className = 'mermaid';
      holder.id = `md-mermaid-${++mermaidSeq}`;
      holder.textContent = code.textContent || '';
      const pre = code.closest('pre') || code;
      const backupHtml = pre.outerHTML;
      pre.replaceWith(holder);
      nodes.push(holder);
      backups.push({ holder, backupHtml });
    }
    fm.mermaid.run({ nodes })
      .then(() => {
        // 渲染完成：若节点已脱离文档（气泡被后续消息覆盖），SVG 不可见，
        // 把原代码块恢复回当前 DOM，保证内容可见
        for (const { holder, backupHtml } of backups) {
          if (!holder.isConnected) restoreMermaidCodeBlock(holder, backupHtml);
        }
      })
      .catch((err) => {
        console.warn('[md-block] mermaid render failed:', err);
        // 渲染失败：恢复所有占位节点为原代码块（内容不丢失）
        for (const { holder, backupHtml } of backups) {
          if (holder.isConnected) restoreMermaidCodeBlock(holder, backupHtml);
        }
      });
  }

  function applyBubblePosition(anchorX, anchorY, widget, forceSnap){
    const bubbleWidth = Math.max(uiWidgetManager.getWidgetSize('asr-bubble', { width: 220, height: 120 }).width, 220);
    asrBubbleTargetX = anchorX - bubbleWidth / 2;
    asrBubbleTargetY = anchorY + widget.offset.y;
    if (!asrBubbleInitialized || forceSnap){
      asrBubbleCurrentX = asrBubbleTargetX;
      asrBubbleCurrentY = asrBubbleTargetY;
      asrBubbleInitialized = true;
    } else {
      const dx = asrBubbleTargetX - asrBubbleCurrentX;
      const dy = asrBubbleTargetY - asrBubbleCurrentY;
      if (Math.abs(dx) < ASR_SNAP_THRESHOLD && Math.abs(dy) < ASR_SNAP_THRESHOLD) {
        asrBubbleCurrentX = asrBubbleTargetX;
        asrBubbleCurrentY = asrBubbleTargetY;
      } else {
        asrBubbleCurrentX += dx * 0.2;
        asrBubbleCurrentY += dy * 0.2;
      }
    }
    const left = Math.round(asrBubbleCurrentX) + 'px';
    const top = Math.round(asrBubbleCurrentY) + 'px';
    const transform = `translate3d(0,0,0) scale(${widget.scale || 1})`;
    if (asrBubbleEl.style.left === left && asrBubbleEl.style.top === top && asrBubbleEl.style.transform === transform) return;
    asrBubbleEl.style.left = left;
    asrBubbleEl.style.top = top;
    asrBubbleEl.style.transform = transform;
    updateHilPosition();
  }

  function updateAsrTextPosition(forceSnap = false){
    if (!asrBubbleEl || !asrTextEl) return;
    applyAsrBubbleProps();
    const widget = uiWidgetManager.getWidget('asr-bubble') || { coord: { x: 0.5, y: 0 }, offset: { x: 0, y: -108 }, scale: 1 };
    if (!applyBubbleVisibility(widget)) return;
    const modelType = getModelType();
    const vrmScene = getVrmScene();
    if (modelType === 'vrm' && vrmScene) {
      try{
        const b = vrmScene.getBounds();
        applyBubblePosition(b.x + b.width * widget.coord.x, b.y + b.height * widget.coord.y, widget, forceSnap);
      }catch(e){/*ignore*/}
      return;
    }
    const currentModel = getCurrentModel();
    const app = getApp();
    if (!currentModel || !app || !app.renderer) return;
    try{
      const b = currentModel.getBounds();
      const anchor = live2DToClient(b.x + b.width * widget.coord.x, b.y + b.height * widget.coord.y);
      if (!anchor) return;
      applyBubblePosition(anchor.x, anchor.y, widget, forceSnap);
    }catch(e){/*ignore*/}
  }

  // 模块当前渲染状态（app.js 的 TTS 路径需要读 source/entries 做增量合并）
  function getState(){
    return { source: asrBubbleSource, entries: asrBubbleState.entries };
  }

  // 按当前状态重放一次渲染（属性变化 / 编辑面板改 props 后调用）
  function refresh(){
    const st = getState();
    showResultBubble(st.source, st.entries);
  }

  if (asrTextEl) {
    asrTextEl.addEventListener('scroll', rememberAsrScrollIntent, { passive: true });
    for (const type of ['wheel', 'pointerdown', 'touchstart', 'keydown']) {
      asrTextEl.addEventListener(type, noteUserScrollInput, { passive: true });
    }
  }
  if (asrBubbleEl) {
    asrBubbleEl.addEventListener('click', rememberToggleAnchor, true);
  }
  // subagent 摘要由 subagent-panel 直接改写，内容/显隐变化时同步气泡可见性
  if (typeof MutationObserver === 'function' && subagentSummaryEl) {
    new MutationObserver(() => { applyBubbleVisibility(); }).observe(subagentSummaryEl, {
      childList: true,
      attributes: true,
      attributeFilter: ['style', 'class'],
    });
  }
  applyBubbleVisibility();

  return {
    showResultBubble,
    refresh,
    reveal,
    isVisible,
    hideResultBubble,
    updateAsrTextPosition,
    getAsrBubbleProps,
    applyAsrBubbleProps,
    handleResultBubbleToggle,
    getState,
  };
}
