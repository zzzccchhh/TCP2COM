import { expect, test } from '@playwright/test';
import type { Locator, Page } from '@playwright/test';
import { join } from 'node:path';
import { installBridge } from './helpers';

async function ready(page: Page, theme = 'light') {
    await installBridge(page);
    await page.addInitScript(value => localStorage.setItem('wifi-bridge-appearance', value), theme);
    await page.goto('/');
    await page.bringToFront();
    await expect(page.getByText('本地运行', { exact: true })).toHaveCount(1);
    await page.evaluate(async () => {
        await document.fonts.ready;
        await Promise.all(document.getAnimations().map(animation => animation.finished.catch(() => {})));
    });
}
async function trace(lens: Locator, duration = 850) {
    await lens.evaluate((element, duration) => {
        const started = performance.now();
        const width = (element as HTMLElement).offsetWidth, height = (element as HTMLElement).offsetHeight;
        (window as any).__elastic = { done: false, frames: [] };
        function frame() {
            const box = element.getBoundingClientRect();
            (window as any).__elastic.frames.push({ time: performance.now() - started,
                x: box.x + box.width / 2, y: box.y + box.height / 2, width: box.width / width, height: box.height / height });
            if (performance.now() - started < duration) requestAnimationFrame(frame);
            else (window as any).__elastic.done = true;
        }
        requestAnimationFrame(frame);
    }, duration);
}
async function frames(page: Page) {
    await expect.poll(() => page.evaluate(() => (window as any).__elastic.done)).toBe(true);
    return page.evaluate(() => (window as any).__elastic.frames as { time: number; x: number; y: number; width: number; height: number }[]);
}

for (const theme of ['light', 'dark']) test(`a quick ${theme} tap blooms then settles without a visible undershoot`, async ({ page }) => {
    await ready(page, theme);
    const group = page.getByRole('group', { name: '电脑网络角色', exact: true });
    const lens = group.locator('.liquid-indicator');
    await trace(lens);
    await group.getByRole('button', { name: 'TCP 服务端', exact: true }).click();
    const path = await frames(page);
    expect(Math.max(...path.map(frame => frame.width))).toBeGreaterThan(1.18);
    const peak = Math.max(...path.map(frame => frame.height));
    expect(peak).toBeGreaterThan(1.35);
    const peakIndex = path.findIndex(frame => frame.height === peak);
    const settling = path.slice(peakIndex);
    expect(Math.min(...settling.map(frame => frame.height))).toBeGreaterThan(.995);
    expect(settling.every((frame, i) => !i || frame.height <= settling[i - 1].height + .01)).toBe(true);
    expect(Math.abs(path.at(-1)!.height - 1)).toBeLessThan(.015);
    expect(Math.max(...path.map(frame => frame.x)) - Math.min(...path.map(frame => frame.x))).toBeLessThan(.5);
    expect(Math.max(...path.map(frame => frame.y)) - Math.min(...path.map(frame => frame.y))).toBeLessThan(.5);
    expect(await page.evaluate(() => (window as any).__calls.filter((call: any[]) => call[0] === 'update_settings'))).toHaveLength(0);
});

test('a held lens returns gently to its normal size within 450ms', async ({ page }) => {
    await ready(page);
    const group = page.getByRole('group', { name: '电脑网络角色', exact: true });
    const lens = group.locator('.liquid-indicator');
    await group.getByRole('button', { name: 'TCP 服务端', exact: true }).hover();
    await page.mouse.down(); await page.waitForTimeout(400);
    await trace(lens, 450); await page.mouse.up();
    const path = await frames(page);
    expect(path[0].height).toBeGreaterThan(1.2);
    expect(Math.min(...path.map(frame => frame.height))).toBeGreaterThan(.995);
    expect(path.every((frame, i) => !i || frame.height <= path[i - 1].height + .01)).toBe(true);
    expect(Math.abs(path.at(-1)!.height - 1)).toBeLessThan(.01);
    await expect(group.getByRole('button', { name: 'TCP 服务端', exact: true })).toHaveAttribute('aria-pressed', 'true');
});

