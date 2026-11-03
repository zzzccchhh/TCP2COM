import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { installBridge } from './helpers';

async function ready(page: Page) {
    await installBridge(page);
    await page.goto('/');
    await expect(page.getByText('本地运行', { exact: true })).toHaveCount(1);
    await page.bringToFront();
    await settle(page);
}
async function settle(page: Page) {
    await page.evaluate(() => Promise.all(document.getAnimations().map(animation => animation.finished.catch(() => {}))));
}

for (const fraction of [.12, .5, .88]) {
    test(`click at ${fraction} goes straight to the option and stays there while held`, async ({ page }) => {
        await ready(page);
        const group = page.getByRole('group', { name: '电脑网络角色', exact: true });
        const target = group.getByRole('button', { name: 'UDP', exact: true });
        const anchor = group.locator('.liquid-anchor');
        const box = (await target.boundingBox())!;
        const start = (await anchor.boundingBox())!;
        await group.evaluate(element => {
            (window as any).__lensPath = [];
            const start = performance.now();
            function frame() {
                const rect = element.querySelector('.liquid-anchor')!.getBoundingClientRect();
                (window as any).__lensPath.push(rect.x + rect.width / 2);
                if (performance.now() - start < 500) requestAnimationFrame(frame);
            }
            requestAnimationFrame(frame);
        });
        await page.mouse.move(box.x + box.width * fraction, box.y + box.height / 2);
        await page.mouse.down();
        await expect(group).toHaveAttribute('data-pressed', 'true');
        await expect(group).not.toHaveAttribute('data-dragging', 'true');
        await settle(page);
        const held = (await anchor.boundingBox())!;
        expect(Math.abs(held.x - box.x)).toBeLessThan(.75);
        expect(Math.abs(held.width - box.width)).toBeLessThan(.75);
        expect(await page.evaluate(() => (window as any).__calls.filter((call: any[]) => call[0] === 'update_settings'))).toHaveLength(0);
        await page.mouse.up();
        await expect(target).toHaveAttribute('aria-pressed', 'true');
        await settle(page);
        expect(await anchor.boundingBox()).toEqual(held);
        const path: number[] = await page.evaluate(() => (window as any).__lensPath);
        expect(path.length).toBeGreaterThan(3);
        expect(Math.min(...path)).toBeGreaterThanOrEqual(start.x + start.width / 2 - .75);
        expect(Math.max(...path)).toBeLessThanOrEqual(box.x + box.width / 2 + .75);
        expect(path.every((x, i) => !i || x >= path[i - 1] - .75), 'click travel must not reverse or overshoot').toBe(true);
        expect(await page.evaluate(() => (window as any).__calls.filter((call: any[]) => call[0] === 'update_settings'))).toHaveLength(1);
    });
}

test('small pointer jitter stays a click and dragging preserves the grab offset', async ({ page }) => {
    await ready(page);
    const group = page.getByRole('group', { name: '电脑网络角色', exact: true });
    const selected = group.getByRole('button', { name: 'TCP 服务端', exact: true });
    const box = (await selected.boundingBox())!;
    await page.mouse.move(box.x + 8, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + 11, box.y + box.height / 2);
    await expect(group).not.toHaveAttribute('data-dragging', 'true');
    expect(Math.abs((await group.locator('.liquid-anchor').boundingBox())!.x - box.x)).toBeLessThan(.75);
    await page.mouse.move(box.x + 33, box.y + box.height / 2);
    await expect(group).toHaveAttribute('data-dragging', 'true');
    expect(Math.abs((await group.locator('.liquid-anchor').boundingBox())!.x - box.x - 25)).toBeLessThan(.75);
    await page.keyboard.press('Escape');
    await page.mouse.up();
    await expect(selected).toHaveAttribute('aria-pressed', 'true');
    expect(await page.evaluate(() => (window as any).__calls.filter((call: any[]) => call[0] === 'update_settings'))).toHaveLength(0);
});

