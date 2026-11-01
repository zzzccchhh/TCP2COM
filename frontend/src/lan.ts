type Connection = { address: string; token: string };
let connection: Connection | null = null;
let revision: number | undefined;
const sharedAddress = new URLSearchParams(location.hash.slice(1)).get('service');
export const hasSharedAddress = !!sharedAddress;
if (sharedAddress) history.replaceState(null, '', location.pathname + location.search);
export const isLAN = () => !!connection;
export const serviceAddress = () => connection?.address || sharedAddress || (location.hostname.endsWith('github.io') ? '' : location.origin);

async function request(address: string, path: string, body: object, token?: string, signal?: AbortSignal) {
    let response: Response;
    const controller = new AbortController();
    const abort = () => controller.abort();
    if (signal?.aborted) abort();
    signal?.addEventListener('abort', abort, { once: true });
    const timer = setTimeout(abort, 45000);
    try {
        response = await fetch(address + path, {
            method: 'POST', headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
            body: JSON.stringify(body), signal: controller.signal, credentials: 'omit', cache: 'no-store',
        });
    } catch {
        if (signal?.aborted) throw new Error('已取消配对申请');
        throw new Error('无法连接局域网服务。请检查服务、防火墙和浏览器的本地网络权限；也可直接打开服务地址使用同款页面。');
    } finally {
        clearTimeout(timer); signal?.removeEventListener('abort', abort);
    }
    const result = await response.json();
    if (typeof result.revision === 'number') revision = Math.max(revision ?? 0, result.revision);
    if (response.status === 401) throw new Error('配对已失效，请打开「局域网配对」重新连接。');
    if (!result.ok) throw new Error(result.error || '请求失败');
    return result;
}

function install(value: Connection) {
    connection = value; revision = undefined;
    window.pywebview = { api: new Proxy({}, { get: (_, method: string) => async (...args: any[]) => {
        try { return await request(value.address, '/api/call', { method, args, revision }, value.token); }
        catch (error) { return { ok: false, error: error instanceof Error ? error.message : String(error) }; }
    } }) };
}

export function normalizeAddress(address: string) {
    const text = address.trim();
    const url = new URL(/^https?:\/\//i.test(text) ? text : 'http://' + text);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new Error('请输入服务根地址，例如 http://192.168.1.100:8765');
    return url.origin;
}

export async function pair(address: string, name: string, signal: AbortSignal, waiting: (id: string) => void) {
    const origin = normalizeAddress(address);
    const result = await request(origin, '/api/pair/request', { name }, undefined, signal);
    const secret = result.data.request;
    const cancel = () => { void request(origin, '/api/pair/cancel', { request: secret }).catch(() => {}); };
    signal.addEventListener('abort', cancel, { once: true });
    if (signal.aborted) { cancel(); throw new Error('已取消配对申请'); }
    waiting(result.data.id);
    let token: string | undefined;
    try {
        const until = Date.now() + 125000;
        while (!signal.aborted && Date.now() < until) {
            const reply = await request(origin, '/api/pair/status', { request: secret }, undefined, signal);
            if (signal.aborted) throw new Error('已取消配对申请');
            if (reply.data.state === 'approved') { token = reply.data.token; break; }
            if (reply.data.state === 'rejected') throw new Error('服务端已拒绝申请，可核对后重新请求');
            if (reply.data.state !== 'pending') throw new Error('配对申请已取消或超时，请重新请求');
            await new Promise<void>(resolve => {
                const finish = () => { clearTimeout(timer); signal.removeEventListener('abort', finish); resolve(); };
                const timer = setTimeout(finish, 1000); signal.addEventListener('abort', finish, { once: true });
            });
        }
        if (!token) throw new Error(signal.aborted ? '已取消配对申请' : '配对申请已超时，请重新请求');
    } catch (error) { cancel(); throw error; }
    finally { signal.removeEventListener('abort', cancel); }
    const value = { address: origin, token };
    try { sessionStorage.setItem('wifi-bridge-lan', JSON.stringify(value)); } catch { /* Optional persistence. */ }
    install(value);
    window.dispatchEvent(new Event('pywebviewready'));
}

export function disconnect() {
    sessionStorage.removeItem('wifi-bridge-lan'); location.reload();
}

try {
    const saved = JSON.parse(sessionStorage.getItem('wifi-bridge-lan') || 'null');
    if (saved?.address && saved?.token && !window.pywebview) install(saved);
} catch { /* Storage may be disabled; manual pairing remains available. */ }