test('holding and regrabbing preserve material continuity without mutating optical resources', async ({ page }) => {
    await ready(page);
    const group = page.getByRole('group', { name: '电脑网络角色', exact: true });
    const lens = group.locator('.liquid-indicator');
    const selected = group.getByRole('button', { name: 'TCP 服务端', exact: true });
    const box = (await selected.boundingBox())!;
    const definitions = await page.locator('.liquid-glass-defs').evaluateAll(elements => elements.map(element => element.outerHTML));
    await selected.hover(); await page.mouse.down(); await page.waitForTimeout(350);
    expect((await lens.boundingBox())!.height / box.height).toBeGreaterThan(1.3);
    await expect(lens.locator('.liquid-raised-optics')).toHaveCSS('opacity', '1');
    const raised = await lens.locator('.liquid-raised-optics').evaluate(element => getComputedStyle(element).backdropFilter);
    expect(raised).toContain('-raised');
    await page.mouse.up(); await page.waitForTimeout(60);
    const before = (await lens.boundingBox())!.height;
    await page.mouse.down();
    const after = (await lens.boundingBox())!.height;
    expect(Math.abs(after - before)).toBeLessThan(8);
    await page.mouse.move(box.x + box.width / 2 + 28, box.y + box.height / 2, { steps: 5 });
    await page.keyboard.press('Escape'); await page.mouse.up();
    await expect(selected).toHaveAttribute('aria-pressed', 'true');
    await expect(lens).not.toHaveAttribute('data-lens-active', 'true');
    expect(await page.locator('.liquid-glass-defs').evaluateAll(elements => elements.map(element => element.outerHTML))).toEqual(definitions);
    expect(await page.evaluate(() => (window as any).__calls.filter((call: any[]) => call[0] === 'update_settings'))).toHaveLength(0);
});

test('switches keep the quick-tap bloom after committing and busy state stops the material', async ({ page }) => {
    await ready(page);
    await page.getByRole('button', { name: '高级参数', exact: true }).click();
    const toggle = page.getByRole('switch', { name: '调试日志', exact: true });
    await toggle.locator('..').scrollIntoViewIfNeeded();
    const lens = toggle.locator('..').locator('.liquid-toggle-thumb');
    await trace(lens);
    await toggle.locator('..').locator('.liquid-toggle-track').click();
    await expect(toggle).toBeChecked();
    const path = await frames(page);
    expect(Math.max(...path.map(frame => frame.height))).toBeGreaterThan(1.35);
    expect(Math.max(...path.map(frame => frame.width))).toBeGreaterThan(1.3);
    expect(Math.abs(path.at(-1)!.height - 1)).toBeLessThan(.015);
    expect(await page.evaluate(() => (window as any).__calls.filter((call: any[]) => call[0] === 'update_settings'))).toHaveLength(1);
    await toggle.focus(); await page.keyboard.press('Space'); await expect(toggle).not.toBeChecked();
    await page.evaluate(() => { (window as any).__bridgeState.config_busy = true; });
    await expect(toggle).toBeDisabled();
    await expect(lens).not.toHaveAttribute('data-lens-active', 'true');
});

test('enabling reduced motion during a bloom immediately restores normal size', async ({ page }) => {
    await ready(page);
    const group = page.getByRole('group', { name: '电脑网络角色', exact: true });
    const lens = group.locator('.liquid-indicator');
    await group.getByRole('button', { name: 'TCP 服务端', exact: true }).hover();
    await page.mouse.down(); await page.waitForTimeout(100);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await expect(lens).not.toHaveAttribute('data-lens-active', 'true');
    await expect(lens).toHaveCSS('transform', 'none');
    await page.mouse.up();
    await trace(lens, 250); await group.getByRole('button', { name: 'UDP', exact: true }).click();
    const path = await frames(page);
    expect(path.every(frame => Math.abs(frame.height - 1) < .001)).toBe(true);
    await expect(group.getByRole('button', { name: 'UDP', exact: true })).toHaveAttribute('aria-pressed', 'true');
});

test('tablet bloom leaves the other panel pixels and global SVG definitions unchanged', async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: 1024, height: 1366 }, hasTouch: true, isMobile: true });
    try {
        const page = await context.newPage(); await ready(page, 'dark');
        const box = (await page.locator('.usb-panel').boundingBox())!;
        const clip = { x: box.x + 20, y: box.y + 50, width: 180, height: 100 };
        const before = await page.screenshot({ clip });
        const definitions = await page.locator('.liquid-glass-defs').evaluateAll(elements => elements.map(element => element.outerHTML));
        await page.getByRole('group', { name: '电脑网络角色' }).getByRole('button', { name: 'TCP 服务端', exact: true }).tap();
        const during = await page.screenshot({ clip });
        const max = await page.evaluate(async images => {
            const pixels = await Promise.all(images.map(async source => {
                const image = new Image(); image.src = `data:image/png;base64,${source}`; await image.decode();
                const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
                const ctx = canvas.getContext('2d')!; ctx.drawImage(image, 0, 0); return ctx.getImageData(0, 0, image.width, image.height).data;
            }));
            return pixels[0].reduce((max, value, i) => i % 4 === 3 ? max : Math.max(max, Math.abs(value - pixels[1][i])), 0);
        }, [before, during].map(buffer => buffer.toString('base64')));
        expect(max).toBeLessThan(8);
        expect(await page.locator('.liquid-glass-defs').evaluateAll(elements => elements.map(element => element.outerHTML))).toEqual(definitions);
    } finally { await context.close(); }
});

