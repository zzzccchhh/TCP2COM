import { expect, test } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { installBridge } from './helpers';

async function settle(page: import('@playwright/test').Page) {
    await page.evaluate(() => Promise.all(document.getAnimations().map(animation => animation.finished.catch(() => {}))));
}

for (const theme of ['light', 'dark']) for (const device of ['desktop', 'tablet', 'phone']) {
    test(`${theme} ${device} floating card refracts real backdrop pixels with clear foreground text`, async ({ browser }) => {
        const directory = process.env.FLOATING_GLASS_PREVIEW_DIR;
        if (directory) await mkdir(directory, { recursive: true });
        const viewport = device === 'desktop' ? { width: 1440, height: 1000 } : device === 'tablet' ? { width: 1024, height: 1366 } : { width: 390, height: 844 };
        const context = await browser.newContext({ viewport, hasTouch: device !== 'desktop', isMobile: device !== 'desktop',
            ...(directory ? { recordVideo: { dir: join(directory, 'raw'), size: viewport } } : {}) });
        const page = await context.newPage(), video = page.video();
        try {
            await page.addInitScript(theme => localStorage.setItem('wifi-bridge-appearance', theme), theme);
            await installBridge(page); await page.goto('/'); await page.bringToFront(); await settle(page);
            await page.getByRole('button', { name: '局域网连接', exact: true }).click();
            const panel = page.locator('.lan-panel');
            await expect(panel).toHaveAttribute('data-state', 'open'); await settle(page);
            await expect(panel).toHaveAttribute('data-glass', 'refractive');
            const box = (await panel.boundingBox())!;
            expect(box.x).toBeGreaterThanOrEqual(11); expect(box.x + box.width).toBeLessThanOrEqual(viewport.width - 11);
            if (directory) await page.screenshot({ path: join(directory, `floating-${theme}-${device}.png`) });
            await page.evaluate(() => {
                const probe = document.createElement('div'); probe.id = 'optical-probe';
                probe.style.cssText = 'position:fixed;inset:0;z-index:14;pointer-events:none;background:repeating-linear-gradient(115deg,#87a4d8 0 10px,#fff5bb 10px 20px)';
                document.body.append(probe);
            });
            const refracted = await panel.screenshot();
            const before = await panel.evaluate(element => ({ filter: getComputedStyle(element.querySelector('input')!).filter,
                nativeHeight: element.querySelector('input')!.getBoundingClientRect().height,
                map: (element as HTMLElement).style.getPropertyValue('--glass-refraction') }));
            await panel.evaluate(element => (element as HTMLElement).style.setProperty('--glass-refraction', 'blur(1.1px) saturate(1.35)'));
            const plain = await panel.screenshot();
            const changed = await page.evaluate(async images => {
                const pixels = await Promise.all(images.map(async base64 => {
                    const image = new Image(); image.src = `data:image/png;base64,${base64}`; await image.decode();
                    const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
                    const ctx = canvas.getContext('2d')!; ctx.drawImage(image, 0, 0); return { width: image.width, height: image.height, data: ctx.getImageData(0, 0, image.width, image.height).data };
                }));
                let rim = 0;
                const { width, height, data } = pixels[0];
                for (let y = 6; y < height - 6; y++) for (let x = 6; x < width - 6; x++) {
                    if (Math.min(x, y, width - x, height - y) > 24) continue;
                    const i = (y * width + x) * 4;
                    if (Math.max(...[0, 1, 2].map(channel => Math.abs(data[i + channel] - pixels[1].data[i + channel]))) > 12) rim++;
                }
                return rim;
            }, [refracted, plain].map(buffer => buffer.toString('base64')));
            expect(changed, 'displacement must visibly bend real backdrop pixels at the rim').toBeGreaterThan(150);
            expect(before.filter).toBe('none');
            expect(await panel.locator('input').first().evaluate(element => element.getBoundingClientRect().height)).toBe(before.nativeHeight);
            await panel.evaluate((element, map) => (element as HTMLElement).style.setProperty('--glass-refraction', map), before.map);
            await page.locator('#optical-probe').evaluate(element => element.remove());
            await page.getByRole('button', { name: '关闭连接面板', exact: true }).click(); await expect(panel).toHaveCount(0);
            await page.getByRole('button', { name: '选择业务 COM', exact: true }).scrollIntoViewIfNeeded();
            await page.getByRole('button', { name: '选择业务 COM', exact: true }).click();
            await expect(page.locator('.picker-window')).toHaveAttribute('data-glass', 'refractive'); await settle(page);
            await page.keyboard.press('Escape'); await expect(page.locator('.picker-position')).toHaveCount(0);
        } finally { await context.close(); }
        if (directory && video) await video.saveAs(join(directory, `floating-${theme}-${device}.webm`));
    });
}

