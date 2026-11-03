import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { installBridge } from './helpers';

async function ready(page: Page, preference?: string, url = '/') {
    await installBridge(page);
    if (preference) await page.addInitScript(value => localStorage.setItem('wifi-bridge-appearance', value), preference);
    await page.goto(url);
    await expect(page.getByText('本地运行', { exact: true })).toHaveCount(1);
    await page.bringToFront();
    await settle(page);
}
async function settle(page: Page) {
    await page.evaluate(() => Promise.all(document.getAnimations().map(animation => animation.finished.catch(() => {}))));
}
async function choose(page: Page, name: string) {
    await page.getByLabel('外观', { exact: true }).click();
    await page.getByRole('listbox', { name: '外观选项', exact: true }).getByRole('option', { name, exact: true }).click();
    await expect(page.locator('.picker-position')).toHaveCount(0);
}

test('first visit restores original warm colours even on a dark operating system', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'dark' });
    await ready(page);
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(241, 240, 234)');
    await expect(page.locator('body')).toHaveCSS('background-image', 'none');
    await expect(page.getByRole('button', { name: '启动桥接', exact: true })).toHaveCSS('background-color', 'rgb(41, 42, 37)');
    await page.screenshot({ path: 'test-results/appearance-original-light.png' });
});

test('explicit choice survives reload, stays client-local and overrides system changes', async ({ browser }) => {
    const a = await browser.newContext(), b = await browser.newContext();
    try {
        const first = await a.newPage(), second = await b.newPage();
        await ready(first); await ready(second);
        await choose(first, '深色');
        await expect(first.locator('html')).toHaveAttribute('data-theme', 'dark');
        await expect(second.locator('html')).toHaveAttribute('data-theme', 'light');
        expect(await first.evaluate(() => (window as any).__calls.filter((call: any[]) => ['update_settings', 'save_device', 'set_appearance'].includes(call[0])))).toHaveLength(0);
        await first.reload();
        await expect(first.getByLabel('外观', { exact: true })).toHaveValue('dark');
        await first.emulateMedia({ colorScheme: 'light' });
        await expect(first.locator('html')).toHaveAttribute('data-theme', 'dark');
        await choose(first, '跟随系统');
        await expect(first.locator('html')).toHaveAttribute('data-theme', 'light');
        await first.emulateMedia({ colorScheme: 'dark' });
        await expect(first.locator('html')).toHaveAttribute('data-theme', 'dark');
        await first.reload();
        await expect(first.getByLabel('外观', { exact: true })).toHaveValue('system');
        await expect(first.locator('html')).toHaveAttribute('data-theme', 'dark');
    } finally { await a.close(); await b.close(); }
});

for (const desktop of [false, true]) test(`dark first paint precedes React (${desktop ? 'desktop fragment' : 'browser preference'})`, async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('wifi-bridge-appearance', 'dark'));
    await page.route('**/assets/*.js', route => route.abort());
    await page.goto(desktop ? '/#desktop-appearance=dark' : '/');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await expect(page.locator('html')).toHaveCSS('background-color', 'rgb(8, 8, 10)');
    await expect(page.locator('#root')).toBeEmpty();
    expect(await page.locator('meta[name="theme-color"]').getAttribute('content')).toBe('rgb(8, 8, 10)');
});

test('desktop bootstrap takes priority and native writes never use business settings', async ({ page }) => {
    await ready(page, 'light', '/#desktop-appearance=dark');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await choose(page, '原始浅色'); await choose(page, '跟随系统');
    await expect.poll(() => page.evaluate(() => (window as any).__calls.filter((call: any[]) => call[0] === 'set_appearance').map((call: any[]) => call[1]))).toEqual(['light', 'system']);
    expect(await page.evaluate(() => (window as any).__calls.filter((call: any[]) => ['update_settings', 'save_device'].includes(call[0])))).toHaveLength(0);
    expect(page.url()).toContain('desktop-appearance=system');
    await page.reload(); await expect(page.getByLabel('外观', { exact: true })).toHaveValue('system');
});

