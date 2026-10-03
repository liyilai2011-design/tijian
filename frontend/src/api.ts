let csrfToken = '';
export function setCsrf(token: string) { csrfToken = token; }
export class ApiError extends Error {
  constructor(message: string, public status: number, public uncertain = false) { super(message); this.name = 'ApiError'; }
}
export function clearRecoveredNetworkError(error: Error | null): Error | null {
  return error instanceof ApiError && error.status === 0 ? null : error;
}
export async function checkServerConnection(): Promise<void> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 10000);
  let status = 0;
  try {
    const response = await fetch('/api/health', {
      credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
    });
    status = response.status;
    if (status !== 200) throw new Error('Health check failed');
    const data = await response.json();
    if (data?.status !== 'ok') throw new Error('Health check failed');
  } catch {
    throw new ApiError('服务器连接失败，请检查局域网连接后重试。', status);
  } finally {
    window.clearTimeout(timer);
  }
}
export async function api<T>(path: string, options: { method?: string; body?: unknown } = {}): Promise<T> {
  const method = options.method || 'GET';
  const write = method !== 'GET';
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 20000);
  let response: Response;
  let data: unknown;
  try {
    response = await fetch('/api' + path, {
      method, credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
      headers: { ...(write ? { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken } : {}) },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
    // Keep the timeout active until the whole response has arrived.
    data = await response.json().catch(error => {
      if (error instanceof SyntaxError) return null;
      throw error;
    });
  } catch {
    window.dispatchEvent(new Event('api-offline'));
    throw new ApiError(write ? '数据尚未保存，请检查网络连接后重新尝试。请求可能已到达服务器，请先核对保存状态。' : '无法连接服务器，请检查局域网连接后重试。', 0, write);
  } finally { window.clearTimeout(timer); }
  window.dispatchEvent(new Event('api-online'));
  if (!response.ok) {
    if (response.status === 401 && path !== '/auth/login') window.dispatchEvent(new Event('session-expired'));
    const detail = data && typeof data === 'object' && 'detail' in data ? data.detail : undefined;
    let message = typeof detail === 'string' ? detail : '请求失败，请稍后重试。';
    if (response.status === 422 && typeof detail !== 'string') message = '填写内容不符合要求，请检查必填项和日期格式。';
    if (response.status === 401 && path !== '/auth/login') message = '登录已过期，请重新登录后重试；当前输入已保留。';
    if (response.status === 409) message += ' 请刷新服务器数据并核对后再提交。';
    throw new ApiError(message, response.status, write && response.status >= 500);
  }
  if (data === null) throw new ApiError('服务器返回了无法识别的响应，请刷新核对保存状态。', response.status, write);
  return data as T;
}
export function errorMessage(error: unknown) { return error instanceof Error ? error.message : '操作失败，请重试。'; }
export function requestId() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
  const h = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}
