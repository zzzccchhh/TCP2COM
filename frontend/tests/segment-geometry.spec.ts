import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { installBridge } from './helpers';

async function settle(page: Page) {
    await page.evaluate(() => Promise.all(document.getAnimations().map(animation => animation.finished.catch(() => {}))));
}
async function logs(page: Page) {
    await page.bringToFront();
    await expect(page.getByText('本地运行', { exact: true })).toHaveCount(1);
    await page.getByRole('button', { name: '运行日志', exact: true }).click(); await settle(page);
}
async function record(page: Page) {
    await page.getByRole('group', { name: '日志来源', exact: true }).evaluate(group => {
        const anchor = group.querySelector('.liquid-anchor')!;
        const frames: { x: number; width: number; height: number }[] = [];
        const start = performance.now();
        (window as any).__geometry = frames;
        const sample = () => {
            const rect = anchor.getBoundingClientRect();
            frames.push({ x: rect.x, width: rect.width, height: rect.height });
            if (performance.now() - start < 480) requestAnimationFrame(sample);
        };
        sample();
    });
}

for (const touch of [false, true]) {
    test(`${touch ? 'phone' : 'desktop'} unequal log options interpolate dimensions in both directions`, async ({ browser }) => {
        const directory = process.env.BUTTON_GEOMETRY_PREVIEW_DIR;
        if (directory) await mkdir(directory, { recursive: true });
        const viewport = touch ? { width: 390, height: 844 } : { width: 1440, height: 1000 };
        const context = await browser.newContext({ viewport, hasTouch: touch, isMobile: touch,
            ...(directory ? { recordVideo: { dir: join(directory, 'raw'), size: viewport } } : {}) });
        const page = await context.newPage(), video = page.video();
        try {
            await installBridge(page); await page.goto('/'); await logs(page);
            const group = page.getByRole('group', { name: '日志来源', exact: true });
            await group.scrollIntoViewIfNeeded(); await settle(page);
            for (const name of ['ESP（USB）', '上位机', 'ESP（USB）', '全部']) {
                const button = group.getByRole('button', { name: name === '全部' ? /^全部/ : name, exact: name !== '全部' });
                const box = (await button.boundingBox())!;
                const before = (await group.locator('.liquid-anchor').boundingBox())!;
                await record(page);
                await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2); await page.mouse.down();
                await page.waitForTimeout(500);
                const frames: { x: number; width: number }[] = await page.evaluate(() => (window as any).__geometry);
                const delta = box.width - before.width;
                if (Math.abs(delta) > 5) {
                    expect(frames.filter(frame => frame.width > Math.min(before.width, box.width) + 1 && frame.width < Math.max(before.width, box.width) - 1).length).toBeGreaterThan(2);
                    expect(Math.max(...frames.slice(1).map((frame, i) => Math.abs(frame.width - frames[i].width)))).toBeLessThan(Math.max(12, Math.abs(delta) * .65));
                }
                await page.mouse.up(); await expect(button).toHaveAttribute('aria-pressed', 'true'); await settle(page);
                const end = (await group.locator('.liquid-anchor').boundingBox())!, target = (await button.boundingBox())!;
                expect(Math.abs(end.x - target.x)).toBeLessThan(.75); expect(Math.abs(end.width - target.width)).toBeLessThan(.75);
            }
        } finally { await context.close(); if (directory && video) await video.saveAs(join(directory, `segments-${touch ? 'phone' : 'desktop'}.webm`)); }
    });
}

