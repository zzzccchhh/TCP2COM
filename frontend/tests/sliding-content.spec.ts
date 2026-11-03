import { expect, test } from '@playwright/test';
import type { Locator, Page } from '@playwright/test';
import { join } from 'node:path';
import { installBridge } from './helpers';

async function ready(page: Page, count = 3, theme = 'light') {
    await installBridge(page, count);
    await page.addInitScript(value => localStorage.setItem('wifi-bridge-appearance', value), theme);
    await page.goto('/'); await page.bringToFront();
    await expect(page.getByText('本地运行', { exact: true })).toBeVisible();
}
async function settle(page: Page) { await expect(page.locator('[data-sliding]')).toHaveCount(0); }
async function x(node: Locator) { return node.evaluate(element => new DOMMatrixReadOnly(getComputedStyle(element).transform).m41); }

for (const kind of ['network', 'workspace', 'config', 'transport', 'logs'] as const) {
    test(`${kind} content waits for release, then slides in the selected direction`, async ({ page }) => {
        await ready(page);
        if (kind === 'config') await page.getByRole('button', { name: '打开配置面板', exact: true }).click();
        if (kind === 'logs') { await page.getByRole('button', { name: '运行日志', exact: true }).click(); await settle(page); }
        const cases = {
            network: ['电脑网络角色', 'TCP 服务端', 'UDP', '.network-content', 'tcp-server', 'udp', 1],
            workspace: ['工作区视图', '流量监测', '运行日志', '.workspace-content', 'traffic', 'logs', -1],
            config: ['配置分页', '接口与联网', '自建热点', '.config-content', 'common', 'ap', 1],
            transport: ['设备配置连接方式', 'USB 配置', 'Wi-Fi / HTTP', '.transport-content', 'usb', 'http', 1],
            logs: ['日志来源', '全部', 'ESP（USB）', '.log-content', 'all', 'usb', 1],
        };
        const [label, from, to, selector, before, after, direction] = cases[kind];
        const group = page.getByRole('group', { name: String(label), exact: true });
        const source = group.getByRole('button', { name: new RegExp(`^${from}`) });
        const target = group.getByRole('button', { name: String(to), exact: true });
        const view = page.locator(String(selector));
        await source.hover(); // Wait for an opening drawer to finish before measuring pointer coordinates.
        const start = (await source.boundingBox())!, end = (await target.boundingBox())!;
        await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2); await page.mouse.down();
        await page.mouse.move(end.x + end.width / 2, end.y + end.height / 2, { steps: 8 });
        await expect(group).toHaveAttribute('data-dragging', 'true');
        await expect(view).toHaveAttribute('data-page', String(before));
        await expect(view).not.toHaveAttribute('data-sliding', 'true');
        expect(await x(view.locator(':scope > .slide-live'))).toBe(0);
        const calls = await page.evaluate(() => (window as any).__calls.filter((call: any[]) => call[0] === 'update_settings').length);
        expect(calls).toBe(0);
        await page.mouse.up();
        await expect(view).toHaveAttribute('data-page', String(after));
        await expect(view).toHaveAttribute('data-sliding', 'true');
        expect((await x(view.locator(':scope > .slide-live'))) * Number(direction)).toBeGreaterThan(0);
        await settle(page);
        expect(await x(view.locator(':scope > .slide-live'))).toBe(0);
        expect(await page.evaluate(() => (window as any).__calls.filter((call: any[]) => call[0] === 'update_settings').length)).toBe(kind === 'network' || kind === 'transport' ? 1 : 0);
    });
}

test('cancelled and same-option drags leave the page still', async ({ page }) => {
    await ready(page);
    const group = page.getByRole('group', { name: '电脑网络角色', exact: true });
    const server = (await group.getByRole('button', { name: 'TCP 服务端', exact: true }).boundingBox())!;
    const udp = (await group.getByRole('button', { name: 'UDP', exact: true }).boundingBox())!;
    for (const cancel of [true, false]) {
        await page.mouse.move(server.x + server.width / 2, server.y + server.height / 2); await page.mouse.down();
        await page.mouse.move(udp.x + udp.width / 2, udp.y + udp.height / 2);
        if (cancel) await page.keyboard.press('Escape');
        else await page.mouse.move(server.x + server.width / 2, server.y + server.height / 2);
        await page.mouse.up();
        await expect(page.locator('.network-content')).toHaveAttribute('data-page', 'tcp-server');
        await expect(page.locator('[data-sliding]')).toHaveCount(0);
    }
    expect(await page.evaluate(() => (window as any).__calls.filter((call: any[]) => call[0] === 'update_settings'))).toHaveLength(0);
});

