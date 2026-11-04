import { expect, test } from '@playwright/test';
import { installBridge } from './helpers';
import { defaults, deviceDefaults, emptySnapshot } from '../src/api';

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
        const dialog = page.getByRole(width <= 760 ? 'region' : 'dialog', { name: '设备配置面板' });
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

test('desktop keeps main actions visible and enlarged statistics reachable', async ({ page }) => {
    await page.setViewportSize({ width: 1420, height: 920 });
    await installBridge(page);
    await page.goto('/');
    await expect(page.getByText('本地运行', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '启动桥接' })).toBeInViewport();
    await expect(page.getByRole('button', { name: '打开配置面板' })).toBeInViewport();
    await page.getByLabel('桥接统计', { exact: true }).scrollIntoViewIfNeeded();
    await expect(page.getByLabel('桥接统计', { exact: true })).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});


test('new C5 exposes GPIO flow mode and pin without changing S3 options', async ({ page }) => {
    await installBridge(page);
    await page.goto('/');
    await page.getByRole('button', { name: '打开配置面板' }).click();
    const panel = page.getByRole('dialog', { name: '设备配置面板' });
    await panel.getByRole('button', { name: '读取设备', exact: true }).click();
    const mode = panel.getByLabel('MCU 接口', { exact: true });
    await expect(mode.locator('option')).toHaveText(['UART 原始透传', 'SPI传输', 'UART GPIO 流控透传']);
    await mode.selectOption('4');
    await expect(panel.getByText('SPI传输仅发送 MCU 数据到网络', { exact: false })).toBeVisible();
    await panel.getByRole('button', { name: '保存并重启', exact: true }).click();
    const spiPreview = await page.evaluate(() => (window as any).__calls.findLast((row: any[]) => row[0] === 'preview_save'));
    expect(spiPreview[1].link_mode).toBe(4);
    await page.getByRole('dialog', { name: '确认保存设备配置' }).getByRole('button', { name: '取消', exact: true }).click();
    await mode.selectOption('3');
    await panel.getByLabel('串口流控线（高暂停 / 低允许）', { exact: true }).selectOption('4');
    await panel.getByRole('button', { name: '保存并重启', exact: true }).click();
    const preview = await page.evaluate(() => (window as any).__calls.findLast((row: any[]) => row[0] === 'preview_save'));
    expect(preview[1].link_mode).toBe(3);
    expect(preview[1].flow_pin).toBe(4);
    await page.getByRole('dialog', { name: '确认保存设备配置' }).getByRole('button', { name: '取消', exact: true }).click();
    await panel.getByRole('button', { name: '关闭面板' }).click();
    await page.getByLabel('当前设备', { exact: true }).selectOption('s3');
    await page.getByRole('button', { name: '打开配置面板' }).click();
    await expect(panel.getByLabel('串口流控线（高暂停 / 低允许）', { exact: true })).toHaveCount(0);
    await expect(panel.getByLabel('MCU 接口', { exact: true }).locator('option')).toHaveText(['UART 原始透传', 'SPI WBR/1', 'UART WBR/1']);
});


test('C5 old drafts require selecting an interface without losing network fields', async ({ page }) => {
    await installBridge(page, 3, true);
    await page.goto('/');
    await page.getByRole('button', { name: '打开配置面板' }).click();
    const panel = page.getByRole('dialog', { name: '设备配置面板' });
    const mode = panel.getByLabel('MCU 接口', { exact: true });
    await expect(mode.locator('option[value="2"]')).toHaveCount(0);
    await panel.getByRole('button', { name: '读取设备', exact: true }).click();
    await panel.getByRole('button', { name: '恢复草稿', exact: true }).click();
    await expect(mode).toHaveValue('-1');
    await expect(mode.locator('option[value="2"]')).toHaveCount(0);
    await expect(panel.getByLabel('设备目标端口', { exact: true })).toHaveValue('9000');
    await expect(panel.getByRole('button', { name: '保存并重启', exact: true })).toBeDisabled();
    await mode.selectOption('0');
    await expect(panel.getByRole('button', { name: '保存并重启', exact: true })).toBeEnabled();
});
