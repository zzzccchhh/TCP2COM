import { expect, test } from '@playwright/test';
import type { Browser, Page } from '@playwright/test';
import type { Buffer } from 'node:buffer';
import { installBridge } from './helpers';

async function tablet(browser: Browser, width = 1024, height = 1366) {
    const context = await browser.newContext({ viewport: { width, height }, hasTouch: true, isMobile: true });
    const page = await context.newPage();
    await installBridge(page);
    await page.goto('/');
    await expect(page.getByText('本地运行', { exact: true })).toHaveCount(1);
    await page.bringToFront();
    await settle(page);
    return { context, page };
}

async function settle(page: Page) {
    await page.evaluate(async () => {
        await document.fonts.ready;
        await Promise.all(document.getAnimations().map(animation => animation.finished.catch(() => {})));
    });
}

async function imageDistances(page: Page, samples: Buffer[], reference: Buffer) {
    return page.evaluate(async images => {
        const pixels = await Promise.all(images.map(async base64 => {
            const image = new Image(); image.src = `data:image/png;base64,${base64}`; await image.decode();
            const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
            const ctx = canvas.getContext('2d')!; ctx.drawImage(image, 0, 0);
            return ctx.getImageData(0, 0, image.width, image.height).data;
        }));
        const target = pixels.at(-1)!;
        return pixels.slice(0, -1).map(data => {
            let max = 0, sum = 0;
            for (let i = 0; i < data.length; i += 4) for (let channel = 0; channel < 3; channel++) {
                const difference = Math.abs(data[i + channel] - target[i + channel]);
                max = Math.max(max, difference); sum += difference;
            }
            return { max, mean: sum / (data.length / 4 * 3) };
        });
    }, [...samples, reference].map(buffer => buffer.toString('base64')));
}

for (const [width, height] of [[1024, 1366], [1366, 1024]]) {
    test(`tablet menu keeps unrelated content and optical resources stable at ${width}px`, async ({ browser }) => {
        const { context, page } = await tablet(browser, width, height);
        try {
            expect(await page.evaluate(() => matchMedia('(pointer: coarse)').matches)).toBe(true);
            expect(await page.locator('body').evaluate(element => getComputedStyle(element).backgroundAttachment.split(',').every(value => value.trim() === 'scroll'))).toBe(true);
            const box = (await page.locator('.terminal-panel').boundingBox())!;
            const clip = { x: box.x + 20, y: box.y + 20, width: 180, height: 110 };
            const baseline = await page.screenshot({ clip });
            const filters = await page.locator('.liquid-glass-defs').count();
            await page.getByRole('button', { name: '选择波特率 / bps', exact: true }).tap();
            const popup = page.locator('.picker-position');
            await expect(popup).toHaveAttribute('data-state', 'open');
            const early = await page.screenshot({ clip });
            await expect(page.locator('.picker-highlight')).toHaveAttribute('data-glass', 'frosted');
            await expect(page.locator('.liquid-glass-defs')).toHaveCount(filters);
            expect(await popup.locator('.picker-window').evaluate(element => getComputedStyle(element, '::before').transform)).toBe('none');
            await settle(page);
            const late = await page.screenshot({ clip });
            await page.screenshot({ path: `test-results/tablet-menu-${width}.png` });
            await page.keyboard.press('Escape');
            await expect(popup).toHaveCount(0);
            const after = await page.screenshot({ clip });
            const changes = await imageDistances(page, [early, late, after], baseline);
            expect(changes.every(change => change.max < 8), 'opening a menu must not flash the traffic panel outside it').toBe(true);
            await expect(page.getByRole('group', { name: '电脑网络角色' }).locator('.liquid-indicator')).toHaveAttribute('data-glass', 'refractive');
        } finally { await context.close(); }
    });
}

test('tablet configuration and reset use a fixed blur mask and a stationary pane', async ({ browser }) => {
    const { context, page } = await tablet(browser);
    try {
        for (const title of ['打开配置面板', '打开远程复位']) {
            const box = (await page.locator('.connection-panel').boundingBox())!;
            const clip = { x: box.x + 20, y: box.y + 25, width: 165, height: 120 };
            const initial = await page.screenshot({ clip });
            const layout = await page.evaluate(() => [scrollX, scrollY, document.documentElement.clientWidth]);
            await page.getByRole('button', { name: title, exact: true }).tap();
            const overlay = page.locator('.drawer-backdrop');
            const pane = overlay.locator('.drawer');
            await expect(overlay).toHaveAttribute('data-state', 'open');
            await expect(overlay).toHaveCSS('backdrop-filter', 'none');
            await expect(overlay.locator('.overlay-shade')).toHaveCSS('backdrop-filter', 'blur(6px)');
            const sampling = await pane.evaluate(element => ({
                scale: new DOMMatrixReadOnly(getComputedStyle(element).transform).d,
                backdrop: getComputedStyle(element, '::before').backdropFilter,
            }));
            expect(sampling.scale).toBe(1);
            expect(sampling.backdrop).toBe('none');
            const early = await page.screenshot({ clip });
            await settle(page);
            const final = await page.screenshot({ clip });
            const distances = await imageDistances(page, [initial, early], final);
            expect(distances[1].mean).toBeLessThanOrEqual(distances[0].mean + .5);
            expect(await page.evaluate(() => [scrollX, scrollY, document.documentElement.clientWidth])).toEqual(layout);
            await page.screenshot({ path: `test-results/tablet-${title === '打开配置面板' ? 'config' : 'reset'}.png` });
            await pane.getByRole('button', { name: '关闭面板', exact: true }).tap();
            await expect(overlay).toHaveCount(0);
        }
    } finally { await context.close(); }
});