test('rapid reversals reuse the current visual position and release each outgoing copy', async ({ page }) => {
    await ready(page);
    const group = page.getByRole('group', { name: '电脑网络角色', exact: true });
    const view = page.locator('.network-content');
    await group.getByRole('button', { name: 'UDP', exact: true }).evaluate(button => button.click());
    await expect(view).toHaveAttribute('data-sliding', 'true');
    const outgoing = await x(view.locator(':scope > .slide-outgoing'));
    await group.getByRole('button', { name: 'TCP 服务端', exact: true }).evaluate(button => button.click());
    await expect(view).toHaveAttribute('data-direction', 'backward');
    const incoming = await x(view.locator(':scope > .slide-live'));
    expect(incoming).toBeLessThanOrEqual(0);
    expect(Math.abs(incoming - outgoing)).toBeLessThan(120);
    await expect(view.locator(':scope > .slide-outgoing')).toHaveCount(1);
    expect(await view.locator(':scope > .slide-outgoing').evaluate(element => ({ inert: (element as HTMLElement).inert, shadow: element.shadowRoot }))).toEqual({ inert: true, shadow: null });
    await settle(page);
    await expect(page.locator('.slide-outgoing')).toHaveCount(0);
});

test('config input and per-page log scroll survive switching without duplicate live controls', async ({ page }) => {
    await ready(page, 260);
    await page.getByRole('button', { name: '运行日志', exact: true }).click(); await settle(page);
    await page.getByRole('button', { name: '暂停自动滚动', exact: true }).click();
    const logs = page.getByRole('log', { name: '运行日志', exact: true });
    await logs.evaluate(element => { element.scrollTop = 135; });
    await page.getByRole('button', { name: '流量监测', exact: true }).click(); await settle(page);
    await page.getByRole('button', { name: '运行日志', exact: true }).click(); await settle(page);
    expect(await logs.evaluate(element => element.scrollTop)).toBe(135);
    await page.getByRole('button', { name: '打开配置面板', exact: true }).click();
    const pane = page.getByRole('dialog', { name: '设备配置面板', exact: true });
    const tabs = pane.getByRole('group', { name: '配置分页', exact: true });
    await tabs.getByRole('button', { name: '电脑热点', exact: true }).click();
    await pane.getByLabel('SSID', { exact: true }).fill('kept-pc');
    await pane.getByLabel('新密码（留空保留）', { exact: true }).fill('test-draft-only');
    await tabs.getByRole('button', { name: '共享 Wi-Fi', exact: true }).click();
    await expect(pane.getByLabel('SSID', { exact: true })).toHaveCount(1);
    await pane.getByLabel('SSID', { exact: true }).fill('kept-shared');
    await tabs.getByRole('button', { name: '电脑热点', exact: true }).click(); await settle(page);
    await expect(pane.getByLabel('SSID', { exact: true })).toHaveValue('kept-pc');
    await expect(pane.getByLabel('新密码（留空保留）', { exact: true })).toHaveValue('test-draft-only');
    expect(await page.evaluate(() => (window as any).__calls.some((call: any[]) => call[0] === 'save_device'))).toBe(false);
});

