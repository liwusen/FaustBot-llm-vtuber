// TTS 流式分块器 — 按 token 权重(字母0.5/CJK及中文标点1)与理想块长启发式分块。
// 经典脚本：挂 window.TtsSplitter，供 app.js(ES module) 与 config-window.html(经典脚本) 共用。
(function () {
  'use strict';

  // CJK 统一表意/扩展/兼容 + 中文标点(含全角形式) 按 1 token 计
  const WIDE_RE = /[\u2E80-\u9FFF\uF900-\uFAFF\u3000-\u303F\uFF00-\uFFEF]/;
  // 句末边界：强制可切块
  const SENT_END_RE = /[。！？!?；;\n]/;
  // 逗号级边界：长句内次级切块点
  const SOFT_END_RE = /[，,、]/;

  function tokenLength(text) {
    let t = 0;
    for (const ch of String(text || '')) t += WIDE_RE.test(ch) ? 1 : 0.5;
    return t;
  }

  // 句级扫描(含 trim 后的 [start,end) 偏移)，供调用方按偏移分配情绪动作
  function extractCompletedSentences(buffer) {
    buffer = String(buffer || '');
    const results = [];
    let start = 0;
    for (let i = 0; i < buffer.length; i++) {
      const ch = buffer[i];
      if (SENT_END_RE.test(ch)) {
        const raw = buffer.slice(start, i + 1);
        const leading = raw.length - raw.trimStart().length;
        const sentence = raw.trim();
        if (sentence) results.push({ text: sentence, start: start + leading, end: i + 1 });
        start = i + 1;
      }
    }
    return { completed: results, rest: buffer.slice(start) };
  }

  // 超长单句切分：优先逗号边界；无边界处累计到 2x ideal 硬切(spec 允许)。
  // 返回 [{text,start,end}]，偏移相对句首。
  function splitLongSentence(sentence, ideal) {
    const cap = ideal * 2;
    if (tokenLength(sentence) <= cap) return [{ text: sentence, start: 0, end: sentence.length }];
    const out = [];
    let curStart = 0;
    let tok = 0;
    let lastSoft = -1; // 句内最后一个逗号级边界(含该字符)之后的结束位置
    for (let i = 0; i < sentence.length; i++) {
      const ch = sentence[i];
      tok += WIDE_RE.test(ch) ? 1 : 0.5;
      if (SOFT_END_RE.test(ch)) lastSoft = i + 1;
      const atHard = (i === sentence.length - 1);
      if (tok >= cap || (atHard && tok > 0)) {
        let cutEnd = i + 1;
        // 已超过 ideal 且存在逗号边界时回退到该边界
        if (tok >= ideal && lastSoft > curStart && lastSoft < cutEnd) {
          cutEnd = lastSoft;
        }
        out.push({ text: sentence.slice(curStart, cutEnd), start: curStart, end: cutEnd });
        curStart = cutEnd;
        tok = tokenLength(sentence.slice(curStart, i + 1));
        lastSoft = -1;
      }
    }
    if (curStart < sentence.length) {
      out.push({ text: sentence.slice(curStart), start: curStart, end: sentence.length });
    }
    return out.filter(p => p.text.trim().length > 0);
  }

  function createChunker(idealTokens) {
    const ideal = Math.min(100, Math.max(10, Number(idealTokens) || 30));
    const cap = ideal * 2;
    let pending = '';
    let streamLen = 0;   // 已 feed 的总字符数(可见文本坐标)
    let queue = [];      // 待合并句子 [{text,start,end}]，绝对偏移

    function enqueueSentences(ext, base) {
      for (const s of ext.completed) {
        queue.push({ text: s.text, start: base + s.start, end: base + s.end });
      }
    }

    // 把句子队列按 ideal 合并出块。final=false(流中)时不足 ideal 的尾部句子
    // 留在队列等后续 delta；final=true(flush)时全部出块。
    function mergeDrain(final) {
      const chunks = [];
      while (queue.length) {
        const first = queue[0];
        const firstTok = tokenLength(first.text);
        if (firstTok >= ideal) {
          // 单句已达 ideal：超长单句(>2x ideal)按逗号/硬切内部再切
          if (firstTok > cap) {
            const base = first.start;
            for (const p of splitLongSentence(first.text, ideal)) {
              chunks.push({ text: p.text, start: base + p.start, end: base + p.end });
            }
          } else {
            chunks.push(first);
          }
          queue.shift();
          continue;
        }
        // 跨句合并：积累到 ideal 即出块；绝不超 2x ideal(spec 上限)
        let acc = first;
        let tok = firstTok;
        let i = 1;
        let capBlocked = false;
        while (i < queue.length && tok < ideal) {
          const nt = tokenLength(queue[i].text);
          if (tok + nt > cap) { capBlocked = true; break; }
          tok += nt;
          acc = { text: acc.text + queue[i].text, start: acc.start, end: queue[i].end };
          i += 1;
        }
        if (tok >= ideal || capBlocked || final) {
          queue.splice(0, i);
          chunks.push(acc);
        } else {
          break; // 未达 ideal 且流未结束：等待更多句子
        }
      }
      return chunks;
    }

    return {
      feed(delta) {
        const d = String(delta || '');
        if (!d) return [];
        const base = streamLen - pending.length; // ext 偏移相对 extract 前的 pending
        streamLen += d.length;
        pending += d;
        const ext = extractCompletedSentences(pending);
        pending = ext.rest;
        enqueueSentences(ext, base);
        return mergeDrain(false);
      },
      flush() {
        if (pending.trim()) {
          const base = streamLen - pending.length;
          const leading = pending.length - pending.trimStart().length;
          queue.push({ text: pending.trim(), start: base + leading, end: base + pending.length });
          pending = '';
        }
        const chunks = mergeDrain(true);
        queue = [];
        return chunks;
      },
    };
  }

  // 一次性切分（Configer 预览 / done 兜底用）
  function demoSplit(text, idealTokens) {
    const c = createChunker(idealTokens);
    return [...c.feed(String(text || '')), ...c.flush()].map(p => p.text);
  }

  window.TtsSplitter = { tokenLength, extractCompletedSentences, createChunker, demoSplit };
})();
