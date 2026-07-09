// HIL (Human-In-Loop) 审批系统 — 浮动确认面板
// 用法: const hil = initHilApproval({ feedbackEndpoint });

export function initHilApproval({ feedbackEndpoint }) {
  let hilApprovalQueue = [];
  let activeHilApproval = null;

  function ensureHost() {
    let host = document.getElementById('hil-approval-host');
    if (host) return host;
    host = document.createElement('div');
    host.id = 'hil-approval-host';
    host.style.position = 'fixed';
    host.style.left = '0';
    host.style.top = '0';
    host.style.zIndex = '2600';
    host.style.pointerEvents = 'none';
    document.body.appendChild(host);
    return host;
  }

  function updatePosition() {
    const host = document.getElementById('hil-approval-host');
    if (!host) return;
    const shell = host.querySelector('.hil-approval-shell');
    if (!shell) return;
    const asrBubbleEl = document.getElementById('asrBubble');
    const bubbleVisible = !!(asrBubbleEl && asrBubbleEl.style.display !== 'none');
    const anchorRect = bubbleVisible && asrBubbleEl ? asrBubbleEl.getBoundingClientRect() : null;
    const shellRect = shell.getBoundingClientRect();
    const preferredWidth = Math.min(Math.max(anchorRect ? anchorRect.width : 320, 320), 560);
    shell.style.width = Math.round(preferredWidth) + 'px';
    const measuredRect = shell.getBoundingClientRect();
    const width = measuredRect.width || preferredWidth;
    const height = measuredRect.height || 320;
    const gap = 14;
    let left = anchorRect ? (anchorRect.left + anchorRect.width / 2 - width / 2) : ((window.innerWidth - width) / 2);
    let top = anchorRect ? (anchorRect.top - height - gap) : 80;
    left = Math.max(12, Math.min(window.innerWidth - width - 12, left));
    top = Math.max(12, Math.min(window.innerHeight - height - 12, top));
    host.style.left = Math.round(left) + 'px';
    host.style.top = Math.round(top) + 'px';
  }

  function isPointOver(clientX, clientY) {
    const host = document.getElementById('hil-approval-host');
    if (!host) return false;
    const panel = host.querySelector('.hil-approval-shell');
    if (!panel) return false;
    const rect = panel.getBoundingClientRect();
    return rect.width > 0 && clientX >= rect.left && clientX <= rect.right && clientY >= rect.top && clientY <= rect.bottom;
  }

  async function submitDecision(requestId, approved, reason) {
    const r = await fetch(feedbackEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        request_id: requestId,
        feedback: !!approved,
        reason: String(reason || '').trim() || (approved ? 'approved' : 'rejected'),
      }),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.error) throw new Error((j && (j.detail || j.error)) || `HTTP ${r.status}`);
    return j;
  }

  function close(requestId) {
    const host = document.getElementById('hil-approval-host');
    if (host) host.innerHTML = '';
    if (activeHilApproval && activeHilApproval.request_id === requestId) {
      activeHilApproval = null;
    } else {
      hilApprovalQueue = hilApprovalQueue.filter((item) => item && item.request_id !== requestId);
    }
    window.setTimeout(() => renderNext(), 0);
  }

  function renderNext() {
    if (activeHilApproval || !hilApprovalQueue.length) return;
    const payload = hilApprovalQueue.shift();
    if (!payload || !payload.request_id) return;
    activeHilApproval = payload;
    const host = ensureHost();
    host.innerHTML = '';

    const overlay = document.createElement('div');
    overlay.className = 'hil-approval-overlay';

    const shell = document.createElement('section');
    shell.className = 'hil-approval-shell';
    shell.dataset.requestId = payload.request_id;
    shell.dataset.severity = String(payload.severity || 'warning');

    const title = document.createElement('h3');
    title.className = 'hil-approval-title';
    title.textContent = String(payload.title || '\u9700\u8981\u4EBA\u5DE5\u786E\u8BA4');

    const badge = document.createElement('span');
    badge.className = 'hil-approval-badge';
    badge.textContent = String(payload.severity || 'warning').toUpperCase();

    const summary = document.createElement('pre');
    summary.className = 'hil-approval-summary';
    summary.textContent = String(payload.summary || '');

    const requestMeta = document.createElement('div');
    requestMeta.className = 'hil-approval-meta';
    requestMeta.textContent = `\u8BF7\u6C42ID: ${payload.request_id}`;

    const reasonInput = document.createElement('textarea');
    reasonInput.className = 'hil-approval-reason';
    reasonInput.placeholder = '\u53EF\u9009\uFF1A\u586B\u5199\u5BA1\u6279\u5907\u6CE8\u6216\u62D2\u7EDD\u539F\u56E0';

    const actionRow = document.createElement('div');
    actionRow.className = 'hil-approval-actions';

    const rejectBtn = document.createElement('button');
    rejectBtn.type = 'button';
    rejectBtn.className = 'hil-approval-btn secondary';
    rejectBtn.textContent = '\u62D2\u7EDD';

    const approveBtn = document.createElement('button');
    approveBtn.type = 'button';
    approveBtn.className = 'hil-approval-btn primary';
    approveBtn.textContent = '\u6279\u51C6';

    const setBusy = (busy) => {
      approveBtn.disabled = busy;
      rejectBtn.disabled = busy;
      reasonInput.disabled = busy;
    };

    rejectBtn.onclick = async () => {
      setBusy(true);
      try {
        await submitDecision(payload.request_id, false, reasonInput.value || 'rejected_by_user');
        close(payload.request_id);
      } catch (e) {
        console.error('submit HIL reject failed', e);
        setBusy(false);
      }
    };

    approveBtn.onclick = async () => {
      setBusy(true);
      try {
        await submitDecision(payload.request_id, true, reasonInput.value || 'approved_by_user');
        close(payload.request_id);
      } catch (e) {
        console.error('submit HIL approve failed', e);
        setBusy(false);
      }
    };

    actionRow.appendChild(rejectBtn);
    actionRow.appendChild(approveBtn);

    shell.appendChild(badge);
    shell.appendChild(title);
    shell.appendChild(summary);
    shell.appendChild(requestMeta);
    shell.appendChild(reasonInput);
    shell.appendChild(actionRow);
    overlay.appendChild(shell);
    host.appendChild(overlay);
    updatePosition();
  }

  function enqueue(payload) {
    if (!payload || !payload.request_id) return;
    if (activeHilApproval && activeHilApproval.request_id === payload.request_id) return;
    if (hilApprovalQueue.some((item) => item && item.request_id === payload.request_id)) return;
    hilApprovalQueue.push(payload);
    renderNext();
  }

  return { enqueue, close, isPointOver, updatePosition };
}