test('LAN exits reveal down to zero without scaling text for either close button', async ({ browser }) => {
    const { context, page } = await tablet(browser);
    try {
        for (const source of ['close', 'entry']) {
            const trigger = page.getByRole('button', { name: '局域网连接', exact: true });
            await trigger.tap();
            const panel = page.locator('.lan-panel');
            await expect(panel).toHaveAttribute('data-state', 'open'); await settle(page);
            const trace = await page.evaluate(async source => {
                const close = source === 'close' ? document.querySelector<HTMLButtonElement>('button[aria-label="关闭连接面板"]')!
                    : [...document.querySelectorAll('button')].find(button => button.textContent === '局域网连接')!;
                close.click();
                const rows: { width: number; height: number; scale: string; opacity: number; reveal: number }[] = [];
                const start = performance.now();
                while (performance.now() - start < 650) {
                    await new Promise(requestAnimationFrame);
                    const panel = document.querySelector<HTMLElement>('.lan-panel');
                    if (!panel) break;
                    const rect = panel.getBoundingClientRect();
                    const edges = getComputedStyle(panel, '::before').clipPath.slice(6).split('round')[0].replace(')', '').trim().split(/\s+/).map(parseFloat);
                    rows.push({ width: rect.width, height: rect.height, scale: getComputedStyle(panel).transform,
                        opacity: parseFloat(getComputedStyle(panel.querySelector('.popover-content')!).opacity),
                        reveal: 1 - (edges[0] + (edges[2] ?? edges[0])) / 100 });
                }
                return rows;
            }, source);
            expect(trace.length).toBeGreaterThan(2);
            expect(trace.every(frame => frame.scale === 'none')).toBe(true);
            expect(Math.max(...trace.map(frame => frame.height)) - Math.min(...trace.map(frame => frame.height))).toBeLessThan(.01);
            expect(trace.at(-1)!.opacity).toBeLessThan(.05);
            expect(trace.at(-1)!.reveal).toBeLessThan(.05);
            await expect(panel).toHaveCount(0);
            await expect(trigger).toBeFocused();
        }
    } finally { await context.close(); }
});

test('LAN closing preserves its rectangle through viewport changes and retains input on reopening', async ({ browser }) => {
    const { context, page } = await tablet(browser, 1024, 480);
    try {
        const trigger = page.getByRole('button', { name: '局域网连接', exact: true });
        await trigger.tap();
        const panel = page.locator('.lan-panel');
        await expect(panel).toHaveAttribute('data-state', 'open'); await settle(page);
        await page.getByLabel('终端名称', { exact: true }).fill('平板测试');
        const before = (await panel.boundingBox())!;
        await page.getByRole('button', { name: '关闭连接面板', exact: true }).evaluate(button => button.click());
        await page.setViewportSize({ width: 1024, height: 1366 });
        expect(await panel.boundingBox()).toEqual(before);
        await expect(panel).toHaveCount(0);
        await trigger.tap();
        await expect(panel).toHaveAttribute('data-state', 'open');
        await expect(page.getByLabel('终端名称', { exact: true })).toHaveValue('平板测试');
    } finally { await context.close(); }
});

test('LAN reopening cancels exit cleanup and keeps its current reveal progress', async ({ browser }) => {
    const { context, page } = await tablet(browser);
    try {
        await page.getByRole('button', { name: '局域网连接', exact: true }).tap();
        const panel = page.locator('.lan-panel');
        await expect(panel).toHaveAttribute('data-state', 'open'); await settle(page);
        const progress = await page.evaluate(async () => {
            document.querySelector<HTMLButtonElement>('button[aria-label="关闭连接面板"]')!.click();
            const end = performance.now() + 60;
            while (performance.now() < end) await new Promise(requestAnimationFrame);
            const content = document.querySelector('.popover-content')!;
            const before = parseFloat(getComputedStyle(content).opacity);
            [...document.querySelectorAll('button')].find(button => button.textContent === '局域网连接')!.click();
            const frames: number[] = [];
            const until = performance.now() + 600;
            while (performance.now() < until) {
                await new Promise(requestAnimationFrame);
                frames.push(parseFloat(getComputedStyle(content).opacity));
            }
            return { before, frames };
        });
        expect(Math.min(...progress.frames)).toBeGreaterThanOrEqual(progress.before - .03);
        await expect(panel).toHaveCount(1);
        await expect(panel).toHaveAttribute('data-state', 'open');
        expect(progress.frames.at(-1)).toBe(1);
    } finally { await context.close(); }
});

test('LAN exit cleanup does not steal focus from a newly opened dialog', async ({ browser }) => {
    const { context, page } = await tablet(browser);
    try {
        await page.getByRole('button', { name: '局域网连接', exact: true }).tap();
        await expect(page.locator('.lan-panel')).toHaveAttribute('data-state', 'open'); await settle(page);
        await page.getByRole('button', { name: '关闭连接面板', exact: true }).evaluate(button => button.click());
        await page.getByRole('button', { name: '打开配置面板', exact: true }).tap();
        const close = page.getByRole('dialog', { name: '设备配置面板', exact: true }).getByRole('button', { name: '关闭面板', exact: true });
        await expect(close).toBeFocused();
        await expect(page.locator('.lan-panel')).toHaveCount(0);
        await expect(close).toBeFocused();
    } finally { await context.close(); }
});