// Set LIQUID_PREVIEW_DIR to retain the same real interactions as reviewable videos.
for (const theme of ['light', 'dark']) for (const touch of [false, true]) {
    test(`${theme} ${touch ? 'touch' : 'mouse'} continuous bloom and reversal preview`, async ({ browser }) => {
        const viewport = touch ? { width: 1024, height: 1366 } : { width: 1280, height: 900 };
        const preview = process.env.LIQUID_PREVIEW_DIR;
        const context = await browser.newContext({ viewport, hasTouch: touch, isMobile: touch,
            ...(preview ? { recordVideo: { dir: join(preview, 'raw'), size: viewport } } : {}) });
        const page = await context.newPage();
        const video = page.video();
        try {
            await ready(page, theme);
            const group = page.getByRole('group', { name: '电脑网络角色', exact: true });
            const server = (await group.getByRole('button', { name: 'TCP 服务端', exact: true }).boundingBox())!;
            const udp = (await group.getByRole('button', { name: 'UDP', exact: true }).boundingBox())!;
            const client = (await group.getByRole('button', { name: 'TCP 客户端', exact: true }).boundingBox())!;
            const y = server.y + server.height / 2;
            const cdp = touch ? await context.newCDPSession(page) : null;
            const down = async (x: number) => { if (cdp) await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] }); else { await page.mouse.move(x, y); await page.mouse.down(); } };
            const move = async (x: number) => { if (cdp) await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x, y }] }); else await page.mouse.move(x, y); };
            const up = async () => { if (cdp) await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); else await page.mouse.up(); };
            async function caption(text: string) {
                if (!preview) return;
                await page.evaluate(text => {
                    let label = document.getElementById('gesture-preview-caption');
                    if (!label) { label = document.createElement('div'); label.id = 'gesture-preview-caption'; document.body.append(label); label.style.cssText = 'position:fixed;left:24px;bottom:20px;z-index:9999;padding:12px 18px;border:1px solid #ddc9ff66;border-radius:12px;background:#141218ed;color:#f0edf6;font:16px Segoe UI,sans-serif;pointer-events:none'; }
                    label.textContent = text;
                }, text);
            }
            await caption('轻点：完整鼓起，松手后柔和回落');
            await down(server.x + server.width / 2); await up(); await page.waitForTimeout(850);
            await down(udp.x + udp.width / 2); await up(); await page.waitForTimeout(850);
            await caption('长按：透明透镜放大、浮起');
            await down(udp.x + udp.width / 2); await page.waitForTimeout(180);
            const lens = (await group.locator('.liquid-indicator').boundingBox())!;
            expect(lens.height / udp.height).toBeGreaterThan(1.3);
            await page.screenshot({ path: `test-results/elastic-hold-${theme}-${touch ? 'touch' : 'mouse'}.png` });
            await page.waitForTimeout(300);
            await caption('拖动、变向：前端鼓起，尾部追随');
            for (let step = 1; step <= 18; step++) { await move(udp.x + udp.width / 2 + (client.x + client.width / 2 - udp.x - udp.width / 2) * step / 18); await page.waitForTimeout(18); }
            for (let step = 1; step <= 9; step++) { await move(client.x + client.width / 2 + (server.x + server.width / 2 - client.x - client.width / 2) * step / 9); await page.waitForTimeout(18); }
            await up(); await page.waitForTimeout(850);
            await expect(group.getByRole('button', { name: 'TCP 服务端', exact: true })).toHaveAttribute('aria-pressed', 'true');
            await caption('连续轻点：接续当前形状和速度');
            for (const box of [client, udp, server]) { await down(box.x + box.width / 2); await up(); await page.waitForTimeout(100); }
            await page.waitForTimeout(1000);
            await expect(group.locator('.liquid-indicator')).not.toHaveAttribute('data-lens-active', 'true');
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        } finally { await context.close(); }
        if (preview && video) await video.saveAs(join(preview, `liquid-${theme}-${touch ? 'touch' : 'mouse'}.webm`));
    });
}
