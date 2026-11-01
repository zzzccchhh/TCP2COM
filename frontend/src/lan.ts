type Connection = { address: string; token: string };
type Saved = { address: string; token?: string; blocked?: boolean };
type LANStatus = { state: 'idle' | 'connecting' | 'connected' | 'error' | 'revoked' | 'disconnected'; address: string; message: string };
const storageKey = 'wifi-bridge-lan';
const fragment = new URLSearchParams(location.hash.slice(1));
let sharedAddress = fragment.get('service');
let sharedInvite = fragment.get('invite');
const bootstrap = (window as any).__WIFI_BRIDGE__ as { invite?: string } | undefined;
if (sharedAddress || sharedInvite) history.replaceState(null, '', location.pathname + location.search);
let connection: Connection | null = null;
let revision: number | undefined;
let attempt = 0;
let restored = false;
let enrollment: { address: string; invite: string; join_id: string } | null = null;
let status: LANStatus = { state: 'idle', address: '', message: '打开服务端提供的连接链接或扫码，即可自动连接。' };
export const isLAN = () => !!connection;
export const getLANStatus = () => status;
export const defaultTerminalName = () => /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent) || navigator.maxTouchPoints > 0 ? '手机 / 平板浏览器' : '电脑浏览器';

function load(): Saved | null {
    try { return JSON.parse(localStorage.getItem(storageKey) || sessionStorage.getItem(storageKey) || 'null'); } catch { return null; }
}
function remember(value: Saved) {
    try { localStorage.setItem(storageKey, JSON.stringify(value)); } catch { /* Storage may be disabled. */ }
    try { sessionStorage.setItem(storageKey, JSON.stringify(value)); } catch { /* Connection works without storage. */ }
}
function update(state: LANStatus['state'], address: string, message: string) {
    status = { state, address, message }; window.dispatchEvent(new Event('lanstatus'));
}
export const serviceAddress = () => connection?.address || sharedAddress || (bootstrap ? location.origin : load()?.address || '');

class HTTPError extends Error {
    constructor(message: string, public status: number) { super(message); }
}
async function request(address: string, path: string, body: object, token?: string, signal?: AbortSignal) {
    const controller = new AbortController();
    const abort = () => controller.abort();
    if (signal?.aborted) abort();
    signal?.addEventListener('abort', abort, { once: true });
    const timer = setTimeout(abort, 45000);
    try {
        const response = await fetch(address + path, {
            method: 'POST', headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
            body: JSON.stringify(body), signal: controller.signal, credentials: 'omit', cache: 'no-store',
        });
        const result = await response.json();
        if (!result.ok) throw new HTTPError(result.error || '连接失败', response.status);
        return result;
    } catch (error) {
        if (error instanceof HTTPError) throw error;
        if (signal?.aborted) throw new Error('已取消连接');
        throw new Error('无法连接服务。请检查服务、防火墙和浏览器本地网络权限；也可直接打开局域网工作台。');
    } finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); }
}

function install(value: Connection) {
    connection = value; revision = undefined;
    enrollment = null;
    remember(value);
    window.pywebview = { api: new Proxy({}, { get: (_, method: string) => async (...args: any[]) => {
        try {
            const reply = await request(value.address, '/api/call', { method, args, revision }, value.token);
            if (connection === value && typeof reply.revision === 'number') revision = Math.max(revision ?? 0, reply.revision);
            if (connection === value && method === 'poll' && status.state === 'error' && status.address === value.address) update('connected', value.address, '已恢复服务连接。');
            return reply;
        } catch (error) {
            if (connection === value && error instanceof HTTPError && error.status === 401) {
                remember({ address: value.address, blocked: true });
                update('revoked', value.address, '连接已撤销或服务已重启，请重新打开服务端连接链接。');
            } else if (connection === value && !(error instanceof HTTPError)) {
                update('error', value.address, error instanceof Error ? error.message : String(error));
            }
            return { ok: false, error: error instanceof Error ? error.message : String(error) };
        }
    } }) };
    update('connected', value.address, '已连接服务，状态已同步。串口和 USB 需要手动启动。');
    window.dispatchEvent(new Event('pywebviewready'));
}

export function normalizeAddress(address: string) {
    const text = address.trim();
    const url = new URL(/^https?:\/\//i.test(text) ? text : 'http://' + text);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new Error('请输入服务根地址，例如 http://192.168.1.100:8765');
    return url.origin;
}

export async function connect(address: string, name = defaultTerminalName(), invite?: string, signal?: AbortSignal) {
    const current = ++attempt;
    let origin = address;
    try {
        origin = normalizeAddress(address);
        update('connecting', origin, '正在自动连接服务…');
        const saved = load();
        let token: string | undefined;
        const authorization = invite || (origin === location.origin ? bootstrap?.invite : undefined);
        if (saved?.address === origin && saved.token) {
            try { await request(origin, '/api/call', { method: 'initial', args: [] }, saved.token, signal); token = saved.token; }
            catch (error) { if (!(error instanceof HTTPError && error.status === 401 && authorization)) throw error; }
        }
        if (!token) {
            if (!authorization) throw new Error('请从服务端复制完整连接网址或扫码；首次访问无法仅凭固定网页网址确定并授权后端。');
            if (!enrollment || enrollment.address !== origin || enrollment.invite !== authorization) {
                const bytes = crypto.getRandomValues(new Uint8Array(24));
                enrollment = { address: origin, invite: authorization, join_id: [...bytes].map(n => n.toString(16).padStart(2, '0')).join('') };
            }
            const result = await request(origin, '/api/connect', { invite: authorization, name, join_id: enrollment.join_id }, undefined, signal);
            token = result.data.token;
        }
        if (current !== attempt || signal?.aborted) return;
        install({ address: origin, token: token! });
    } catch (error) {
        if (current === attempt) {
            if (error instanceof HTTPError && error.status === 401) remember({ address: origin, blocked: true });
            update('error', origin, error instanceof Error ? error.message : String(error));
        }
    }
}

export async function autoConnect() {
    if (restored || (window.pywebview && !connection)) return;
    restored = true;
    const saved = load();
    const address = serviceAddress();
    if (!address) return;
    if (!sharedInvite && saved?.blocked && normalizeAddress(address) === saved.address) {
        update('disconnected', saved.address, '此终端已断开或被撤销；重新打开服务端连接链接，或点击连接服务重试。'); return;
    }
    await connect(address, defaultTerminalName(), sharedInvite || undefined);
}

export function retryConnection(address: string, name: string, signal?: AbortSignal) {
    let invite: string | undefined;
    try { if (sharedInvite && normalizeAddress(address) === normalizeAddress(sharedAddress || location.origin)) invite = sharedInvite; }
    catch { /* connect() reports invalid addresses. */ }
    return connect(address, name, invite, signal);
}
export function disconnect() {
    attempt++;
    remember({ address: connection?.address || serviceAddress(), blocked: true });
    location.reload();
}

// Browsers can reuse the same tab when a refreshed share link changes only the fragment.
window.addEventListener('hashchange', () => {
    const next = new URLSearchParams(location.hash.slice(1));
    if (!next.has('service') && !next.has('invite')) return;
    sharedAddress = next.get('service'); sharedInvite = next.get('invite');
    history.replaceState(null, '', location.pathname + location.search);
    if (window.pywebview && !connection) return;
    void connect(sharedAddress || location.origin, defaultTerminalName(), sharedInvite || undefined);
});
