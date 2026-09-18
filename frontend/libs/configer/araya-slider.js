function createArayaTriggerSlider(onTrigger, initialHint = "向右拖动触发 Araya") {
  const wrap = el("div", "araya-trigger-wrap");
  const canvas = document.createElement("canvas");
  canvas.className = "araya-trigger-canvas";
  const idleHint = initialHint;
  const hint = el("div", "araya-trigger-hint", idleHint);
  wrap.append(canvas, hint);

  let dpr = window.devicePixelRatio || 1;
  const MIN_W = 320;
  const H = 46;
  const pad = 10;
  const knobR = 14;
  let W = MIN_W;
  let x = 0;
  let v = 0;
  let target = 0;
  let dragging = false;
  let draggingOffset = 0;
  let armed = false;
  let triggered = false;
  let raf = 0;

  const min = () => pad + knobR;
  const max = () => W - pad - knobR;
  const range = () => max() - min();
  const threshold = () => range() * 0.82;

  // 画布铺满卡片宽度（创建时还没进 DOM → 先按最小宽度，ResizeObserver 再校正）
  function measuredWidth() {
    return Math.max(MIN_W, Math.floor(wrap.clientWidth || 0));
  }

  function setupCanvas() {
    dpr = window.devicePixelRatio || 1;
    W = measuredWidth();
    canvas.width = Math.floor(W * dpr);
    canvas.height = Math.floor(H * dpr);
    canvas.style.width = `${W}px`;
    canvas.style.height = `${H}px`;
  }

  function roundRect(ctx, rx, ry, rw, rh, rr) {
    const r = Math.min(rr, rw / 2, rh / 2);
    ctx.beginPath();
    ctx.moveTo(rx + r, ry);
    ctx.arcTo(rx + rw, ry, rx + rw, ry + rh, r);
    ctx.arcTo(rx + rw, ry + rh, rx, ry + rh, r);
    ctx.arcTo(rx, ry + rh, rx, ry, r);
    ctx.arcTo(rx, ry, rx + rw, ry, r);
    ctx.closePath();
  }

  function draw() {
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    const cy = H / 2;
    const trackY = cy - 12;
    const trackH = 24;
    const trackX = pad;
    const trackW = W - pad * 2;

    roundRect(ctx, trackX, trackY, trackW, trackH, 12);
    ctx.fillStyle = "#ecf2fb";
    ctx.fill();
    ctx.strokeStyle = "#cfd9e8";
    ctx.lineWidth = 1;
    ctx.stroke();

    const clampedX = Math.max(-28, Math.min(range(), x));
    const knobX = min() + clampedX;
    const progressW = Math.max(0, Math.min(trackW, knobX - trackX));

    if (progressW > 2) {
      const grad = ctx.createLinearGradient(trackX, 0, trackX + progressW, 0);
      grad.addColorStop(0, "#6f96ff");
      grad.addColorStop(1, "#3f6be8");
      roundRect(ctx, trackX, trackY, progressW, trackH, 12);
      ctx.fillStyle = grad;
      ctx.fill();
    }

    const tX = min() + threshold();
    ctx.beginPath();
    ctx.moveTo(tX, trackY + 4);
    ctx.lineTo(tX, trackY + trackH - 4);
    ctx.strokeStyle = "rgba(63,107,232,0.45)";
    ctx.lineWidth = 2;
    ctx.stroke();

    if (clampedX < 0) {
      ctx.beginPath();
      ctx.moveTo(min(), cy);
      ctx.quadraticCurveTo(min() + clampedX * 0.45, cy - 8, knobX, cy);
      ctx.strokeStyle = "rgba(229,68,71,0.45)";
      ctx.lineWidth = 3;
      ctx.stroke();
    }

    ctx.beginPath();
    ctx.arc(knobX, cy, knobR, 0, Math.PI * 2);
    ctx.fillStyle = triggered ? "#2c9158" : "#ffffff";
    ctx.fill();
    ctx.strokeStyle = triggered ? "#2c9158" : "#3f6be8";
    ctx.lineWidth = 2;
    ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(knobX - 4, cy - 5);
    ctx.lineTo(knobX + 3, cy);
    ctx.lineTo(knobX - 4, cy + 5);
    ctx.strokeStyle = triggered ? "#ffffff" : "#3f6be8";
    ctx.lineWidth = 2;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.stroke();
  }

  function tick() {
    raf = 0;
    // 页面 re-render 后旧画布会被丢弃：动画自动停，不能靠已废弃的 DOMNodeRemoved
    if (!canvas.isConnected) return;
    if (!dragging) {
      const force = (target - x) * 0.18;
      v = v * 0.78 + force;
      x += v;
      if (Math.abs(target - x) < 0.03 && Math.abs(v) < 0.03) {
        x = target;
        v = 0;
      }
    }
    draw();
    if (!dragging && (Math.abs(target - x) >= 0.03 || Math.abs(v) >= 0.03)) startLoop();
  }

  // 只在需要动画时跑 rAF（拖动中由 pointermove 直接重绘），静止时零开销
  function startLoop() {
    if (!raf && canvas.isConnected) raf = window.requestAnimationFrame(tick);
  }

  function stopLoop() {
    if (raf) window.cancelAnimationFrame(raf);
    raf = 0;
  }

  function setTarget(value) {
    target = value;
    startLoop();
  }

  function pointX(evt) {
    const rect = canvas.getBoundingClientRect();
    return evt.clientX - rect.left;
  }

  function updateByPointer(px) {
    const dx = px - min() - draggingOffset;
    if (dx < 0) {
      x = dx * 0.35;
    } else {
      x = Math.min(range() + 8, dx);
    }
    armed = x >= threshold();
    if (!triggered) {
      hint.textContent = armed ? "松手触发 Araya" : idleHint;
    }
  }

  async function releaseHandle() {
    dragging = false;
    if (armed && !triggered) {
      triggered = true;
      setTarget(range());
      hint.textContent = "触发中...";
      draw();
      try {
        // onTrigger 可以用字符串说明「为什么没触发」，直接当作提示文案
        const message = await onTrigger();
        hint.textContent = typeof message === "string" && message ? message : "触发成功";
      } catch (_e) {
        hint.textContent = "触发失败，请重试";
      }
      window.setTimeout(() => {
        triggered = false;
        armed = false;
        setTarget(0);
        hint.textContent = idleHint;
      }, 800);
      return;
    }
    armed = false;
    setTarget(0);
    hint.textContent = idleHint;
  }

  canvas.addEventListener("pointerdown", (evt) => {
    if (triggered) return;
    canvas.setPointerCapture(evt.pointerId);
    dragging = true;
    v = 0;
    target = x;
    stopLoop();
    const px = pointX(evt);
    draggingOffset = px - (min() + x);
  });

  canvas.addEventListener("pointermove", (evt) => {
    if (!dragging || triggered) return;
    updateByPointer(pointX(evt));
    draw();
  });

  const endDrag = async (evt) => {
    if (!dragging) return;
    if (evt && canvas.hasPointerCapture(evt.pointerId)) {
      canvas.releasePointerCapture(evt.pointerId);
    }
    await releaseHandle();
  };

  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);
  canvas.addEventListener("lostpointercapture", async () => {
    if (dragging) await releaseHandle();
  });

  const resizeObserver = window.ResizeObserver ? new ResizeObserver(() => {
    // 页面 re-render 后 wrap 已脱离文档：断开观察，别再为废弃画布重绘
    if (!canvas.isConnected) {
      resizeObserver.disconnect();
      return;
    }
    if (measuredWidth() === W) return;
    setupCanvas();
    x = Math.max(-28, Math.min(range(), x));
    draw();
  }) : null;
  if (resizeObserver) resizeObserver.observe(wrap);

  setupCanvas();
  draw();

  return wrap;
}
