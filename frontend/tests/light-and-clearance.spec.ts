import { expect, test } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { installBridge } from './helpers';

async function settle(page: import('@playwright/test').Page) {
    await page.evaluate(() => Promise.all(document.getAnimations().map(animation => animation.finished.catch(() => {}))));
}

function auditLens(element: Element) {
    const rect = element.getBoundingClientRect();
    const clipped: string[] = [];
    for (let ancestor = element.parentElement; ancestor; ancestor = ancestor.parentElement) {
        const style = getComputedStyle(ancestor), bounds = ancestor.getBoundingClientRect();
        if (/(hidden|clip|auto|scroll)/.test(style.overflowX) && (rect.left < bounds.left - .5 || rect.right > bounds.right + .5)) clipped.push(ancestor.className);
        if (/(hidden|clip|auto|scroll)/.test(style.overflowY) && (rect.top < bounds.top - .5 || rect.bottom > bounds.bottom + .5)) clipped.push(ancestor.className);
    }
    return { clipped, width: rect.width, natural: (element as HTMLElement).offsetWidth };
}

for (const touch of [false, true]) test(`${touch ? 'phone' : 'desktop'} static IP, reconnect, debug and LAN controls clear clipping edges at both endpoints`, async ({ browser }) => {
    const directory = process.env.LIGHT_CLEARANCE_PREVIEW_DIR;
    if (directory) await mkdir(directory, { recursive: true });
    const viewport = touch ? { width: 390, height: 844 } : { width: 1440, height: 1000 };
    const context = await browser.newContext({ viewport, hasTouch: touch, isMobile: touch,
        ...(directory ? { recordVideo: { dir: join(directory, 'raw'), size: viewport } } : {}) });
    const page = await context.newPage(), video = page.video();
    try {
        await installBridge(page); await page.goto('/'); await page.bringToFront();
        await page.getByRole('button', { name: '打开配置面板', exact: true }).click();
        const tab = page.getByRole('group', { name: '配置分页', exact: true });
        for (const name of ['电脑热点', '共享 Wi-Fi', '接口与联网']) {
            await tab.getByRole('button', { name, exact: true }).click(); await settle(page);
            const input = page.getByRole('switch', { name: name === '接口与联网' ? '设备自动重连' : '使用静态 IP（关闭时使用 DHCP）', exact: true });
            if (await input.isChecked()) { await input.focus(); await page.keyboard.press('Space'); }
            const track = input.locator('..').locator('.liquid-toggle-track');
            await track.scrollIntoViewIfNeeded();
            const box = (await track.boundingBox())!;
            const x = box.x + 15, y = box.y + box.height / 2;
            await page.mouse.move(x, y); await page.mouse.down();
            await page.mouse.move(x - 35, y, { steps: 5 });
            await expect(track.locator('[data-lens-active]')).toHaveCount(1);
            const check = async () => track.locator('.liquid-toggle-thumb').evaluate(auditLens);
            await expect.poll(async () => (await check()).width).toBeGreaterThan(touch ? 46 : 37);
            expect((await check()).clipped).toEqual([]);
            if (directory) await page.screenshot({ path: join(directory, `clearance-${touch ? 'phone' : 'desktop'}-${name}-left.png`) });
            await page.mouse.move(x + 60, y, { steps: 7 });
            expect((await check()).clipped).toEqual([]);
            await page.mouse.up(); await expect(input).toBeChecked();
            await input.focus(); await page.keyboard.press('Space'); await expect(input).not.toBeChecked();
        }
        await page.getByRole('button', { name: touch ? '返回工作台' : '关闭面板', exact: true }).click();
        await expect(page.locator('.drawer-backdrop')).toHaveCount(0);
        await page.getByRole('button', { name: '高级参数', exact: true }).click(); await settle(page);
        const debug = page.getByRole('switch', { name: '调试日志', exact: true }).locator('..').locator('.liquid-toggle-track');
        await debug.scrollIntoViewIfNeeded(); const debugBox = (await debug.boundingBox())!;
        await page.mouse.move(debugBox.x + 15, debugBox.y + debugBox.height / 2); await page.mouse.down();
        await page.waitForTimeout(220);
        expect((await debug.locator('.liquid-toggle-thumb').evaluate(auditLens)).clipped).toEqual([]);
        await page.mouse.up();
        await page.getByRole('button', { name: '局域网连接', exact: true }).click();
        await expect(page.locator('.lan-panel')).toHaveAttribute('data-state', 'open'); await settle(page);
        const button = page.getByRole('button', { name: '连接服务 / 重试', exact: true });
        await button.scrollIntoViewIfNeeded(); const buttonBox = (await button.boundingBox())!;
        await page.mouse.move(buttonBox.x + buttonBox.width / 2, buttonBox.y + buttonBox.height / 2); await page.mouse.down();
        await page.waitForTimeout(220);
        expect((await button.locator('.liquid-button-material').evaluate(auditLens)).clipped).toEqual([]);
        await page.keyboard.press('Escape'); await page.mouse.up();
    } finally { await context.close(); }
    if (directory && video) await video.saveAs(join(directory, `clearance-${touch ? 'phone' : 'desktop'}.webm`));
});

