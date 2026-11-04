import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { join } from 'node:path';
import { installBridge } from './helpers';

async function ready(page: Page, url = '/', preference = 'light') {
    await installBridge(page);
    await page.addInitScript(preference => localStorage.setItem('wifi-bridge-appearance', preference), preference);
    await page.goto(url); await page.bringToFront();
    await expect(page.locator('.local-indicator')).toHaveText('本地运行');
    await page.evaluate(() => document.fonts.ready);
}
async function colour(page: Page) {
    return page.evaluate(() => {
        const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
        const context = canvas.getContext('2d')!;
        const rgb = (value: string) => { context.clearRect(0, 0, 1, 1); context.fillStyle = value; context.fillRect(0, 0, 1, 1); return [...context.getImageData(0, 0, 1, 1).data]; };
        return { body: rgb(getComputedStyle(document.body).backgroundColor), root: rgb(getComputedStyle(document.documentElement).backgroundColor) };
    });
}
async function setTheme(page: Page, theme: string) {
    await page.evaluate(theme => window.dispatchEvent(new StorageEvent('storage', { key: 'wifi-bridge-appearance', newValue: theme })), theme);
}
async function pause(page: Page, time = 150) {
    await expect(page.locator('html')).toHaveAttribute('data-theme-transition', 'true');
    await page.locator('html').evaluate((root, time) => { root.getAnimations().forEach(animation => { animation.pause(); animation.currentTime = time; }); }, time);
}
async function settled(page: Page) { await expect(page.locator('html')).not.toHaveAttribute('data-theme-transition'); }

for (const touch of [false, true]) {
    test(`${touch ? 'phone' : 'desktop'} palette fades continuously, reverses and retains live control optics`, async ({ browser }) => {
        const viewport = touch ? { width: 390, height: 844 } : { width: 1280, height: 900 };
        const directory = process.env.REVISION_PREVIEW_DIR;
        const context = await browser.newContext({ viewport, hasTouch: touch, isMobile: touch,
            ...(directory ? { recordVideo: { dir: join(directory, 'raw'), size: viewport } } : {}) });
        const page = await context.newPage(), video = page.video();
        try {
            await ready(page);
            await settled(page);
            const ids = await page.locator('.liquid-glass-defs').evaluateAll(nodes => nodes.map(node => node.outerHTML));
            const main = page.getByLabel('业务 COM', { exact: true }); await main.fill('COM66');
            await page.getByLabel('外观', { exact: true }).click();
            await page.getByRole('option', { name: '深色', exact: true }).click();
            await pause(page);
            const middle = await colour(page);
            expect(middle.body[0]).toBeGreaterThan(20); expect(middle.body[0]).toBeLessThan(220);
            expect(Math.abs(middle.root[0] - middle.body[0])).toBeLessThan(3);
            await page.screenshot({ path: `test-results/theme-mid-${touch ? 'phone' : 'desktop'}.png` });
            await page.evaluate(() => {
                window.dispatchEvent(new StorageEvent('storage', { key: 'wifi-bridge-appearance', newValue: 'light' }));
                document.documentElement.getAnimations().forEach(animation => { animation.pause(); animation.currentTime = 0; });
            });
            const reversed = await colour(page);
            expect(Math.abs(reversed.body[0] - middle.body[0])).toBeLessThan(3);
            await page.locator('html').evaluate(root => root.getAnimations().forEach(animation => animation.play()));
            await settled(page);
            expect((await colour(page)).body.slice(0, 3)).toEqual([241, 240, 234]);
            await setTheme(page, 'dark'); await settled(page);
            expect((await colour(page)).body.slice(0, 3)).toEqual([8, 8, 10]);
            await expect(main).toHaveValue('COM66');
            expect(await page.locator('.liquid-glass-defs').evaluateAll(nodes => nodes.map(node => node.outerHTML))).toEqual(ids);
        } finally { await context.close(); }
        if (directory && video) await video.saveAs(join(directory, `theme-${touch ? 'phone' : 'desktop'}.webm`));
    });
}

test('system following animates only an actual colour change and reduced motion ends travel', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'light' }); await ready(page, '/', 'system');
    await settled(page);
    await setTheme(page, 'light'); await settled(page);
    await setTheme(page, 'system');
    await expect(page.locator('html')).not.toHaveAttribute('data-theme-transition');
    await page.emulateMedia({ colorScheme: 'dark' }); await pause(page);
    await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' }); await settled(page);
    await expect.poll(async () => (await colour(page)).body.slice(0, 3)).toEqual([8, 8, 10]);
    await setTheme(page, 'light'); await settled(page);
    expect((await colour(page)).body.slice(0, 3)).toEqual([241, 240, 234]);
});

