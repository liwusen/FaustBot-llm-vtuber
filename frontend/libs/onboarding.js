// 交互式功能引导 — 游戏化新手教程（遮罩高亮 + 说明气泡 + 进度点 + 实操按钮）
// 用法: var ob = initOnboarding({ onComplete: fn }); ob.start(steps);
// 步骤: { title, body, target?, action?, actionLabel?, actionHint? }
//   有 action 的步骤：遮罩 pointer-events:none（passive），用户可自由操作页面，
//   气泡显示"让我试试"按钮；无 action 的步骤：全遮罩，点遮罩 = 下一步。
// 按钮语义: 跳过=本次退出不标记; 不再提示=退出并 onComplete(); 最后一步"完成"= onComplete()。

function initOnboarding(options = {}) {
  const onComplete = typeof options.onComplete === 'function' ? options.onComplete : null;
  const onChange = typeof options.onChange === 'function' ? options.onChange : null;

  let root = null;
  let overlay = null;
  let highlight = null;
  let bubble = null;
  let titleEl = null;
  let bodyEl = null;
  let dotsEl = null;
  let progressEl = null;
  let skipBtn = null;
  let dismissBtn = null;
  let nextBtn = null;
  let tryBtn = null;

  let steps = [];
  let index = -1;
  let active = false;

  function buildDom() {
    root = document.createElement('div');
    root.className = 'onboarding-root';
    root.style.display = 'none';

    overlay = document.createElement('div');
    overlay.className = 'onboarding-overlay';

    highlight = document.createElement('div');
    highlight.className = 'onboarding-highlight';
    highlight.style.display = 'none';

    bubble = document.createElement('div');
    bubble.className = 'onboarding-bubble';
    bubble.innerHTML =
      '<div class="onboarding-bubble-title"></div>' +
      '<div class="onboarding-bubble-body"></div>' +
      '<div class="onboarding-bubble-try" style="display:none">' +
        '<button type="button" class="onboarding-btn-try">让我试试</button>' +
      '</div>' +
      '<div class="onboarding-bubble-actions">' +
        '<button type="button" class="onboarding-btn-skip">跳过</button>' +
        '<button type="button" class="onboarding-btn-dismiss">不再提示</button>' +
        '<span class="onboarding-bubble-dots"></span>' +
        '<span class="onboarding-progress"></span>' +
        '<button type="button" class="onboarding-btn-next">下一步</button>' +
      '</div>';
    titleEl = bubble.querySelector('.onboarding-bubble-title');
    bodyEl = bubble.querySelector('.onboarding-bubble-body');
    dotsEl = bubble.querySelector('.onboarding-bubble-dots');
    progressEl = bubble.querySelector('.onboarding-progress');
    skipBtn = bubble.querySelector('.onboarding-btn-skip');
    dismissBtn = bubble.querySelector('.onboarding-btn-dismiss');
    nextBtn = bubble.querySelector('.onboarding-btn-next');
    tryBtn = bubble.querySelector('.onboarding-btn-try');

    root.append(overlay, highlight, bubble);
    document.body.appendChild(root);

    overlay.addEventListener('click', onOverlayClick);
    skipBtn.addEventListener('click', skip);
    dismissBtn.addEventListener('click', dismiss);
    nextBtn.addEventListener('click', next);
    tryBtn.addEventListener('click', onTryClick);
    window.addEventListener('resize', onResize);
    document.addEventListener('keydown', onKeyDown);
  }

  function setActive(on) {
    active = on;
    if (!root) return;
    root.style.display = on ? 'block' : 'none';
    if (onChange) onChange(on);
  }

  function start(stepDefs) {
    if (!root) buildDom();
    steps = Array.isArray(stepDefs) ? stepDefs : [];
    if (steps.length === 0) return;
    index = 0;
    renderStep();
    setActive(true);
  }

  function stop() {
    setActive(false);
    if (highlight) highlight.style.display = 'none';
    index = -1;
  }

  function next() {
    if (index < 0 || !active) return;
    if (index >= steps.length - 1) { finish(); return; }
    index += 1;
    renderStep();
  }

  function skip() { stop(); } // 本次退出，不标记

  function dismiss() { stop(); if (onComplete) onComplete(); } // 不再提示

  function finish() { stop(); if (onComplete) onComplete(); } // 完成

  function onTryClick() {
    const step = steps[index];
    if (!step) return;
    if (typeof step.action === 'function') {
      try { step.action(); } catch (e) { console.warn('onboarding action failed', e); }
    }
    if (step.actionHint) bodyEl.textContent = step.actionHint;
  }

  function onOverlayClick(ev) {
    if (ev.target !== overlay) return;
    if (overlay.classList.contains('passive')) return; // 实操步骤让用户自由操作
    next();
  }

  function onKeyDown(ev) {
    if (!active) return;
    if (ev.key === 'Escape') { ev.preventDefault(); skip(); }
    else if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); next(); }
  }

  function onResize() {
    if (!active || index < 0) return;
    renderStep();
  }

  function renderStep() {
    const step = steps[index];
    titleEl.textContent = step.title || '';
    bodyEl.textContent = step.body || '';
    dotsEl.innerHTML = '';
    steps.forEach((_, i) => {
      const d = document.createElement('span');
      d.className = 'onboarding-dot' + (i === index ? ' active' : '');
      dotsEl.appendChild(d);
    });
    progressEl.textContent = (index + 1) + ' / ' + steps.length;

    const hasAction = typeof step.action === 'function';
    bubble.querySelector('.onboarding-bubble-try').style.display = hasAction ? '' : 'none';
    tryBtn.textContent = step.actionLabel || '让我试试';
    overlay.classList.toggle('passive', hasAction); // 实操步骤遮罩穿透
    nextBtn.textContent = index >= steps.length - 1 ? '完成' : '下一步';

    const target = step.target ? document.querySelector(step.target) : null;
    positionHighlight(target);
    positionBubble(target);
  }

  function positionHighlight(target) {
    if (!target) { highlight.style.display = 'none'; return; }
    const rect = target.getBoundingClientRect();
    highlight.style.display = 'block';
    highlight.style.left = rect.left + 'px';
    highlight.style.top = rect.top + 'px';
    highlight.style.width = rect.width + 'px';
    highlight.style.height = rect.height + 'px';
  }

  function positionBubble(target) {
    if (!target) {
      bubble.classList.remove('with-target');
      bubble.style.left = '50%';
      bubble.style.top = '38%';
      bubble.style.transform = 'translate(-50%, -50%)';
      return;
    }
    const rect = target.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const p = computeBubblePlacement(rect, vw, vh);
    bubble.classList.remove('with-target');
    bubble.classList.add('with-target', 'side-' + p.side);
    bubble.style.left = p.left + 'px';
    bubble.style.top = p.top + 'px';
    bubble.style.transform = 'none';
  }

  return { start, stop, next, skip, isActive: () => active };
}

// 纯函数：计算气泡摆放方位与坐标（供单元测试与复用）
// rect: getBoundingClientRect 结果；vw/vh: 视口尺寸
// 返回 { side: 'top'|'bottom', left, top }
function computeBubblePlacement(rect, vw, vh, opts = {}) {
  const bw = opts.bubbleWidth || 340;
  const bh = opts.bubbleHeight || 150;
  const gap = opts.gap || 14;
  const side = (rect.bottom + bh + gap > vh && rect.top - bh - gap >= 0) ? 'top' : 'bottom';
  const left = Math.max(8, Math.min(rect.left + rect.width / 2 - bw / 2, vw - bw - 8));
  const top = side === 'bottom' ? rect.bottom + gap : Math.max(8, rect.top - bh - gap);
  return { side, left, top };
}
