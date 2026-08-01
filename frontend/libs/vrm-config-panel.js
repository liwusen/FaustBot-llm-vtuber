// VRM 配置面板 — 滑块 UI 调节模型姿态/手势参数 + 交互式动作编辑模式
// 用法: const vrmCfg = initVRMConfigPanel({ getVrmScene });

export function initVRMConfigPanel({ getVrmScene }) {
  let configBuilt = false;
  let dirty = false;
  let editCleanup = null;

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

    // ── 动作编辑模式 ──
    if (typeof vrmScene.getIkChains === 'function') {
      buildEditSection(vrmScene);
    }
    configBuilt = true;
  }

  function buildEditSection(vrmScene) {
    // mode buttons
    const editDiv = document.createElement('div');
    editDiv.className = 'vrm-config-section';
    const editHeader = document.createElement('div');
    editHeader.className = 'vrm-config-section-header';
    editHeader.textContent = '动作编辑（拖拽身体摆姿势）';
    editDiv.appendChild(editHeader);
    const editBody = document.createElement('div');
    editBody.className = 'vrm-config-section-body';

    const modeRow = document.createElement('div');
    modeRow.className = 'vrm-config-row';
    const modeLabel = document.createElement('label');
    modeLabel.textContent = '拖拽模式';
    const modeSelect = document.createElement('select');
    modeSelect.innerHTML =
      '<option value="drag">拖拽（摆姿势）</option>' +
      '<option value="orbit">环绕（转视角）</option>' +
      '<option value="move">移动（挪模型）</option>';
    modeRow.appendChild(modeLabel);
    modeRow.appendChild(modeSelect);
    editBody.appendChild(modeRow);
    modeSelect.addEventListener('change', () => {
      vrmScene.editDragMode = modeSelect.value;
    });
    vrmScene.editDragMode = modeSelect.value;

    // chain selector
    const chainRow = document.createElement('div');
    chainRow.className = 'vrm-config-row';
    const chainLabel = document.createElement('label');
    chainLabel.textContent = '拖动部位';
    const chainSelect = document.createElement('select');
    chainSelect.innerHTML = '<option value="">（选择拖动部位）</option>';
    const chains = vrmScene.getIkChains();
    for (const c of chains) {
      const opt = document.createElement('option');
      opt.value = c.name;
      opt.textContent = c.label;
      chainSelect.appendChild(opt);
    }
    chainRow.appendChild(chainLabel);
    chainRow.appendChild(chainSelect);
    editBody.appendChild(chainRow);

    // expression sliders
    const expDiv = document.createElement('div');
    expDiv.className = 'vrm-config-section';
    const expHeader = document.createElement('div');
    expHeader.className = 'vrm-config-section-header';
    expHeader.textContent = '表情权重';
    expDiv.appendChild(expHeader);
    const expBody = document.createElement('div');
    expBody.className = 'vrm-config-section-body';
    expDiv.appendChild(expBody);

    let expNames = [];
    if (typeof vrmScene.getExpressionNames === 'function') {
      expNames = vrmScene.getExpressionNames();
    }
    const weightSliders = [];
    for (const ename of expNames) {
      const row = document.createElement('div');
      row.className = 'vrm-config-row';
      row.appendChild(Object.assign(document.createElement('label'), { textContent: ename }));
      const slider = document.createElement('input');
      slider.type = 'range'; slider.min = 0; slider.max = 1; slider.step = 0.02; slider.value = 0;
      const val = document.createElement('span');
      val.className = 'vrm-config-val'; val.textContent = '0.00';
      slider.addEventListener('input', () => {
        const v = parseFloat(slider.value);
        val.textContent = v.toFixed(2);
        const sc = getScene();
        if (sc && sc.setExpressionWeight) {
          sc.setExpressionWeight(ename, v);
          dirty = true;
        }
      });
      row.appendChild(slider);
      row.appendChild(val);
      expBody.appendChild(row);
      weightSliders.push({ name: ename, slider });
    }

    // preset save row
    const saveRow = document.createElement('div');
    saveRow.className = 'vrm-config-row';
    const presetInput = document.createElement('input');
    presetInput.type = 'text';
    presetInput.placeholder = '预设名（不含空格）';
    const snapshotBtn = Object.assign(document.createElement('button'), { textContent: '保存当前姿态' });
    saveRow.appendChild(presetInput);
    saveRow.appendChild(snapshotBtn);
    editBody.appendChild(saveRow);

    // preset list
    const presetList = document.createElement('div');
    presetList.className = 'vrm-config-preset-list';
    editBody.appendChild(presetList);

    editDiv.appendChild(editBody);
    panelBody.appendChild(editDiv);
    panelBody.appendChild(expDiv);

    // drag wiring — IK drag handled here in 'drag' mode; app.js routes orbit/move
    const vrmCanvas = vrmScene.getCanvas();
    let poseDragging = false;
    let dragChain = chainSelect.value;
    chainSelect.addEventListener('change', () => { dragChain = chainSelect.value; });

    const onPointerDown = (e) => {
      if (modeSelect.value !== 'drag') return;
      if (!dragChain) return;
      if (vrmScene.beginPoseDrag(e.clientX, e.clientY, dragChain)) {
        poseDragging = true;
        dirty = true;
      }
    };
    const onPointerMove = (e) => {
      if (!poseDragging) return;
      vrmScene.updatePoseDrag(e.clientX, e.clientY);
    };
    const onPointerUp = () => {
      if (poseDragging) {
        poseDragging = false;
        vrmScene.endPoseDrag();
      }
    };
    if (vrmCanvas) {
      vrmCanvas.addEventListener('pointerdown', onPointerDown);
      vrmCanvas.addEventListener('pointermove', onPointerMove);
      vrmCanvas.addEventListener('pointerup', onPointerUp);
      vrmCanvas.addEventListener('pointerupoutside', onPointerUp);
    }

    const refreshPresetList = async () => {
      try {
        const resp = await fetch('http://127.0.0.1:13900/faust/admin/vrm-poses');
        const data = await resp.json();
        const poses = (data && data.poses) || {};
        presetList.innerHTML = '';
        for (const pname of Object.keys(poses)) {
          const row = document.createElement('div');
          row.className = 'vrm-config-row';
          row.appendChild(Object.assign(document.createElement('span'), { textContent: pname }));
          const applyBtn = Object.assign(document.createElement('button'), { textContent: '应用' });
          applyBtn.addEventListener('click', async () => {
            const sc = getScene();
            if (!sc || !sc.applyPoseSnapshot) return;
            const entry = poses[pname] || {};
            const pose = entry.pose || {};
            const trans = Number(pose.transition) >= 0 ? Number(pose.transition) : 600;
            await sc.applyPoseSnapshot(pose, trans);
          });
          const delBtn = Object.assign(document.createElement('button'), { textContent: '删除' });
          delBtn.addEventListener('click', async () => {
            if (!confirm(`删除预设 ${pname}？`)) return;
            await fetch(`http://127.0.0.1:13900/faust/admin/vrm-poses/${encodeURIComponent(pname)}`, { method: 'DELETE' });
            refreshPresetList();
          });
          row.appendChild(applyBtn);
          row.appendChild(delBtn);
          presetList.appendChild(row);
        }
      } catch (e) {
        console.warn('Failed to load VRM presets:', e);
      }
    };

    snapshotBtn.addEventListener('click', async () => {
      const name = presetInput.value.trim();
      if (!name || /\s/.test(name)) { alert('预设名不能为空且不能含空格'); return; }
      const sc = getScene();
      if (!sc || typeof sc.getPoseSnapshot !== 'function') return;
      const snap = sc.getPoseSnapshot();
      const pose = { ...snap, transition: 600 };
      try {
        const resp = await fetch('http://127.0.0.1:13900/faust/admin/vrm-poses', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, pose }),
        });
        if (resp.ok) {
          dirty = false;
          refreshPresetList();
        }
      } catch (e) {
        console.warn('Failed to save VRM preset:', e);
      }
    });

    refreshPresetList();

    editCleanup = () => {
      if (vrmCanvas) {
        vrmCanvas.removeEventListener('pointerdown', onPointerDown);
        vrmCanvas.removeEventListener('pointermove', onPointerMove);
        vrmCanvas.removeEventListener('pointerup', onPointerUp);
        vrmCanvas.removeEventListener('pointerupoutside', onPointerUp);
      }
      editCleanup = null;
    };
  }

  function open() {
    const vrmScene = getScene();
    if (!vrmScene || !panel) return;
    if (editCleanup) editCleanup();
    dirty = false;
    panel.style.display = 'flex';
    build();
    if (vrmScene.enterEditMode) vrmScene.enterEditMode();
    else if (vrmScene.enterConfigMode) vrmScene.enterConfigMode();
  }

  function closePanel() {
    const vrmScene = getScene();
    if (dirty) {
      const choice = confirm('姿态未保存，保存还是放弃？');
      if (choice) {
        const input = panelBody ? panelBody.querySelector('input[type="text"]') : null;
        if (input) input.focus();
        return;
      }
    }
    if (editCleanup) editCleanup();
    panel.style.display = 'none';
    if (vrmScene && vrmScene.restoreEditStartPose) vrmScene.restoreEditStartPose();
    if (vrmScene && vrmScene.exitEditMode) vrmScene.exitEditMode();
    else if (vrmScene && vrmScene.exitConfigMode) vrmScene.exitConfigMode();
    if (vrmScene && vrmScene.setModelTransform) {
      try {
        const t = vrmScene.getModelTransform();
        fetch('http://127.0.0.1:13900/faust/admin/vrm-config/model-state', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(t),
        }).catch(() => {});
      } catch (e) {}
    }
    dirty = false;
  }

  function init() {
    if (openBtn) {
      openBtn.addEventListener('click', () => {
        const vrmScene = getScene();
        if (!vrmScene || !panel) return;
        const isOpen = panel.style.display !== 'none';
        if (isOpen) {
          closePanel();
        } else {
          open();
        }
      });
    }
    if (closeBtn) {
      closeBtn.addEventListener('click', () => {
        closePanel();
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
            if (editCleanup) editCleanup();
            if (vrmScene.exitEditMode) vrmScene.exitEditMode();
            else if (vrmScene.exitConfigMode) vrmScene.exitConfigMode();
            dirty = false;
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