test('palette fallback still fades and removes its temporary inline colours', async ({ page }) => {
    await page.addInitScript(() => { Object.defineProperty(CSS, 'registerProperty', { value: undefined }); });
    await ready(page);
    await setTheme(page, 'dark');
    await page.waitForTimeout(120);
    const middle = (await colour(page)).body[0];
    expect(middle).toBeGreaterThan(8); expect(middle).toBeLessThan(241);
    await settled(page);
    expect((await colour(page)).body.slice(0, 3)).toEqual([8, 8, 10]);
    expect(await page.locator('html').evaluate(root => (root as HTMLElement).style.getPropertyValue('--page'))).toBe('');
});

test('phone reset retains its endpoint and in-flight status through theme and navigation changes', async ({ browser }) => {
    const directory = process.env.REVISION_PREVIEW_DIR;
    const viewport = { width: 390, height: 844 };
    const context = await browser.newContext({ viewport, hasTouch: true, isMobile: true,
        ...(directory ? { recordVideo: { dir: join(directory, 'raw'), size: viewport } } : {}) });
    const page = await context.newPage(), video = page.video();
    try {
        await ready(page);
        const open = page.getByRole('button', { name: '打开远程复位', exact: true });
        await open.click();
        const pane = page.getByRole('region', { name: '远程复位', exact: true });
        await expect(pane).toBeVisible();
        await pane.getByLabel('设备 IPv4 地址', { exact: true }).fill('192.168.50.2');
        await pane.getByLabel('控制端口', { exact: true }).fill('8089');
        await pane.getByRole('button', { name: '复位外部主控 · 0/5', exact: true }).click();
        await page.evaluate(() => { Object.assign((window as any).__bridgeState.reset, { busy: true, count: 3, message: '测试中的复位请求' }); });
        await expect(pane.getByRole('button', { name: '复位请求进行中…', exact: true })).toBeDisabled();
        await setTheme(page, 'dark'); await settled(page);
        await expect(pane.getByLabel('设备 IPv4 地址', { exact: true })).toHaveValue('192.168.50.2');
        await page.goBack(); await expect(page.locator('.drawer-backdrop')).toHaveCount(0);
        await page.goForward(); await expect(pane).toBeVisible();
        await expect(pane.getByLabel('控制端口', { exact: true })).toHaveValue('8089');
        await expect(pane.getByRole('button', { name: '复位请求进行中…', exact: true })).toBeDisabled();
        await expect(pane.locator('.reset-progress .filled')).toHaveCount(3);
        await page.screenshot({ path: 'test-results/reset-phone-dark.png' });
        await page.setViewportSize({ width: 844, height: 390 });
        await expect(pane.getByRole('button', { name: '返回工作台', exact: true })).toBeInViewport();
        await page.setViewportSize(viewport);
        await pane.getByRole('button', { name: '返回工作台', exact: true }).click();
        await expect(page.locator('.drawer-backdrop')).toHaveCount(0);
        expect(await page.evaluate(() => (window as any).__calls.filter((call: any[]) => call[0] === 'reset_click'))).toEqual([['reset_click', '192.168.50.2', '8089']]);
    } finally { await context.close(); }
    if (directory && video) await video.saveAs(join(directory, 'reset-phone.webm'));
});

test('desktop appearance persistence preserves the phone page history entry and back destination theme', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await ready(page, '/#desktop-appearance=light');
    await page.getByRole('button', { name: '打开远程复位', exact: true }).click();
    await expect(page.getByRole('region', { name: '远程复位', exact: true })).toBeVisible();
    const state = await page.evaluate(() => history.state.__wifiBridgePage);
    await page.locator('.appearance-switch select').evaluate(select => {
        (select as HTMLSelectElement).value = 'dark'; select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await settled(page);
    expect(await page.evaluate(() => history.state.__wifiBridgePage)).toEqual(state);
    expect(page.url()).toContain('desktop-appearance=dark');
    await page.goBack(); await expect(page.locator('.drawer-backdrop')).toHaveCount(0);
    expect(page.url()).toContain('desktop-appearance=dark');
    await page.reload(); await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    expect(await page.evaluate(() => (window as any).__calls.filter((call: any[]) => call[0] === 'reset_click'))).toHaveLength(0);
});
