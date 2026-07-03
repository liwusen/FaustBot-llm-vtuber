// 直播模式模块 — 定时轮询直播状态，切换文字输入栏显隐
// 用法: const live = initLiveMode(); live.start();

export function initLiveMode() {
  const textChatBar = document.getElementById('textChatBar');
  let lastLiveModeState = false;
  let timer = null;

  async function poll() {
    try {
      const resp = await fetch('http://127.0.0.1:13900/faust/live/status');
      const data = await resp.json();
      const isLive = Boolean(data.live_mode);
      if (isLive !== lastLiveModeState) {
        lastLiveModeState = isLive;
        if (textChatBar) {
          textChatBar.style.display = isLive ? 'none' : '';
        }
      }
    } catch (e) {
      // ignore network errors
    }
  }

  function start() {
    stop();
    poll();
    timer = setInterval(poll, 3000);
  }

  function stop() {
    if (timer) { clearInterval(timer); timer = null; }
  }

  return { start, stop, poll };
}
