function createArayaTriggerSlider(onTrigger) {
  const wrap = el("div", "araya-trigger-wrap");
  const canvas = document.createElement("canvas");
  canvas.className = "araya-trigger-canvas";
  const hint = el("div", "araya-trigger-hint", "向右拖动触发 Araya");
  wrap.append(canvas, hint);

  const dpr = window.devicePixelRatio || 1;
  const W = 320;
  const H = 46;
  const pad = 10;
  const knobR = 14;
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

  function setupCanvas() {
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
    raf = window.requestAnimationFrame(tick);
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
      hint.textContent = armed ? "松手触发 Araya" : "向右拖动触发 Araya";
    }
  }

  async function releaseHandle() {
    dragging = false;
    if (armed && !triggered) {
      triggered = true;
      target = range();
      hint.textContent = "触发中...";
      draw();
      try {
        await onTrigger();
        hint.textContent = "触发成功";
      } catch (_e) {
        hint.textContent = "触发失败，请重试";
      }
      window.setTimeout(() => {
        triggered = false;
        armed = false;
        target = 0;
        hint.textContent = "向右拖动触发 Araya";
      }, 800);
      return;
    }
    armed = false;
    target = 0;
    hint.textContent = "向右拖动触发 Araya";
  }

  canvas.addEventListener("pointerdown", (evt) => {
    if (triggered) return;
    canvas.setPointerCapture(evt.pointerId);
    dragging = true;
    target = x;
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

  setupCanvas();
  draw();
  raf = window.requestAnimationFrame(tick);

  wrap.addEventListener("DOMNodeRemoved", () => {
    if (raf) window.cancelAnimationFrame(raf);
  });

  return wrap;
}
