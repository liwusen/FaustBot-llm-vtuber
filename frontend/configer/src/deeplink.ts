type DeeplinkHandler = (payload: unknown) => void;

const queue: unknown[] = [];
let handler: DeeplinkHandler | null = null;
let installed = false;

/** 在 App 挂载前安装，避免主进程早到的事件丢失。 */
export function installDeeplinkBridge(): void {
  if (installed) return;
  installed = true;
  if (!window.deeplink || typeof window.deeplink.onConfigFaustCloud !== 'function') {
    console.warn('[deeplink] onConfigFaustCloud 不可用');
    return;
  }
  window.deeplink.onConfigFaustCloud((payload) => {
    if (handler) handler(payload);
    else queue.push(payload);
  });
}

export function onConfigDeeplink(fn: DeeplinkHandler): () => void {
  handler = fn;
  while (queue.length) fn(queue.shift());
  return () => {
    if (handler === fn) handler = null;
  };
}