for (const theme of ['light', 'dark']) test(`${theme} card light enters, returns and reverses continuously without rebuilding optics`, async ({ page }) => {
    await page.addInitScript(theme => localStorage.setItem('wifi-bridge-appearance', theme), theme);
    await installBridge(page); await page.goto('/'); await page.bringToFront(); await settle(page);
    for (const selector of ['.connection-panel', '.lan-panel']) {
        if (selector === '.lan-panel') {
            await page.getByRole('button', { name: '局域网连接', exact: true }).click();
            await expect(page.locator(selector)).toHaveAttribute('data-state', 'open'); await settle(page);
        }
        const surface = page.locator(selector);
        const trace = await surface.evaluate(async node => {
            const element = node as HTMLElement, bounds = element.getBoundingClientRect();
            const point = () => [parseFloat(element.style.getPropertyValue('--glass-light-x')), parseFloat(element.style.getPropertyValue('--glass-light-y'))];
            const map = element.style.getPropertyValue('--glass-refraction');
            const id = map.match(/#([^"')]+)/)?.[1], image = id ? document.getElementById(id)?.querySelector('feImage') : null;
            const href = image?.getAttribute('href');
            element.dispatchEvent(new PointerEvent('pointerleave'));
            await new Promise(resolve => setTimeout(resolve, 650));
            const home = point();
            const target = [element.offsetWidth * .86, element.offsetHeight * .65];
            element.dispatchEvent(new PointerEvent('pointerenter', { pointerType: 'mouse', clientX: bounds.x + target[0], clientY: bounds.y + target[1] }));
            const immediate = point(), entering: number[][] = [];
            const end = performance.now() + 500;
            while (performance.now() < end) { await new Promise(requestAnimationFrame); entering.push(point()); }
            element.dispatchEvent(new PointerEvent('pointerleave'));
            const exitImmediate = point();
            await new Promise(resolve => setTimeout(resolve, 90));
            const returning = point();
            element.dispatchEvent(new PointerEvent('pointerenter', { pointerType: 'mouse', clientX: bounds.x + target[0], clientY: bounds.y + target[1] }));
            const reversal = point();
            await new Promise(resolve => setTimeout(resolve, 130));
            const backToPointer = point();
            element.dispatchEvent(new PointerEvent('pointerleave'));
            await new Promise(resolve => setTimeout(resolve, 750));
            return { home, target, immediate, entering, exitImmediate, returning, reversal, backToPointer, final: point(), stable: image?.getAttribute('href') === href };
        });
        const distance = (a: number[], b: number[]) => Math.hypot(a[0] - b[0], a[1] - b[1]);
        expect(trace.immediate).toEqual(trace.home);
        expect(distance(trace.entering[0], trace.target)).toBeGreaterThan(10);
        expect(distance(trace.entering.at(-1)!, trace.target)).toBeLessThan(1);
        expect(distance(trace.exitImmediate, trace.target)).toBeLessThan(1);
        expect(distance(trace.returning, trace.home)).toBeGreaterThan(5);
        expect(distance(trace.returning, trace.target)).toBeGreaterThan(5);
        expect(trace.reversal).toEqual(trace.returning);
        expect(distance(trace.backToPointer, trace.target)).toBeLessThan(distance(trace.returning, trace.target));
        expect(distance(trace.final, trace.home)).toBeLessThan(.2);
        expect(trace.stable).toBe(true);
    }
});

test('reduced motion restores the resting light during travel', async ({ page }) => {
    await installBridge(page); await page.goto('/'); await page.bringToFront();
    const card = page.locator('.connection-panel'); await expect(card).toBeVisible();
    await card.dispatchEvent('pointerenter', { pointerType: 'mouse', clientX: 150, clientY: 300 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    expect(await card.evaluate(element => Math.abs(parseFloat((element as HTMLElement).style.getPropertyValue('--glass-light-x')) - (element as HTMLElement).offsetWidth / 2))).toBeLessThan(.2);
});

test('touch button contact lights its touch point and cancellation returns home without a click', async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    const page = await context.newPage();
    try {
        await installBridge(page); await page.goto('/'); await page.bringToFront();
        const button = page.getByRole('button', { name: '启动桥接', exact: true });
        await button.scrollIntoViewIfNeeded();
        const material = button.locator('.liquid-button-material'), box = (await button.boundingBox())!;
        const resting = await material.evaluate(element => (element as HTMLElement).offsetWidth * .4);
        const cdp = await context.newCDPSession(page);
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: box.x + box.width * .8, y: box.y + box.height / 2 }] });
        await expect.poll(() => material.evaluate(element => parseFloat((element as HTMLElement).style.getPropertyValue('--glass-light-x')))).toBeGreaterThan(resting + 10);
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchCancel', touchPoints: [] });
        await expect.poll(async () => Math.abs(await material.evaluate(element => parseFloat((element as HTMLElement).style.getPropertyValue('--glass-light-x'))) - resting)).toBeLessThan(.2);
        expect(await page.evaluate(() => (window as any).__calls.some((call: any[]) => call[0] === 'start'))).toBe(false);
    } finally { await context.close(); }
});
