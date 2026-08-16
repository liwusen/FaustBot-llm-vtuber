// 鼠标跟踪生效验证：鼠标移到不同位置 → 截图对比头部方向
const WebSocket = require('ws');
const crypto = require('crypto');

async function main() {
  const targets = await (await fetch('http://127.0.0.1:9227/json')).json();
  const page = targets.find((t) => t.type === 'page' && t.url.includes('index.html'));
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  const exceptions = [];
  ws.on('message', (data) => {
    const msg = JSON.parse(data.toString());
    if (msg.id && pending.has(msg.id)) { const { resolve } = pending.get(msg.id); pending.delete(msg.id); resolve(msg); }
    else if (msg.method === 'Runtime.exceptionThrown') {
      const d = msg.params.exceptionDetails;
      exceptions.push((d.exception && d.exception.description || d.text || '').slice(0, 300));
    }
  });
  const send = (method, params = {}) => new Promise((resolve) => { const mid = ++id; pending.set(mid, { resolve }); ws.send(JSON.stringify({ id: mid, method, params })); });
  await new Promise((r) => ws.on('open', r));
  await send('Runtime.enable');
  const evalExpr = async (expr) => {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.result && r.result.exceptionDetails) return 'THREW: ' + (r.result.exceptionDetails.exception && r.result.exceptionDetails.exception.description || r.result.exceptionDetails.text);
    const v = r.result && r.result.result;
    return v && v.value !== undefined ? v.value : (v && v.description || 'undefined');
  };
  const shot = async (label) => {
    const r = await send('Page.captureScreenshot', { format: 'png' });
    const buf = Buffer.from(r.result.data, 'base64');
    const hash = crypto.createHash('sha256').update(buf).digest('hex').slice(0, 12);
    console.log(`${label}: ${buf.length}B ${hash}`);
    return buf;
  };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  await sleep(2000);

  // 加载模型
  await evalExpr(`(function(){ document.getElementById('modelPath').value = '2D/hiyori_pro/hiyori_pro_t11.model3.json'; document.getElementById('loadBtn').click(); return true; })()`);
  await sleep(4000);
  const state = await evalExpr('window.__soullinkDebug ? JSON.stringify(window.__soullinkDebug.getState()) : "no debug"');
  console.log('soullink:', state);

  const move = async (fx, fy) => {
    await evalExpr(`(function(){
      const canvas = document.querySelector('canvas');
      const rect = canvas.getBoundingClientRect();
      for (let i = 0; i < 6; i++) {
        canvas.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, clientX: rect.left + rect.width * (${fx} + i * 0.01), clientY: rect.top + rect.height * (${fy} + i * 0.01) }));
      }
      return true;
    })()`);
    await sleep(1500);
  };

  // 鼠标移到左上角 → 截图；移到右下角 → 截图
  await move(0.15, 0.15);
  const a = await shot('mouse-topleft');
  await move(0.85, 0.85);
  const b = await shot('mouse-bottomright');
  // 移回中间附近
  await move(0.5, 0.5);
  const c = await shot('mouse-center');

  console.log('左上 vs 右下 相同:', a.equals(b), '（false=头部确实跟随鼠标转动）');
  console.log('右下 vs 中心 相同:', b.equals(c));
  console.log('=== 异常 ===');
  console.log(exceptions.length ? exceptions.join('\n') : '(无)');
  ws.close();
}
main().catch((e) => { console.error('err', e); process.exit(1); });
