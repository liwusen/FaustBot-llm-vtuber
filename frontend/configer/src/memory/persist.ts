// 记忆模块 localStorage 持久化：键名逐字沿用旧实现，读写失败只告警不中断。

export const MEM_TREE_KEY = 'faustbot.memory.tree';
export const MEM_VIEW_KEY = 'faustbot.memory.view';
export const MEM_COLUMNS_KEY = 'faustbot.memory.columns';
export const MEM_VIZ_TAB_KEY = 'faustbot.memory.viztab';

export function memReadLocal<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch (err) {
    console.warn('[memory] 本地状态读取失败', key, err);
    return fallback;
  }
}

export function memWriteLocal(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch (err) {
    console.warn('[memory] 本地状态写入失败', key, err);
  }
}

/** 旧代码的「读数组且非空才采用」规则。 */
export function memReadNonEmptyArray(key: string, fallback: string[]): string[] {
  const saved = memReadLocal<unknown>(key, fallback);
  return Array.isArray(saved) && saved.length ? saved.map(String) : fallback;
}
