function detailText(detail: unknown): string | null {
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (typeof item === 'string') return item;
        if (item && typeof item === 'object') {
          const msg = (item as { msg?: unknown; message?: unknown }).msg
            ?? (item as { message?: unknown }).message;
          if (typeof msg === 'string') return msg;
        }
        return '';
      })
      .filter(Boolean);
    if (parts.length) return parts.join('；');
  }
  if (detail && typeof detail === 'object') {
    const obj = detail as { message?: unknown; error?: unknown; detail?: unknown };
    if (typeof obj.message === 'string' && obj.message.trim()) return obj.message;
    if (typeof obj.error === 'string' && obj.error.trim()) return obj.error;
    if (typeof obj.detail === 'string' && obj.detail.trim()) return obj.detail;
  }
  return null;
}

/** 统一把 IPC/HTTP 错误转成界面使用的短文案。 */
export function formatApiError(err: unknown): string {
  if (err == null) return '未知错误';
  if (typeof err === 'string') return err;
  if (err instanceof Error && err.message) return err.message;
  const obj = err as {
    response?: { detail?: unknown; data?: { detail?: unknown } };
    detail?: unknown;
    message?: unknown;
    error?: unknown;
  };
  return (
    detailText(obj.response?.detail)
    ?? detailText(obj.response?.data?.detail)
    ?? detailText(obj.detail)
    ?? (typeof obj.message === 'string' && obj.message ? obj.message : null)
    ?? detailText(obj.error)
    ?? String(err)
  );
}
