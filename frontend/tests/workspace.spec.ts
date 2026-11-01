import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { defaults, deviceDefaults, emptySnapshot } from '../src/api';

async function installBridge(page: Page, count = 3) {
    await page.addInitScript(({ settings, config, snapshot, count }) => {
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
            if (method === 'initial') return response({ settings, config, drafts: {} });
            if (method === 'ports') return response([{ device: 'COM88', description: 'USB Serial' }]);
            if (method === 'poll') return response({ ...state, logs: args[0] >= state.cursor ? [] : state.logs });
            if (method === 'update_settings') { Object.assign(settings, args[0]); state.baseline = null; state.epoch++; return response(true); }
            if (method === 'read_device') { state.baseline = { ...config, revision: 23 }; return response(state.baseline); }
            if (method === 'preview_save') return response({ changes: [{ field: 'baud', value: '2000000' }], needed: true, epoch: state.epoch });
            if (method === 'save_device') { state.baseline = null; state.epoch++; return response({ saved: true }); }
            return response(true);
        } }) };
    }, { settings: { ...defaults }, config: deviceDefaults('c5'), snapshot: { ...emptySnapshot }, count });
}

test('browser preview is explicit and does not pretend to connect', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('/');
    await expect(page.getByText('浏览器界面预览', { exact: false })).toBeVisible();
    await page.getByRole('button', { name: '启动桥接' }).click();
    await expect(page.getByRole('status').filter({ hasText: '当前为浏览器预览' })).toBeVisible();
    const status = page.getByRole('status', { name: '网络连接状态' });
    await expect(status).toHaveText('TCP 未连接');
    await expect(status).toHaveCSS('background-color', 'rgb(245, 222, 214)');
    expect(errors).toEqual([]);
});

test('desktop waits for native methods before initializing', async ({ page }) => {
    await page.addInitScript(() => { window.pywebview = { api: {} }; });
    await page.goto('/');
    await page.evaluate(({ settings, config, snapshot }) => {
        window.pywebview = { api: {
            initial: async () => ({ ok: true, data: { settings, config, drafts: {} } }),
            ports: async () => ({ ok: true, data: [] }),
            poll: async () => ({ ok: true, data: snapshot }),
        } };
        window.dispatchEvent(new Event('pywebviewready'));
    }, { settings: defaults, config: deviceDefaults('c5'), snapshot: emptySnapshot });
    await expect(page.getByText('本地运行', { exact: true })).toBeVisible();
    await expect(page.getByRole('status').filter({ hasText: 'is not a function' })).toHaveCount(0);
});

for (const width of [1440, 960, 390]) {
    test(`layout fits ${width}px and all panels remain reachable`, async ({ page }) => {
        await page.setViewportSize({ width, height: 1000 });
        await page.goto('/');
        await expect(page.getByLabel('当前设备')).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
        await page.getByRole('button', { name: '打开配置面板' }).click();
        const dialog = page.getByRole('dialog', { name: '设备配置面板' });
        await expect(dialog).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
        await dialog.getByRole('button', { name: '共享 Wi-Fi' }).click();
        await expect(dialog.getByLabel('SSID', { exact: true })).toBeVisible();
        await page.keyboard.press('Escape');
        await expect(dialog).not.toBeVisible();
    });
}

test('native log filtering, current read and explicit save work through the bridge', async ({ page }) => {
    await installBridge(page);
    await page.goto('/');
    await page.getByRole('button', { name: '运行日志', exact: true }).click();
    await expect(page.getByText('主机连接已就绪', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'ESP（USB）', exact: true }).click();
    await expect(page.getByText('E (123) device error', { exact: true })).toBeVisible();
    await expect(page.getByText('boot: ESP32 ready', { exact: true })).toBeVisible();
    await expect(page.locator('.log-row')).toHaveCount(2);
    await expect(page.locator('.log-origin')).toHaveText(['ESP', 'ESP']);
    await expect(page.getByRole('button', { name: 'USB', exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'ESP', exact: true })).toHaveCount(0);
    await expect(page.getByText('主机连接已就绪', { exact: true })).not.toBeVisible();
    await page.getByRole('button', { name: '打开配置面板' }).click();
    const panel = page.getByRole('dialog', { name: '设备配置面板' });
    await expect(panel.getByRole('button', { name: '保存并重启', exact: true })).toBeDisabled();
    await panel.getByRole('button', { name: '读取设备', exact: true }).click();
    await panel.getByLabel('设备 UART 波特率 / bps', { exact: true }).fill('2000000');
    await panel.getByRole('button', { name: '保存并重启', exact: true }).click();
    const confirm = page.getByRole('dialog', { name: '确认保存设备配置' });
    await expect(confirm).toBeVisible();
    expect(await page.evaluate(() => (window as any).__calls.filter((row: any[]) => row[0] === 'save_device').length)).toBe(0);
    await confirm.getByRole('button', { name: '确认保存并重启' }).click();
    await expect(confirm).not.toBeVisible();
    await expect.poll(() => page.evaluate(() => (window as any).__calls.filter((row: any[]) => row[0] === 'save_device').length)).toBe(1);
});