test('unavailable storage keeps the chosen appearance usable and explains the save failure', async ({ page }) => {
    await page.addInitScript(() => {
        Storage.prototype.getItem = () => { throw new Error('blocked'); };
        Storage.prototype.setItem = () => { throw new Error('blocked'); };
    });
    await ready(page); await choose(page, '深色');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await expect(page.getByText('外观已切换，但未能保存；重启后可能恢复原设置。', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: '打开配置面板', exact: true }).click();
    await expect(page.getByRole('dialog', { name: '设备配置面板', exact: true })).toBeVisible();
});

test('system changes recolour retained portals without remounting optics or moving the pane', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'light' }); await ready(page, 'system');
    await page.getByRole('button', { name: '打开配置面板', exact: true }).click();
    const pane = page.getByRole('dialog', { name: '设备配置面板', exact: true });
    await pane.getByLabel('MCU 接口', { exact: true }).click();
    await expect(page.locator('.picker-position')).toHaveAttribute('data-state', 'open');
    await settle(page);
    const bounds = await pane.boundingBox();
    const ids = await page.locator('.liquid-glass-defs filter').evaluateAll(elements => elements.map(element => element.id));
    await page.emulateMedia({ colorScheme: 'dark' });
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await settle(page);
    expect(await pane.boundingBox()).toEqual(bounds);
    expect(await page.locator('.liquid-glass-defs filter').evaluateAll(elements => elements.map(element => element.id))).toEqual(ids);
    const colours = await page.locator('.picker-list > button').first().evaluate(element => ({
        colour: getComputedStyle(element).color,
        background: getComputedStyle(element.closest('.picker-window')!, '::before').backgroundImage,
    }));
    expect(colours.colour).toBe('rgb(223, 204, 255)'); // Selected row uses the brighter accent.
    await expect(page.locator('.picker-list > button').nth(1)).toHaveCSS('color', 'rgb(240, 237, 246)');
    expect(colours.background).toContain('28, 22, 36');
    await page.screenshot({ path: 'test-results/appearance-dark-config-menu.png' });
    await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
    await expect(pane).toHaveCount(0);
    await page.getByRole('button', { name: '局域网连接', exact: true }).click();
    await expect(page.locator('.lan-panel')).toHaveAttribute('data-state', 'open'); await settle(page);
    await page.screenshot({ path: 'test-results/appearance-dark-lan.png' });
});

for (const [width, height, touch] of [[1440, 1000, false], [1024, 1366, true], [1366, 1024, true], [390, 844, true]] as const) {
    test(`dark appearance stays readable and interactive at ${width}px`, async ({ browser }) => {
        const context = await browser.newContext({ viewport: { width, height }, hasTouch: touch, isMobile: touch });
        try {
            const page = await context.newPage(); await ready(page, 'dark');
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
            await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(8, 8, 10)');
            await expect(page.getByLabel('业务 COM', { exact: true })).toHaveCSS('color', 'rgb(240, 237, 246)');
            await page.screenshot({ path: `test-results/appearance-dark-${width}.png` });
            const group = page.getByRole('group', { name: '电脑网络角色', exact: true });
            await expect(group.locator('.liquid-indicator')).toHaveAttribute('data-glass', 'refractive');
            await group.getByRole('button', { name: 'TCP 服务端', exact: true }).focus();
            await page.keyboard.press('End'); await expect(group.getByRole('button', { name: 'UDP', exact: true })).toHaveAttribute('aria-pressed', 'true');
            await page.getByRole('button', { name: '选择业务 COM', exact: true }).click();
            await page.getByRole('option', { name: 'COM88 · USB Serial', exact: true }).click();
            await expect(page.getByLabel('业务 COM', { exact: true })).toHaveValue('COM88');
            await page.getByRole('button', { name: '打开远程复位', exact: true }).click();
            await settle(page); await page.screenshot({ path: `test-results/appearance-dark-reset-${width}.png` });
            await page.getByRole('button', { name: '关闭面板', exact: true }).click();
            await expect(page.locator('.drawer-backdrop')).toHaveCount(0);
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        } finally { await context.close(); }
    });
}
