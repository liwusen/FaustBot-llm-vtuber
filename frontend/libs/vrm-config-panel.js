// VRM 配置面板 — 滑块 UI 调节模型姿态/手势参数
// 用法: const vrmCfg = initVRMConfigPanel({ getVrmScene });

export function initVRMConfigPanel({ getVrmScene }) {
  let configBuilt = false;

  const panel = document.getElementById('vrmConfigPanel');
  const panelBody = document.getElementById('vrmConfigPanelBody');
  const openBtn = document.getElementById('openVRMConfigBtn');
  const closeBtn = document.getElementById('vrmConfigCloseBtn');
  const saveBtn = document.getElementById('vrmConfigSaveBtn');
  const resetBtn = document.getElementById('vrmConfigResetBtn');

  function getScene() {
    return typeof getVrmScene === 'function' ? getVrmScene() : null;
  }

  function build() {
    const vrmScene = getScene();
    if (!vrmScene || !panelBody) return;
    const cfg = vrmScene.getConfig();
    panelBody.innerHTML = '';
    const sections = [
      { key: 'armsRight', title: '\u53F3\u81C2', rows: [
        { label: '\u4E0A\u81C2\u524D\u503E', path: 'arms.rightUpperArm.x', min: -0.5, max: 0.5, step: 0.01, val: cfg.arms.rightUpperArm.x },
        { label: '\u4E0A\u81C2\u4E0B\u5782', path: 'arms.rightUpperArm.z', min: -1.8, max: 0, step: 0.01, val: cfg.arms.rightUpperArm.z },
        { label: '\u5C0F\u81C2\u5F2F\u66F2', path: 'arms.rightLowerArm.x', min: 0, max: 1.8, step: 0.01, val: cfg.arms.rightLowerArm.x },
      ]},
      { key: 'armsLeft', title: '\u5DE6\u81C2', rows: [
        { label: '\u4E0A\u81C2\u524D\u503E', path: 'arms.leftUpperArm.x', min: -0.5, max: 0.5, step: 0.01, val: cfg.arms.leftUpperArm.x },
        { label: '\u4E0A\u81C2\u4E0B\u5782', path: 'arms.leftUpperArm.z', min: 0, max: 1.8, step: 0.01, val: cfg.arms.leftUpperArm.z },
        { label: '\u5C0F\u81C2\u5F2F\u66F2', path: 'arms.leftLowerArm.x', min: 0, max: 1.8, step: 0.01, val: cfg.arms.leftLowerArm.x },
      ]},
      { key: 'swing', title: '\u624B\u81C2\u6446\u52A8', rows: [
        { label: '\u6446\u52A8\u5E45\u5EA6', path: 'arms.swingAmplitude', min: 0, max: 0.2, step: 0.005, val: cfg.arms.swingAmplitude },
        { label: '\u6446\u52A8\u901F\u5EA6', path: 'arms.swingSpeed', min: 0.1, max: 2, step: 0.1, val: cfg.arms.swingSpeed },
      ]},
      { key: 'rightHand', title: '\u53F3\u624B\u624B\u6307\uFF08\u5F2F\u66F2\uFF09', rows: [
        { label: '\u62C7\u6307\u5F2F\u66F2', path: 'hands.right.thumbCurl', min: 0, max: 1, step: 0.02, val: (cfg.hands && cfg.hands.right ? cfg.hands.right.thumbCurl : 0) },
        { label: '\u98DF\u6307\u5F2F\u66F2', path: 'hands.right.indexCurl', min: 0, max: 1, step: 0.02, val: (cfg.hands && cfg.hands.right ? cfg.hands.right.indexCurl : 0) },
        { label: '\u4E2D\u6307\u5F2F\u66F2', path: 'hands.right.middleCurl', min: 0, max: 1, step: 0.02, val: (cfg.hands && cfg.hands.right ? cfg.hands.right.middleCurl : 0) },
        { label: '\u65E0\u540D\u6307\u5F2F\u66F2', path: 'hands.right.ringCurl', min: 0, max: 1, step: 0.02, val: (cfg.hands && cfg.hands.right ? cfg.hands.right.ringCurl : 0) },
        { label: '\u5C0F\u6307\u5F2F\u66F2', path: 'hands.right.littleCurl', min: 0, max: 1, step: 0.02, val: (cfg.hands && cfg.hands.right ? cfg.hands.right.littleCurl : 0) },
      ]},
      { key: 'leftHand', title: '\u5DE6\u624B\u624B\u6307\uFF08\u5F2F\u66F2\uFF09', rows: [
        { label: '\u62C7\u6307\u5F2F\u66F2', path: 'hands.left.thumbCurl', min: 0, max: 1, step: 0.02, val: (cfg.hands && cfg.hands.left ? cfg.hands.left.thumbCurl : 0) },
        { label: '\u98DF\u6307\u5F2F\u66F2', path: 'hands.left.indexCurl', min: 0, max: 1, step: 0.02, val: (cfg.hands && cfg.hands.left ? cfg.hands.left.indexCurl : 0) },
        { label: '\u4E2D\u6307\u5F2F\u66F2', path: 'hands.left.middleCurl', min: 0, max: 1, step: 0.02, val: (cfg.hands && cfg.hands.left ? cfg.hands.left.middleCurl : 0) },
        { label: '\u65E0\u540D\u6307\u5F2F\u66F2', path: 'hands.left.ringCurl', min: 0, max: 1, step: 0.02, val: (cfg.hands && cfg.hands.left ? cfg.hands.left.ringCurl : 0) },
        { label: '\u5C0F\u6307\u5F2F\u66F2', path: 'hands.left.littleCurl', min: 0, max: 1, step: 0.02, val: (cfg.hands && cfg.hands.left ? cfg.hands.left.littleCurl : 0) },
      ]},
      { key: 'body', title: '\u8EAB\u4F53', rows: [
        { label: '\u524D\u540E\u6447\u6446', path: 'body.spineSwayX', min: 0, max: 0.02, step: 0.001, val: cfg.body.spineSwayX },
        { label: '\u5DE6\u53F3\u6447\u6446', path: 'body.spineSwayZ', min: 0, max: 0.02, step: 0.001, val: cfg.body.spineSwayZ },
        { label: '\u6447\u6446\u901F\u5EA6', path: 'body.swaySpeed', min: 0.1, max: 2, step: 0.1, val: cfg.body.swaySpeed },
      ]},
      { key: 'head', title: '\u5934\u90E8', rows: [
        { label: '\u5DE6\u53F3\u5FAE\u8F6C', path: 'head.neckZ', min: 0, max: 0.03, step: 0.001, val: cfg.head.neckZ },
        { label: '\u4E0A\u4E0B\u5FAE\u8F6C', path: 'head.neckY', min: 0, max: 0.03, step: 0.001, val: cfg.head.neckY },
        { label: '\u8FD0\u52A8\u901F\u5EA6', path: 'head.speed', min: 0.1, max: 1.5, step: 0.1, val: cfg.head.speed },
      ]},
      { key: 'blink', title: '\u7728\u773C', rows: [
        { label: '\u6700\u5C0F\u65F6\u9694', path: 'blink.minInterval', min: 0.5, max: 10, step: 0.5, val: cfg.blink.minInterval },
        { label: '\u6700\u5927\u65F6\u9694', path: 'blink.maxInterval', min: 1, max: 15, step: 0.5, val: cfg.blink.maxInterval },
        { label: '\u95ED\u773C\u65F6\u957F', path: 'blink.closeDuration', min: 0.02, max: 0.3, step: 0.01, val: cfg.blink.closeDuration },
        { label: '\u7741\u773C\u65F6\u957F', path: 'blink.openDuration', min: 0.02, max: 0.3, step: 0.01, val: cfg.blink.openDuration },
      ]},
      { key: 'eye', title: '\u89C6\u7EBF', rows: [
        { label: '\u6C34\u5E73\u8303\u56F4', path: 'eye.saccadeRangeX', min: 0, max: 1, step: 0.05, val: cfg.eye.saccadeRangeX },
        { label: '\u5782\u76F4\u8303\u56F4', path: 'eye.saccadeRangeY', min: 0, max: 0.5, step: 0.05, val: cfg.eye.saccadeRangeY },
        { label: '\u626B\u89C6\u65F6\u957F', path: 'eye.duration', min: 0.2, max: 3, step: 0.1, val: cfg.eye.duration },
        { label: '\u9F20\u6807\u7075\u654F\u5EA6', path: 'eye.mouseFovScale', min: 0, max: 1, step: 0.05, val: cfg.eye.mouseFovScale },
        { label: '\u9F20\u6807\u8D85\u65F6', path: 'eye.mouseIdleTimeout', min: 1, max: 30, step: 1, val: cfg.eye.mouseIdleTimeout },
      ]},
      { key: 'microExp', title: '\u5FAE\u8868\u60C5', rows: [
        { label: '\u6700\u5C0F\u65F6\u9694', path: 'microExp.minInterval', min: 2, max: 30, step: 1, val: cfg.microExp.minInterval },
        { label: '\u6700\u5927\u65F6\u9694', path: 'microExp.maxInterval', min: 5, max: 60, step: 1, val: cfg.microExp.maxInterval },
        { label: '\u8868\u60C5\u6743\u91CD', path: 'microExp.weight', min: 0, max: 0.5, step: 0.01, val: cfg.microExp.weight },
        { label: '\u6DE1\u5165\u65F6\u957F', path: 'microExp.fadeIn', min: 0.1, max: 2, step: 0.1, val: cfg.microExp.fadeIn },
        { label: '\u4FDD\u6301\u65F6\u957F', path: 'microExp.hold', min: 0.3, max: 5, step: 0.1, val: cfg.microExp.hold },
        { label: '\u6DE1\u51FA\u65F6\u957F', path: 'microExp.fadeOut', min: 0.1, max: 2, step: 0.1, val: cfg.microExp.fadeOut },
      ]},
    ];

    for (const sec of sections) {
      const secDiv = document.createElement('div');
      secDiv.className = 'vrm-config-section';
      const header = document.createElement('div');
      header.className = 'vrm-config-section-header';
      header.textContent = sec.title;
      header.dataset.expanded = 'true';
      header.addEventListener('click', () => {
        const body = secDiv.querySelector('.vrm-config-section-body');
        if (body) {
          body.style.display = body.style.display === 'none' ? '' : 'none';
          header.dataset.expanded = header.dataset.expanded === 'true' ? 'false' : 'true';
        }
      });
      secDiv.appendChild(header);

      const bodyDiv = document.createElement('div');
      bodyDiv.className = 'vrm-config-section-body';

      for (const row of sec.rows) {
        const rowDiv = document.createElement('div');
        rowDiv.className = 'vrm-config-row';
        const label = document.createElement('label');
        label.textContent = row.label;
        const input = document.createElement('input');
        input.type = 'range';
        input.min = row.min;
        input.max = row.max;
        input.step = row.step;
        input.value = row.val;
        const valSpan = document.createElement('span');
        valSpan.className = 'vrm-config-val';
        valSpan.textContent = Number(row.val).toFixed(row.step < 0.01 ? 3 : row.step < 0.05 ? 2 : 1);

        input.addEventListener('input', () => {
          const v = parseFloat(input.value);
          valSpan.textContent = v.toFixed(row.step < 0.01 ? 3 : row.step < 0.05 ? 2 : 1);
          const scene = getScene();
          if (scene) scene.applyConfigValue(row.path, v);
        });

        rowDiv.appendChild(label);
        rowDiv.appendChild(input);
        rowDiv.appendChild(valSpan);
        bodyDiv.appendChild(rowDiv);
      }

      secDiv.appendChild(bodyDiv);
      panelBody.appendChild(secDiv);
    }
    configBuilt = true;
  }

  function open() {
    const vrmScene = getScene();
    if (!vrmScene || !panel) return;
    panel.style.display = 'flex';
    build();
    if (vrmScene.enterConfigMode) vrmScene.enterConfigMode();
  }

  function init() {
    if (openBtn) {
      openBtn.addEventListener('click', () => {
        const vrmScene = getScene();
        if (!vrmScene || !panel) return;
        const isOpen = panel.style.display !== 'none';
        if (isOpen) {
          panel.style.display = 'none';
          if (vrmScene.exitConfigMode) vrmScene.exitConfigMode();
          if (vrmScene.setModelTransform) {
            try {
              const t = vrmScene.getModelTransform();
              fetch('http://127.0.0.1:13900/faust/admin/vrm-config/model-state', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(t),
              }).catch(() => {});
            } catch (e) {}
          }
        } else {
          open();
        }
      });
    }
    if (closeBtn) {
      closeBtn.addEventListener('click', () => {
        if (panel) panel.style.display = 'none';
        const vrmScene = getScene();
        if (vrmScene && vrmScene.exitConfigMode) vrmScene.exitConfigMode();
      });
    }
    if (saveBtn) {
      saveBtn.addEventListener('click', async () => {
        const vrmScene = getScene();
        if (!vrmScene) return;
        const cfg = vrmScene.getConfig();
        const t = vrmScene.getModelTransform();
        cfg.modelState = t;
        try {
          const resp = await fetch('http://127.0.0.1:13900/faust/admin/vrm-config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config: cfg }),
          });
          if (resp.ok) {
            if (panel) panel.style.display = 'none';
            if (vrmScene.exitConfigMode) vrmScene.exitConfigMode();
          }
        } catch (e) {
          console.warn('Failed to save VRM config:', e);
        }
      });
    }
    if (resetBtn) {
      resetBtn.addEventListener('click', async () => {
        try {
          const resp = await fetch('http://127.0.0.1:13900/faust/admin/vrm-config/reset');
          const data = await resp.json();
          const vrmScene = getScene();
          if (data && data.config && vrmScene) {
            vrmScene.setConfig(data.config);
            configBuilt = false;
            build();
          }
        } catch (e) {
          console.warn('Failed to reset VRM config:', e);
        }
      });
    }
  }

  return { init, open, build };
}
