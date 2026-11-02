export type Data = Record<string, any>;
type Reply = { ok: boolean; data?: any; error?: string };

declare global {
    interface Window {
        pywebview?: { api: Record<string, (...args: any[]) => Promise<Reply>> };
    }
}

export const defaults: Data = {
    device: 'c5', mode: 'tcp-server', host: '', listen: '0.0.0.0', port: '8080',
    peer_port: '6060', udp_payload: '1200', com: 'COM1', baud: '1152000', bytesize: '8',
    parity: 'N', stopbits: '1', flow: 'none', timeout: '0.02', write_timeout: '2', reconnect: '2',
    connect_timeout: '4', keepalive_idle: '3', keepalive_interval: '1', keepalive_count: '5',
    bufsize: '65536', stats_interval: '0.25', control_port: '8081', no_reset_ui: false, verbose: false,
    usb_port: '', usb_baud: '115200', usb_log: '', config_transport: 'usb', config_url: 'http://192.168.4.1/',
};

export function deviceDefaults(device: string): Data {
    const station = { ssid: '', static_ip: false, ip: '192.168.137.200', gateway: '192.168.137.1', netmask: '255.255.255.0', dns: '192.168.137.1' };
    return {
        link_mode: 0, mode: 0, band: 0, baud: 1152000, tcp_port: 8080,
        ap_channel: 1, ap_ssid: `ESP32-${device.toUpperCase()}-Bridge`, ap_ip: '192.168.4.1',
        ap_gateway: '192.168.4.1', ap_netmask: '255.255.255.0', net_role: 0, auto_reconnect: true,
        remote_ip: '192.168.137.1', remote_port: 8080, local_port: 6060, reconnect_ms: 1000,
        connect_timeout_ms: 10000, up_kb: 4096, down_kb: 1024, ...(device !== 'esp01s' ? { reset_pin: 0 } : {}),
        ...(device === 'c5' ? { flow_pin: 1 } : {}),
        pc: { ...station }, shared: { ...station },
    };
}

export const emptySnapshot: Data = {
    state: '已停止', running: false, connected: false, busy: false, running_settings: null,
    applied: false, valid: true, address: '业务桥接已停止', rates: [0, 0, 0], stats: {},
    queued: 0, peak: 0, usb: null, config_busy: false, config_status: '尚未读取设备', baseline: null,
    epoch: 0, state_status: '设置会自动记住；连接需手动启动', log_dropped: 0, logs: [], cursor: 0,
    log_generation: 0, reset: { count: 0, busy: false, message: '3 秒内连续点击 5 次，复位外部主控' },
};

export const labels: Data = { c5: 'ESP32-C5', s3: 'ESP32-S3', esp01s: 'ESP-01S' };

export function isTrafficActive(snapshot: Data, fallbackMode: string): boolean {
    const mode = snapshot.running_settings?.mode ?? fallbackMode;
    return !!snapshot.running && (mode === 'udp' || !!snapshot.connected);
}

export function applyDraft(base: Data, draft: Data, device: string): Data {
    const result = structuredClone(base);
    const schema = deviceDefaults(device);
    Object.entries(draft).forEach(([group, fields]) => {
        if (group === 'pc' || group === 'shared') result[group] = { ...result[group], ...fields };
        else Object.entries(fields).forEach(([key, value]) => {
            result[key] = typeof schema[key] === 'number' && typeof value === 'string' && /^\d+$/.test(value) ? Number(value) : value;
        });
    });
    if (device === 'c5' && [1, 2].includes(Number(result.link_mode))) result.link_mode = -1;
    return result;
}

export async function call<T = any>(method: string, ...args: any[]): Promise<T> {
    const api = window.pywebview?.api;
    if (!api) throw new Error('当前为浏览器预览。请使用“启动_新版界面.vbs”连接设备。');
    const reply = await api[method](...args);
    if (!reply.ok) throw new Error(reply.error || '操作未完成');
    return reply.data as T;
}

export function size(value: number, rate = false): string {
    const n = Number.isFinite(value) ? value : 0;
    const unit = n >= 1048576 ? 'MiB' : n >= 1024 ? 'KiB' : 'B';
    const divisor = n >= 1048576 ? 1048576 : n >= 1024 ? 1024 : 1;
    return `${(n / divisor).toFixed(divisor === 1 ? 0 : 1)} ${unit}${rate ? '/s' : ''}`;
}

export function download(name: string, text: string, mime = 'text/plain;charset=utf-8') {
    const url = URL.createObjectURL(new Blob([text], { type: mime }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = name;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}