test('droplet reverses with velocity, relaxes in place and reuses the same optical bitmap', async ({ page }) => {
    await ready(page);
    const group = page.getByRole('group', { name: '电脑网络角色', exact: true });
    const lens = group.locator('.liquid-indicator');
    await expect(lens).toHaveAttribute('data-glass', 'refractive');
    const optics = () => lens.evaluate(element => {
        const id = (element as HTMLElement).style.getPropertyValue('--glass-refraction').match(/#([^"')]+)/)![1];
        return { id, bitmap: document.getElementById(id)!.querySelector('feImage')!.getAttribute('href') };
    });
    const original = await optics();
    const defs = await page.locator('.liquid-glass-defs').count();
    const box = (await group.getByRole('button', { name: 'TCP 服务端', exact: true }).boundingBox())!;
    const x = box.x + box.width / 2, y = box.y + box.height / 2;
    await page.mouse.move(x, y); await page.mouse.down();
    for (let step = 1; step <= 7; step++) {
        await page.mouse.move(x + step * 10, y);
        await page.waitForTimeout(16);
    }
    const bend = () => lens.evaluate(element => parseFloat((element as HTMLElement).style.getPropertyValue('--liquid-bend')));
    await expect.poll(bend).toBeGreaterThan(.04);
    const shape = await lens.evaluate(element => {
        const style = getComputedStyle(element);
        return [style.borderTopLeftRadius, style.borderTopRightRadius];
    });
    expect(shape[0]).not.toBe(shape[1]);
    expect(await optics()).toEqual(original);
    await page.screenshot({ path: 'test-results/liquid-droplet-drag.png' });
    for (let step = 1; step <= 11; step++) {
        await page.mouse.move(x + 70 - step * 10, y);
        await page.waitForTimeout(16);
    }
    await expect.poll(bend).toBeLessThan(-.04);
    expect(await optics()).toEqual(original);
    await page.mouse.up();
    await settle(page);
    const anchor = group.locator('.liquid-anchor');
    const resting = await anchor.boundingBox();
    await expect.poll(async () => Math.abs(await bend())).toBeLessThan(.002);
    expect(await anchor.boundingBox()).toEqual(resting);
    await expect(page.locator('.liquid-glass-defs')).toHaveCount(defs);
});

for (const [width, height, touch] of [[1440, 1000, false], [1024, 1366, true], [1366, 1024, true], [390, 844, true]] as const) {
    test(`larger controls fit ${width}px ${touch ? 'touch' : 'desktop'} and keep panels reachable`, async ({ browser }) => {
        const context = await browser.newContext({ viewport: { width, height }, hasTouch: touch, isMobile: touch });
        try {
            const page = await context.newPage(); await ready(page);
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
            const input = page.getByLabel('业务 COM', { exact: true });
            expect((await input.boundingBox())!.height).toBeGreaterThanOrEqual(touch ? 48 : 42);
            expect(await input.evaluate(element => parseFloat(getComputedStyle(element).fontSize))).toBeGreaterThanOrEqual(touch ? 16 : 14);
            const segments = page.getByRole('group', { name: '电脑网络角色', exact: true });
            for (const button of await segments.getByRole('button').all()) expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(touch ? 48 : 42);
            const config = page.getByRole('button', { name: '打开配置面板', exact: true });
            if (width >= 1024) {
                await expect(config).toBeInViewport();
                await expect(page.getByRole('button', { name: '启动桥接', exact: true })).toBeInViewport();
            }
            await page.screenshot({ path: `test-results/enlarged-workspace-${width}.png` });
            await config.click();
            await expect(page.getByRole('dialog', { name: '设备配置面板', exact: true })).toBeVisible();
            await page.getByRole('button', { name: '关闭面板', exact: true }).click();
            await expect(page.locator('.drawer-backdrop')).toHaveCount(0);
            const metrics = page.getByLabel('桥接统计', { exact: true });
            await metrics.scrollIntoViewIfNeeded(); await expect(metrics).toBeInViewport();
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        } finally { await context.close(); }
    });
}
