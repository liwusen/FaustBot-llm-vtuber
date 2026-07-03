// 文本处理工具函数 — 纯函数，无外部依赖

export function normalizeTtsText(text) {
  return String(text ?? '')
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .replace(/\\n/g, '\n');
}

export function decodeWsPayload(data) {
  if (typeof data === 'string') return data;
  try {
    if (data instanceof ArrayBuffer) return new TextDecoder('utf-8').decode(data);
    if (ArrayBuffer.isView(data)) return new TextDecoder('utf-8').decode(data);
    if (data && typeof Blob !== 'undefined' && data instanceof Blob) {
      return data.text();
    }
  } catch (e) {
    console.warn('decodeWsPayload failed, fallback to String(data)', e);
  }
  return String(data ?? '');
}

export function extractCompletedSentences(buffer) {
  buffer = normalizeTtsText(buffer);
  const results = [];
  let start = 0;
  for (let i = 0; i < buffer.length; i++) {
    const ch = buffer[i];
    if ('。！？!?；;\n'.includes(ch)) {
      const sentence = buffer.slice(start, i + 1).trim();
      if (sentence) results.push(sentence);
      start = i + 1;
    }
  }
  return { completed: results, rest: buffer.slice(start) };
}
