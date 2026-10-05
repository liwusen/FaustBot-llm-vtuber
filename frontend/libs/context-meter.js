/**
 * 上下文占用药丸（输入栏工具条右端）。
 *
 * 分工约定：token 数字与「K / M」格式化规则**只在后端实现一份**
 * （`faust_backend/runtime/session_stats.py` 的 `collect_session_stats()` /
 * `format_tokens()`），前端只消费 `used_tokens_text` / `context_length_text`
 * 与原始整数（后者仅用于 hover 提示里的完整数字），不再复制一套规则，
 * 避免两端阈值不同导致显示漂移。
 *
 * 刷新策略：不轮询。由 app.js 在主 Agent 的终结事件
 * （done / interrupted / error / compact_done）后调用 `refresh()`。
 * 失败时保留上一次成功值；从未成功过则保持隐藏——不显示猜测值。
 *
 * @module context-meter
 */

const DEFAULT_TEXT = '— / 128K';

function exact(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  return number.toLocaleString('en-US');
}

function buildTitle(stats) {
  const limit = exact(stats.context_length);
  if (!stats.has_api_usage) {
    return `暂无 API 计数（本会话尚无 LLM 响应上报 usage）\n窗口上限 ${limit} tokens`;
  }
  const used = exact(stats.used_tokens);
  const percent = Number.isFinite(Number(stats.percent)) ? Number(stats.percent).toFixed(1) : '0.0';
  const threshold = exact(stats.threshold_tokens);
  return `已用 ${used} / ${limit} tokens（${percent}%）\n自动压缩阈值 ${threshold} tokens`;
}

/**
 * @param {object} opts
 * @param {string} opts.endpoint      GET /faust/session/context 的完整 URL
 * @param {HTMLElement|null} opts.element     药丸容器（切换 hidden）
 * @param {HTMLElement|null} opts.textElement 药丸文本节点
 * @param {(url: string, init?: object) => Promise<any>} [opts.fetchImpl]
 * @returns {{ refresh: () => Promise<object|null>, getLast: () => object|null }}
 */
export function initContextMeter({ endpoint, element, textElement, fetchImpl } = {}) {
  const doFetch = fetchImpl || (typeof fetch === 'function' ? fetch.bind(window) : null);
  let last = null;      // 最近一次成功的数据
  let inflight = null;  // 同一时刻只允许一个请求在飞
  let warned = false;

  function render(stats) {
    if (textElement) {
      const used = stats.has_api_usage ? String(stats.used_tokens_text ?? '—') : '—';
      const limit = String(stats.context_length_text || '—');
      textElement.textContent = `${used} / ${limit}`;
    }
    if (element) {
      element.hidden = false;
      element.title = buildTitle(stats);
    }
  }

  async function refresh() {
    if (!endpoint || !doFetch) return null;
    if (inflight) return inflight;
    inflight = (async () => {
      try {
        const resp = await doFetch(endpoint, { method: 'GET', cache: 'no-store' });
        if (!resp || !resp.ok) throw new Error(`HTTP ${resp && resp.status}`);
        const stats = await resp.json();
        if (!stats || stats.status !== 'ok') throw new Error('unexpected payload');
        last = stats;
        warned = false;
        render(stats);
        return stats;
      } catch (error) {
        // 失败不隐瞒也不编造：保留上一次成功值，从未成功过则保持隐藏。
        if (!warned) console.warn('[context-meter] 上下文占用拉取失败:', error);
        warned = true;
        return null;
      } finally {
        inflight = null;
      }
    })();
    return inflight;
  }

  if (element && textElement) textElement.textContent = DEFAULT_TEXT;

  return { refresh, getLast: () => last };
}