for (const width of [761, 1024, 390]) test(`LAN position stays fixed through delayed peers and scrollbars at ${width}px`, async ({ browser, request }) => {
    const links = await (await request.get('http://127.0.0.1:8767')).json();
    const context = await browser.newContext({ viewport: { width, height: 600 }, hasTouch: true, isMobile: true });
    const page = await context.newPage();
    try {
        await page.route('**/api/connect', route => route.continue({
            postData: JSON.stringify({ ...route.request().postDataJSON(), name: `窗口定位回归-${width}` }),
        }));
        await page.route('**/api/call', async route => {
            if (route.request().postDataJSON()?.method !== 'peers') return route.continue();
            const response = await route.fetch(), result = await response.json();
            result.data = Array.from({ length: 12 }, (_, i) => ({ id: `delayed-${i}`, name: `延迟返回的终端 ${i}`, self: false }));
            await new Promise(resolve => setTimeout(resolve, 180));
            await route.fulfill({ response, json: result });
        });
        await page.goto(links.online); await page.bringToFront();
        await expect(page.getByRole('status', { name: '服务连接状态', exact: true })).toHaveText('服务已连接');
        const trace = await page.evaluate(async () => {
            [...document.querySelectorAll('button')].find(button => button.textContent === '局域网连接')!.click();
            const frames: number[][] = [], end = performance.now() + 850;
            while (performance.now() < end) {
                await new Promise(requestAnimationFrame);
                const node = document.querySelector<HTMLElement>('.lan-panel'); if (!node?.dataset.ready) continue;
                const rect = node.getBoundingClientRect(); frames.push([rect.x, rect.y, rect.width, rect.height]);
            }
            return frames;
        });
        expect(trace.length).toBeGreaterThan(8);
        for (let i = 0; i < 4; i++) expect(Math.max(...trace.map(row => row[i])) - Math.min(...trace.map(row => row[i]))).toBeLessThan(.1);
        expect(trace[0][0]).toBeGreaterThanOrEqual(11); expect(trace[0][0] + trace[0][2]).toBeLessThanOrEqual(width - 11);
        await expect(page.getByRole('listitem').filter({ hasText: '延迟返回的终端 11' })).toBeAttached();
        const panel = page.locator('.lan-panel');
        expect(await panel.locator('.popover-content').evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
        const before = await panel.boundingBox();
        await page.getByRole('listitem').filter({ hasText: '延迟返回的终端 11' }).scrollIntoViewIfNeeded();
        expect(await panel.boundingBox()).toEqual(before);
        await page.getByRole('button', { name: '局域网连接', exact: true }).evaluate(button => button.click());
        await expect(panel).toHaveCount(0);
    } finally {
        // These clients share the LAN fixture with its pairing tests; remove only our own session.
        const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('wifi-bridge-lan') || 'null')).catch(() => null);
        if (saved?.token) {
            const headers = { Authorization: `Bearer ${saved.token}` };
            const response = await request.post(`${saved.address}/api/call`, { headers, data: { method: 'peers', args: [] } });
            const own = (await response.json()).data?.find((peer: any) => peer.self);
            if (own) await request.post(`${saved.address}/api/call`, { headers, data: { method: 'revoke', args: [own.id] } });
        }
        await context.close();
    }
});