test('switching devices clears password edits and S3 exposes only 2.4GHz', async ({ page }) => {
    await installBridge(page);
    await page.goto('/');
    await page.getByRole('button', { name: '打开配置面板' }).click();
    const panel = page.getByRole('dialog', { name: '设备配置面板' });
    await panel.getByRole('button', { name: '自建热点', exact: true }).click();
    await panel.getByLabel('新密码（留空保留）', { exact: true }).fill('do-not-restore');
    await panel.getByRole('button', { name: '关闭面板' }).click();
    await page.getByLabel('当前设备', { exact: true }).selectOption('s3');
    await page.getByRole('button', { name: '打开配置面板' }).click();
    await expect(panel.getByLabel('新密码（留空保留）', { exact: true })).toHaveValue('');
    await panel.getByRole('button', { name: '接口与联网', exact: true }).click();
    await expect(panel.getByLabel('Wi-Fi 频段', { exact: true }).locator('option')).toHaveCount(1);
    await expect(panel.getByRole('button', { name: '保存并重启', exact: true })).toBeDisabled();
});

test('startup defaults to traffic monitoring, including after reload', async ({ page }) => {
    await installBridge(page);
    await page.goto('/');
    await expect(page.getByRole('img', { name: /最近30秒速率/ })).toHaveCount(3);
    await expect(page.getByRole('log', { name: '运行日志' })).toHaveCount(0);
    await page.getByRole('button', { name: '运行日志', exact: true }).click();
    await expect(page.getByRole('log', { name: '运行日志' })).toBeVisible();
    await page.reload();
    await expect(page.getByRole('img', { name: /最近30秒速率/ })).toHaveCount(3);
    await page.getByRole('button', { name: '查看链路诊断', exact: true }).click();
    const panel = page.getByRole('dialog', { name: '链路诊断' });
    await expect(panel.getByText('COM 写平均 / 最大', { exact: true })).toBeVisible();
    await expect(panel.getByLabel('追加日志文件路径（可选）', { exact: true })).toBeVisible();
});

test('traffic curves freeze without bridging even with USB connected, and resume on start', async ({ page }) => {
    await installBridge(page);
    await page.goto('/');
    await expect(page.getByText('本地运行', { exact: true })).toBeVisible();
    const curves = page.locator('.chart polyline');
    const points = () => curves.evaluateAll(elements => elements.map(element => element.getAttribute('points')));
    const pollCount = () => page.evaluate(() => (window as any).__calls.filter((row: any[]) => row[0] === 'poll').length);
    await page.evaluate(() => {
        const state = (window as any).__bridgeState;
        state.usb = { connected: true, closing: false, port: 'COM88', age: 0, fields: {} };
        state.rates = [1000, 800, 200];
    });
    const idlePolls = await pollCount();
    await expect.poll(pollCount).toBeGreaterThanOrEqual(idlePolls + 3);
    await expect(page.getByRole('button', { name: '断开 USB', exact: true })).toBeVisible();
    expect(await points()).toEqual(['', '', '']);

    await page.evaluate(() => { Object.assign((window as any).__bridgeState, { running: true, connected: true }); });
    await expect.poll(async () => (await points())[0]!.trim().split(' ').length).toBeGreaterThanOrEqual(2);
    await page.evaluate(() => {
        const state = (window as any).__bridgeState;
        state.running = false;
        state.connected = false;
        state.rates = [0, 0, 0];
    });
    await expect(page.locator('.live-label')).toHaveText('IDLE');
    const frozen = await points();
    const stoppedPolls = await pollCount();
    await expect.poll(pollCount).toBeGreaterThanOrEqual(stoppedPolls + 3);
    expect(await points()).toEqual(frozen);

    await page.evaluate(() => {
        const state = (window as any).__bridgeState;
        state.running = true;
        state.connected = true;
        state.rates = [500, 400, 100];
    });
    await expect.poll(points).not.toEqual(frozen);
    await expect(page.locator('.live-label')).toHaveText('LIVE');
});