test('content height changes keep the large optical map stable until the slide ends', async ({ page }) => {
    await ready(page);
    const panel = page.locator('.connection-panel');
    await expect(panel).toHaveAttribute('data-glass', 'refractive');
    const bitmap = () => panel.evaluate(element => {
        const id = (element as HTMLElement).style.getPropertyValue('--glass-refraction').match(/#([^"')]+)/)![1];
        return document.getElementById(id)!.querySelector('feImage')!.getAttribute('href');
    });
    const before = await bitmap();
    await page.getByRole('group', { name: '电脑网络角色' }).getByRole('button', { name: 'UDP', exact: true }).evaluate(button => button.click());
    await expect(page.locator('.network-content')).toHaveAttribute('data-sliding', 'true');
    expect(await bitmap()).toBe(before);
    await settle(page);
    await expect.poll(bitmap).not.toBe(before);
});

test('reduced motion cancels a running page transition immediately', async ({ page }) => {
    await ready(page);
    await page.getByRole('button', { name: '运行日志', exact: true }).evaluate(button => button.click());
    await expect(page.locator('.workspace-content')).toHaveAttribute('data-sliding', 'true');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await expect(page.locator('[data-sliding]')).toHaveCount(0);
    await expect(page.locator('.slide-outgoing')).toHaveCount(0);
    await page.getByRole('button', { name: '流量监测', exact: true }).click();
    await expect(page.locator('.workspace-content')).toHaveAttribute('data-page', 'traffic');
    await expect(page.locator('[data-sliding]')).toHaveCount(0);
});

for (const theme of ['light', 'dark']) for (const touch of [false, true]) {
    test(`${theme} ${touch ? 'touch' : 'mouse'} release-only page slide and clean selected rim`, async ({ browser }) => {
        const viewport = touch ? { width: 1024, height: 1366 } : { width: 1280, height: 900 };
        const preview = process.env.SLIDE_PREVIEW_DIR;
        const context = await browser.newContext({ viewport, hasTouch: touch, isMobile: touch,
            ...(preview ? { recordVideo: { dir: join(preview, 'raw'), size: viewport } } : {}) });
        const page = await context.newPage(), video = page.video();
        try {
            await ready(page, 3, theme);
            const group = page.getByRole('group', { name: '电脑网络角色', exact: true });
            const source = (await group.getByRole('button', { name: 'TCP 服务端', exact: true }).boundingBox())!;
            const target = (await group.getByRole('button', { name: 'UDP', exact: true }).boundingBox())!;
            const cdp = touch ? await context.newCDPSession(page) : null;
            const y = source.y + source.height / 2;
            if (cdp) {
                await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: source.x + source.width / 2, y }] });
                for (let i = 1; i <= 10; i++) await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: source.x + source.width / 2 + (target.x + target.width / 2 - source.x - source.width / 2) * i / 10, y }] });
            } else {
                await page.mouse.move(source.x + source.width / 2, y); await page.mouse.down();
                await page.mouse.move(target.x + target.width / 2, y, { steps: 10 });
            }
            await page.waitForTimeout(400);
            await expect(page.locator('.network-content')).toHaveAttribute('data-page', 'tcp-server');
            await expect(page.locator('[data-sliding]')).toHaveCount(0);
            if (cdp) await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); else await page.mouse.up();
            await expect(page.locator('.network-content')).toHaveAttribute('data-page', 'udp');
            await settle(page);
            const udp = group.getByRole('button', { name: 'UDP', exact: true });
            if (cdp) await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: target.x + target.width / 2, y }] });
            else { await udp.hover(); await page.mouse.down(); }
            await page.waitForTimeout(350);
            await expect(udp).toHaveCSS('outline-style', 'none');
            await page.screenshot({ path: `test-results/slide-rim-${theme}-${touch ? 'touch' : 'mouse'}.png` });
            if (cdp) await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); else await page.mouse.up();
            await group.getByRole('button', { name: 'TCP 服务端', exact: true }).click(); await settle(page);
            await page.getByRole('button', { name: '运行日志', exact: true }).click(); await settle(page);
            await page.getByRole('button', { name: 'ESP（USB）', exact: true }).click(); await settle(page);
            await page.getByRole('button', { name: '流量监测', exact: true }).click(); await settle(page);
            await page.getByRole('button', { name: '打开配置面板', exact: true }).click();
            const pane = page.getByRole('dialog', { name: '设备配置面板', exact: true });
            await pane.getByRole('button', { name: '自建热点', exact: true }).click(); await settle(page);
            await pane.getByRole('button', { name: '电脑热点', exact: true }).click(); await settle(page);
            await pane.getByRole('button', { name: '接口与联网', exact: true }).click(); await settle(page);
            await page.getByRole('button', { name: '关闭面板', exact: true }).click();
            await expect(page.locator('.slide-outgoing')).toHaveCount(0);
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        } finally { await context.close(); }
        if (preview && video) await video.saveAs(join(preview, `slide-${theme}-${touch ? 'touch' : 'mouse'}.webm`));
    });
}
