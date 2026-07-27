// 「布景台」— 编辑模式下的左侧 UI 设置面板
export function initLayoutSidePanel({ manager, saveSettings } = {}) {
  const groups = new Map();
  const renderers = new Map();
  const groupEls = new Map();
  let seqCounter = 0;
  let visible = false;

  const panelEl = document.createElement('div');
  panelEl.className = 'layout-side-panel';
  panelEl.innerHTML = '<div class="lsp-title"><span class="lsp-title-text">布景台</span><span class="lsp-title-hint">编辑模式 · 组件布景</span></div><div class="lsp-groups"></div>';
  document.body.appendChild(panelEl);
  const groupsEl = panelEl.querySelector('.lsp-groups');

  function sortedGroups() {
    return Array.from(groups.values()).sort((a, b) => (a.order - b.order) || (a.seq - b.seq));
  }

  function renderGroupBody(group, bodyEl) {
    bodyEl.innerHTML = '';
    const renderer = renderers.get(group.id);
    if (typeof renderer !== 'function') {
      const empty = document.createElement('div');
      empty.className = 'lsp-empty';
      empty.textContent = '该组暂无内容';
      bodyEl.appendChild(empty);
      return;
    }
    try {
      renderer(bodyEl, { groupId: group.id, refresh: () => renderGroup(group.id), manager, saveSettings });
    } catch (e) {
      console.warn('[layout-side-panel] render group failed:', group.id, e);
      const err = document.createElement('div');
      err.className = 'lsp-empty';
      err.textContent = '渲染出错';
      bodyEl.appendChild(err);
    }
  }

  function buildGroupEl(group) {
    const root = document.createElement('div');
    root.className = 'lsp-group' + (group.open ? ' open' : '');
    const header = document.createElement('div');
    header.className = 'lsp-group-header';
    header.innerHTML = `<span class="lsp-group-label"></span><span class="lsp-group-chevron"></span>`;
    header.querySelector('.lsp-group-label').textContent = group.label;
    const bodyWrap = document.createElement('div');
    bodyWrap.className = 'lsp-group-body-wrap';
    const body = document.createElement('div');
    body.className = 'lsp-group-body';
    bodyWrap.appendChild(body);
    header.addEventListener('click', () => {
      group.open = !group.open;
      root.classList.toggle('open', group.open);
    });
    root.append(header, bodyWrap);
    groupEls.set(group.id, { rootEl: root, bodyEl: body });
    return root;
  }

  function renderAll() {
    groupsEl.innerHTML = '';
    groupEls.clear();
    for (const group of sortedGroups()) {
      const root = buildGroupEl(group);
      groupsEl.appendChild(root);
      renderGroupBody(group, groupEls.get(group.id).bodyEl);
    }
  }

  function renderGroup(id) {
    const group = groups.get(String(id));
    const entry = groupEls.get(String(id));
    if (!group || !entry) return;
    renderGroupBody(group, entry.bodyEl);
  }

  function registerGroup(spec) {
    if (!spec || typeof spec.id !== 'string' || !spec.id) throw new Error('group id is required');
    const id = spec.id;
    const prev = groups.get(id);
    const next = {
      id,
      label: typeof spec.label === 'string' && spec.label ? spec.label : id,
      plugin: spec.plugin || (prev && prev.plugin) || '',
      order: Number.isFinite(Number(spec.order)) ? Number(spec.order) : (prev ? prev.order : 100),
      seq: prev ? prev.seq : seqCounter++,
      open: prev ? prev.open : !spec.collapsed,
    };
    groups.set(id, next);
    if (visible) renderAll();
    return { ...next };
  }

  function setGroupRender(id, fn) {
    const key = String(id);
    if (typeof fn !== 'function') {
      renderers.delete(key);
    } else {
      renderers.set(key, fn);
    }
    if (visible && groups.has(key)) renderGroup(key);
  }

  function setVisible(next) {
    const enabled = !!next;
    if (enabled === visible) return;
    visible = enabled;
    document.body.classList.toggle('layout-side-panel-open', enabled);
    if (enabled) renderAll();
  }

  function isVisible() {
    return visible;
  }

  return { registerGroup, setGroupRender, setVisible, isVisible, renderGroup };
}
