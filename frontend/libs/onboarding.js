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
  let repositionTimer = null;

  let steps = [];
  let index = -1;
  let active = false;

  function buildStyles() {
    if (document.getElementById('onboarding-styles')) return;
    const style = document.createElement('style');
    style.id = 'onboarding-styles';
    style.textContent =
      '.onboarding-root{position:fixed;inset:0;z-index:100000}' +
      '.onboarding-overlay{position:absolute;inset:0;background:rgba(10,14,24,.55);cursor:pointer}' +
      '.onboarding-overlay.passive{pointer-events:none}' +
      '.onboarding-highlight{position:fixed;border:2px solid #4fc3f7;border-radius:12px;box-shadow:0 0 0 4px rgba(79,195,247,.25),0 0 24px rgba(79,195,247,.6);pointer-events:none;z-index:100001}' +
      '.onboarding-bubble{position:fixed;width:340px;min-height:120px;background:#fff;color:#1c2333;border-radius:14px;box-shadow:0 8px 32px rgba(0,0,0,.35);padding:14px 16px;z-index:100002;font-size:14px;line-height:1.5}' +
      '.onboarding-bubble-title{font-size:16px;font-weight:700;margin-bottom:6px}' +
      '.onboarding-bubble-body{margin-bottom:12px;white-space:pre-line}' +
      '.onboarding-bubble-try{margin-bottom:10px}' +
      '.onboarding-bubble-try button{border:none;border-radius:8px;padding:6px 14px;font-size:13px;cursor:pointer;background:#ffe082;color:#5d4300;font-weight:600}' +
      '.onboarding-bubble-actions{display:flex;align-items:center;gap:8px}' +
      '.onboarding-bubble-actions button{border:none;border-radius:8px;padding:6px 14px;font-size:13px;cursor:pointer}' +
      '.onboarding-btn-skip{background:transparent;color:#8a93a6}' +
      '.onboarding-btn-dismiss{background:transparent;color:#8a93a6;border:1px solid #d5dbe6!important}' +
      '.onboarding-btn-next{background:#4fc3f7;color:#10202e;font-weight:600;margin-left:auto}' +
      '.onboarding-bubble-dots{display:inline-flex;gap:5px;margin:0 8px}' +
      '.onboarding-dot{width:7px;height:7px;border-radius:50%;background:#c9d2e0}' +
      '.onboarding-dot.active{background:#4fc3f7}' +
      '.onboarding-progress{font-size:12px;color:#8a93a6}';
    document.head.appendChild(style);
  }

  function buildDom() {
    buildStyles();
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
    positionOnTarget(target);
  }

  // 定位高亮框与气泡；目标在视口外时平滑滚动过去后重新定位
  function positionOnTarget(target) {
    if (!target) {
      highlight.style.display = 'none';
      bubble.classList.remove('with-target');
      bubble.style.left = '50%';
      bubble.style.top = '38%';
      bubble.style.transform = 'translate(-50%, -50%)';
      return;
    }
    const rect = target.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const offscreen = rect.bottom < 0 || rect.top > vh || rect.right < 0 || rect.left > vw;
    if (offscreen) {
      target.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      clearTimeout(repositionTimer);
      repositionTimer = setTimeout(() => {
        if (!active) return;
        positionOnTarget(target);
      }, 360);
      return;
    }
    highlight.style.display = 'block';
    highlight.style.left = rect.left + 'px';
    highlight.style.top = rect.top + 'px';
    highlight.style.width = rect.width + 'px';
    highlight.style.height = rect.height + 'px';
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
