// ASR/结果气泡模块 — 承载结果气泡（#asrBubble / #asrText）的增量渲染、mermaid 水合与平滑定位。
// 用法: const bubble = initAsrBubble({ uiWidgetManager, onLayoutDirty, onHideSubagentSummary,
//                                      getModelType, getVrmScene, getCurrentModel, getApp,
//                                      live2DToClient, updateHilPosition });
// 说明：气泡动画坐标、滚动意图、mermaid 初始化状态全部内聚在本模块；
// 模型/场景等外部状态一律通过注入的 getter 读取，模块内不引用 app.js 闭包变量。

import { formatResultBubbleText, renderResultBubbleHtml, cloneBubbleEntries, entryKey, entryHash, renderBubbleEntryHtml } from './bubble-utils.js';

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

  let asrBubbleCurrentX = 0;
  let asrBubbleCurrentY = 0;
  let asrBubbleTargetX = 0;
  let asrBubbleTargetY = 0;
  let asrBubbleInitialized = false;
  let asrBubbleAnimating = false;
  const ASR_SNAP_THRESHOLD = 0.5;
  let asrBubbleSource = 'ai';
  let asrBubbleState = { source: 'ai', entries: [] };
  let asrTextPinnedToBottom = true;
  let mermaidInitialized = false;
  let mermaidSeq = 0;

  function handleResultBubbleToggle(ev){
    // 折叠/展开改变气泡尺寸 → 几何重绘（置于最前，任何 details toggle 都覆盖）
    onLayoutDirty();
    const details = ev.target;
    if (!details || !details.classList) return;
    // Tool call details
    if (details.dataset && details.dataset.callId) {
      const callId = String(details.dataset.callId || '');
      if (!callId || !Array.isArray(asrBubbleState.entries)) return;
      for (const entry of asrBubbleState.entries){
        if (entry && entry.type === 'tool' && String(entry.callId || '') === callId) {
          entry.expanded = details.open;
          break;
        }
      }
      return;
    }
    // Reasoning card details
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
      return;
    }
  }

  function rememberAsrScrollIntent(){
    if (!asrTextEl) return;
    const threshold = 18;
    const distanceToBottom = asrTextEl.scrollHeight - asrTextEl.scrollTop - asrTextEl.clientHeight;
    asrTextPinnedToBottom = distanceToBottom <= threshold;
  }

  function scrollAsrTextToBottom(force = false){
    if (!asrTextEl) return;
    if (force || asrTextPinnedToBottom){
      asrTextEl.scrollTop = asrTextEl.scrollHeight;
      asrTextPinnedToBottom = true;
    }
  }

  function hideResultBubble(){
    if (!asrBubbleEl) return;
    asrBubbleEl.style.display = 'none';
    if (typeof onHideSubagentSummary === 'function') onHideSubagentSummary();
    asrBubbleInitialized = false;
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
    };
  }

  // 把 AsrBubble 属性应用到元素（字体大小 / 白色背景 / 长宽比）
  function applyAsrBubbleProps(){
    if (!asrBubbleEl || !asrTextEl) return;
    const props = getAsrBubbleProps();
    asrTextEl.style.fontSize = props.fontSize + 'px';
    asrTextEl.style.color = props.textColor;
    asrBubbleEl.classList.toggle('asr-bubble-no-bg', !props.whiteBackground);
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

  // 按 entry key/hash 对齐更新气泡子元素；仅 append 新 entry / replace 变化 entry / 移除多余节点
  function applyBubbleEntriesDiff(source, entries) {
    const keys = entries.map((e, i) => entryKey(source, e, i));
    const hashes = entries.map((e) => entryHash(e, source));
    const children = Array.from(asrTextEl.children);
    let changed = false;
    let reasoningIdx = 0;

    for (let i = 0; i < keys.length; i++) {
      const el = children[i];
      const key = keys[i];
      const hash = hashes[i];
      const isReasoning = !!(entries[i] && entries[i].type === 'reasoning');
      const entryReasoningIdx = isReasoning ? reasoningIdx++ : reasoningIdx;
      if (!el) {
        const node = document.createElement('div');
        node.dataset.entryKey = key;
        node.dataset.entryHash = hash;
        node.innerHTML = renderBubbleEntryHtml(source, entries[i], i, entryReasoningIdx);
        asrTextEl.appendChild(node);
        hydrateNewBubbleNode(node);
        changed = true;
        continue;
      }
      if (el.dataset.entryKey !== key || el.dataset.entryHash !== hash) {
        const node = document.createElement('div');
        node.dataset.entryKey = key;
        node.dataset.entryHash = hash;
        node.innerHTML = renderBubbleEntryHtml(source, entries[i], i, entryReasoningIdx);
        el.replaceWith(node);
        hydrateNewBubbleNode(node);
        changed = true;
      }
    }
    for (let i = keys.length; i < children.length; i++) {
      children[i].remove();
      changed = true;
    }
    if (changed) updateAsrTextPosition(true);
    return changed;
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
    const widget = uiWidgetManager.getWidget('asr-bubble');
    if (widget && widget.hidden && !uiWidgetManager.isEditMode()) return;
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
    // 增量渲染：按 entry key/hash 对齐更新，避免每次全量 innerHTML + markdown 重解析
    const html = renderResultBubbleHtml(asrBubbleSource, renderEntries);
    rememberAsrScrollIntent();
    asrBubbleEl.style.display = html ? 'flex' : 'none';
    if (html) {
      applyBubbleEntriesDiff(asrBubbleSource, renderEntries);
    } else {
      asrTextEl.innerHTML = '';
    }
    onLayoutDirty();
    if (html) {
      applyAsrBubbleProps();
      updateAsrTextPosition(true);
      scrollAsrTextToBottom(true);
    }
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

  function showAsrText(text){
    if (!asrTextEl || !asrBubbleEl) return;
    rememberAsrScrollIntent();
    asrBubbleEl.style.display = text ? 'flex' : 'none';
    asrBubbleEl.dataset.source = 'ai';
    asrBubbleSource = 'ai';
    asrTextEl.textContent = formatResultBubbleText('ai', text || '');
    updateAsrTextPosition(true);
    scrollAsrTextToBottom(true);
  }

  function updateAsrTextPosition(forceSnap = false){
    if (!asrBubbleEl || !asrTextEl) return;
    applyAsrBubbleProps();
    const widget = uiWidgetManager.getWidget('asr-bubble') || { coord: { x: 0.5, y: 0 }, offset: { x: 0, y: -108 }, scale: 1 };
    const editMode = uiWidgetManager.isEditMode();
    asrBubbleEl.classList.toggle('ui-widget-hidden-preview', !!(editMode && widget.hidden));
    if (widget.hidden && !editMode) {
      asrBubbleEl.style.display = 'none';
      return;
    }
    const modelType = getModelType();
    const vrmScene = getVrmScene();
    if (modelType === 'vrm' && vrmScene) {
      try{
        const b = vrmScene.getBounds();
        const clientX = b.x + b.width * widget.coord.x;
        const clientY = b.y + b.height * widget.coord.y;
        const bubbleWidth = Math.max(uiWidgetManager.getWidgetSize('asr-bubble', { width: 220, height: 120 }).width, 220);
        asrBubbleTargetX = clientX - bubbleWidth / 2;
        asrBubbleTargetY = clientY + widget.offset.y;
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
            asrBubbleAnimating = false;
          } else {
            asrBubbleCurrentX += dx * 0.2;
            asrBubbleCurrentY += dy * 0.2;
            asrBubbleAnimating = true;
          }
        }
        if (asrBubbleAnimating || forceSnap || !asrBubbleInitialized) {
          asrBubbleEl.style.left = Math.round(asrBubbleCurrentX) + 'px';
          asrBubbleEl.style.top = Math.round(asrBubbleCurrentY) + 'px';
          asrBubbleEl.style.transform = `translate3d(0,0,0) scale(${widget.scale || 1})`;
          updateHilPosition();
        }
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
      const clientX = anchor.x;
      const clientY = anchor.y;
      const bubbleWidth = Math.max(uiWidgetManager.getWidgetSize('asr-bubble', { width: 220, height: 120 }).width, 220);
      asrBubbleTargetX = clientX - bubbleWidth / 2;
      asrBubbleTargetY = clientY + widget.offset.y;
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
          asrBubbleAnimating = false;
        } else {
          asrBubbleCurrentX += dx * 0.2;
          asrBubbleCurrentY += dy * 0.2;
          asrBubbleAnimating = true;
        }
      }
      if (asrBubbleAnimating || forceSnap || !asrBubbleInitialized) {
        asrBubbleEl.style.left = Math.round(asrBubbleCurrentX) + 'px';
        asrBubbleEl.style.top = Math.round(asrBubbleCurrentY) + 'px';
        asrBubbleEl.style.transform = `translate3d(0,0,0) scale(${widget.scale || 1})`;
        updateHilPosition();
      }
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

  return {
    showResultBubble,
    refresh,
    hideResultBubble,
    showAsrText,
    updateAsrTextPosition,
    getAsrBubbleProps,
    applyAsrBubbleProps,
    handleResultBubbleToggle,
    rememberAsrScrollIntent,
    getState,
  };
}