test('reversing and regrabbing an unequal-size transition preserves the visible box and updates optics once per allocation', async ({ page }) => {
    await installBridge(page); await page.goto('/'); await logs(page);
    const group = page.getByRole('group', { name: '日志来源', exact: true });
    const esp = (await group.getByRole('button', { name: 'ESP（USB）', exact: true }).boundingBox())!;
    await group.evaluate(group => {
        const lens = group.querySelector<HTMLElement>('.liquid-indicator')!;
        const id = lens.style.getPropertyValue('--glass-refraction').match(/#([^"')]+)/)![1];
        (window as any).__maps = 0;
        new MutationObserver(records => { (window as any).__maps += records.length; })
            .observe(document.getElementById(id)!.querySelector('feImage')!, { attributes: true, attributeFilter: ['href'] });
    });
    await page.mouse.move(esp.x + esp.width / 2, esp.y + esp.height / 2); await page.mouse.down();
    await page.waitForTimeout(65);
    const anchor = group.locator('.liquid-anchor');
    // Sample around the actual event, excluding automation/network time while the box keeps animating.
    await group.evaluate(group => {
        group.addEventListener('keydown', () => {
            const anchor = group.querySelector('.liquid-anchor')!;
            const before = anchor.getBoundingClientRect().width;
            requestAnimationFrame(() => { (window as any).__reversalWidths = [before, anchor.getBoundingClientRect().width]; });
        }, { capture: true, once: true });
    });
    await page.keyboard.press('Escape'); await page.mouse.up();
    const widths = await page.evaluate(async () => {
        await new Promise(requestAnimationFrame); return (window as any).__reversalWidths as number[];
    });
    expect(Math.abs(widths[1] - widths[0])).toBeLessThan(12);
    await settle(page);
    expect(await page.evaluate(() => (window as any).__maps)).toBeLessThanOrEqual(3);
    const all = group.getByRole('button', { name: /^全部/ });
    await expect(all).toHaveAttribute('aria-pressed', 'true');
    const from = (await all.boundingBox())!;
    await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2); await page.mouse.down();
    await page.mouse.move(esp.x + esp.width / 2, esp.y + esp.height / 2, { steps: 6 });
    const dragged = (await anchor.boundingBox())!;
    await page.mouse.up();
    const released = (await anchor.boundingBox())!;
    expect(Math.abs(released.width - dragged.width)).toBeLessThan(12);
    await page.waitForTimeout(35);
    await page.mouse.down();
    const regrabbed = (await anchor.boundingBox())!;
    await page.mouse.move(esp.x + esp.width / 2 + 8, esp.y + esp.height / 2);
    const following = (await anchor.boundingBox())!;
    expect(Math.abs(following.width - regrabbed.width)).toBeLessThan(8);
    expect(Math.abs(following.x - regrabbed.x - 8)).toBeLessThan(14);
    await page.keyboard.press('Escape'); await page.mouse.up();
    await settle(page);
    await expect(group.getByRole('button', { name: 'ESP（USB）', exact: true })).toHaveAttribute('aria-pressed', 'true');
});

test('keyboard size transitions and reduced motion land on the selected log option', async ({ page }) => {
    await installBridge(page); await page.goto('/'); await logs(page);
    const group = page.getByRole('group', { name: '日志来源', exact: true });
    await group.getByRole('button', { name: /^全部/ }).focus();
    await record(page); await page.keyboard.press('End'); await page.waitForTimeout(500);
    const frames: { width: number }[] = await page.evaluate(() => (window as any).__geometry);
    expect(frames.filter(frame => frame.width > frames[0].width + 1 && frame.width < frames.at(-1)!.width - 1).length).toBeGreaterThan(2);
    await page.emulateMedia({ reducedMotion: 'reduce' }); await page.keyboard.press('Home');
    const end = (await group.locator('.liquid-anchor').boundingBox())!, button = (await group.getByRole('button', { name: /^全部/ }).boundingBox())!;
    expect(Math.abs(end.x - button.x) + Math.abs(end.width - button.width)).toBeLessThan(1);
});

test('live log counter resize does not cancel a held selection', async ({ page }) => {
    await installBridge(page, 9); await page.goto('/'); await logs(page);
    const group = page.getByRole('group', { name: '日志来源', exact: true });
    const esp = group.getByRole('button', { name: 'ESP（USB）', exact: true });
    await esp.hover(); await page.mouse.down();
    await page.evaluate(() => {
        const state = (window as any).__bridgeState;
        state.cursor += 1;
        state.logs = [{ id: state.cursor, time: '12:00:03', source: 'host', level: '', text: 'counter resize' }];
    });
    await expect(group.getByRole('button', { name: /^全部/ })).toContainText('10');
    await expect(group).toHaveAttribute('data-pressed', 'true');
    await page.mouse.up(); await expect(esp).toHaveAttribute('aria-pressed', 'true');
});
