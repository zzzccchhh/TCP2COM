type Connection = { address: string; token: string };
let connection: Connection | null = null;
let revision: number | undefined;
export const isLAN = () => !!connection;
export const serviceAddress = () => connection?.address || (location.hostname.endsWith('github.io') ? 'http://192.168.1.100:8765' : location.origin);

async function request(address: string, path: string, body: object, token?: string) {
    let response: Response;
    try {
        response = await fetch(address + path, {
            method: 'POST', headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
            body: JSON.stringify(body), signal: AbortSignal.timeout(45000), credentials: 'omit', cache: 'no-store',
        });
    } catch {
        throw new Error('无法连接局域网服务。请检查服务、防火墙和浏览器的本地网络权限；也可直接打开服务地址使用同款页面。');
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

export async function pair(address: string, code: string, name: string) {
    const url = new URL(address);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new Error('请输入服务根地址，例如 http://192.168.1.100:8765');
    const result = await request(url.origin, '/api/pair', { code: code.trim().toUpperCase(), name });
    const value = { address: url.origin, token: result.data.token };
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
