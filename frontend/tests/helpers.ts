import type { Page } from '@playwright/test';
import { defaults, deviceDefaults, emptySnapshot } from '../src/api';

export async function installBridge(page: Page, count = 3, retiredDraft = false, networkLogs = false) {
    await page.addInitScript(({ settings, config, snapshot, count, retiredDraft, networkLogs }) => {
        if (networkLogs) Object.assign(config, { log_enabled: true, log_port: 19082 });
        const state = { ...snapshot, logs: [
            { id: 1, time: '12:00:00', source: 'host', level: '', text: '主机连接已就绪' },
            { id: 2, time: '12:00:01', source: 'usb', level: 'E', text: 'E (123) device error' },
            { id: 3, time: '12:00:02', source: 'usb', level: '', text: 'boot: ESP32 ready' },
        ], cursor: count };
        for (let i = 4; i <= count; i++) state.logs.push({ id: i, time: '12:00:02', source: 'host', level: '', text: `load ${i}` });
        const calls: any[] = [];
        (window as any).__calls = calls;
        (window as any).__bridgeState = state;
        const response = (data: any) => Promise.resolve({ ok: true, data });
        window.pywebview = { api: new Proxy({}, { get: (_, method: string) => (...args: any[]) => {
            calls.push([method, ...args]);
            if (method === 'initial') return response({ settings, config, drafts: retiredDraft ? { c5: { common: { link_mode: 2, remote_port: '9000' } } } : {} });
            if (method === 'ports') return response([{ device: 'COM88', description: 'USB Serial' }]);
            if (method === 'poll') return response({ ...state, logs: state.logs.filter(row => row.id > args[0]) });
            if (method === 'update_settings') { Object.assign(settings, args[0]); state.baseline = null; state.epoch++; return response(true); }
            if (method === 'network_log') { state.net_log = args[0] ? { connected: true, host: settings.net_log_host, port: Number(settings.net_log_port), message: '已连接', fields: { IP: settings.net_log_host }, age: 1, dropped: 0 } : null; return response(true); }
            if (method === 'read_device') { state.baseline = { ...config, revision: 23 }; return response(state.baseline); }
            if (method === 'preview_save') return response({ changes: [{ field: 'baud', value: '2000000' }], needed: true, epoch: state.epoch });
            if (method === 'save_device') { state.baseline = null; state.epoch++; return response({ saved: true }); }
            return response(true);
        } }) };
    }, { settings: { ...defaults }, config: deviceDefaults('c5'), snapshot: { ...emptySnapshot }, count, retiredDraft, networkLogs });
}