for (const mode of ['tcp-client', 'tcp-server']) {
    test(`${mode} highlights connection state and freezes traffic until connected`, async ({ page }) => {
        await installBridge(page);
        await page.goto('/');
        await expect(page.getByText('本地运行', { exact: true })).toBeVisible();
        await page.evaluate(mode => Object.assign((window as any).__bridgeState, {
            running: true, connected: false, running_settings: { mode }, rates: [1000, 800, 200],
        }), mode);
        const status = page.getByRole('status', { name: '网络连接状态' });
        const curves = page.locator('.chart polyline');
        const points = () => curves.evaluateAll(elements => elements.map(element => element.getAttribute('points')));
        const pollCount = () => page.evaluate(() => (window as any).__calls.filter((row: any[]) => row[0] === 'poll').length);
        await expect(status).toHaveText(mode === 'tcp-server' ? 'TCP 未连接 · 等待设备' : 'TCP 未连接 · 重连中');
        await expect(status).toHaveCSS('background-color', 'rgb(241, 226, 191)');
        await expect(status).toBeInViewport();
        await expect(page.locator('.live-label')).toHaveText('PAUSED');
        const waitingPolls = await pollCount();
        await expect.poll(pollCount).toBeGreaterThanOrEqual(waitingPolls + 2);
        expect(await points()).toEqual(['', '', '']);

        await page.evaluate(() => { (window as any).__bridgeState.connected = true; });
        await expect(status).toHaveText('TCP 已连接');
        await expect(status).toHaveCSS('background-color', 'rgb(219, 232, 210)');
        await expect(page.locator('.live-label')).toHaveText('LIVE');
        await expect.poll(async () => (await points())[0]!.trim().split(' ').length).toBeGreaterThanOrEqual(2);

        await page.evaluate(() => { (window as any).__bridgeState.connected = false; });
        await expect(status).toHaveAttribute('data-state', 'waiting');
        await expect(page.locator('.live-label')).toHaveText('PAUSED');
        const frozen = await points();
        // An unapplied UDP draft must not change the running TCP connection's rules.
        await page.getByRole('button', { name: 'UDP', exact: true }).click();
        const disconnectedPolls = await pollCount();
        await expect.poll(pollCount).toBeGreaterThanOrEqual(disconnectedPolls + 2);
        await expect(status).toContainText('TCP 未连接');
        expect(await points()).toEqual(frozen);

        await page.evaluate(() => { (window as any).__bridgeState.connected = true; });
        await expect(status).toHaveText('TCP 已连接');
        await expect.poll(points).not.toEqual(frozen);
    });
}

test('UDP keeps sampling while bound without claiming a TCP connection', async ({ page }) => {
    await installBridge(page);
    await page.goto('/');
    await expect(page.getByText('本地运行', { exact: true })).toBeVisible();
    await page.evaluate(() => Object.assign((window as any).__bridgeState, {
        running: true, connected: false, running_settings: { mode: 'udp' }, rates: [1000, 800, 200],
    }));
    const status = page.getByRole('status', { name: '网络连接状态' });
    await expect(status).toHaveText('UDP 已绑定');
    await expect(status).toHaveAttribute('data-state', 'udp');
    await expect(page.locator('.live-label')).toHaveText('LIVE');
    const points = () => page.locator('.chart polyline').first().getAttribute('points');
    await expect.poll(async () => (await points())!.trim().split(' ').length).toBeGreaterThanOrEqual(2);
    await page.getByRole('button', { name: 'TCP 客户端', exact: true }).click();
    const before = await points();
    await expect.poll(points).not.toEqual(before);
    await expect(status).toHaveText('UDP 已绑定');
});

test('log backlog stays bounded and remains searchable', async ({ page }) => {
    await installBridge(page, 3100);
    await page.goto('/');
    await page.getByRole('button', { name: '运行日志', exact: true }).click();
    await expect(page.locator('.log-row')).toHaveCount(3000);
    await page.getByLabel('搜索日志', { exact: true }).fill('load 3100');
    await expect(page.locator('.log-row')).toHaveCount(1);
    await expect(page.getByText('load 3100', { exact: true })).toBeVisible();
});

test('high DPI workspace has no clipped columns', async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: 960, height: 720 }, deviceScaleFactor: 1.5 });
    const page = await context.newPage();
    await page.goto('http://127.0.0.1:4173');
    await expect(page.getByRole('button', { name: '启动桥接' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await context.close();
});

test('desktop keeps main controls and metrics in the first screen', async ({ page }) => {
    await page.setViewportSize({ width: 1420, height: 920 });
    await installBridge(page);
    await page.goto('/');
    await expect(page.getByText('本地运行', { exact: true })).toBeVisible();
    const metrics = await page.getByLabel('桥接统计', { exact: true }).boundingBox();
    expect(metrics!.y + metrics!.height).toBeLessThanOrEqual(920);
    await expect(page.getByRole('button', { name: '启动桥接' })).toBeInViewport();
    await expect(page.getByRole('button', { name: '打开配置面板' })).toBeInViewport();
});
