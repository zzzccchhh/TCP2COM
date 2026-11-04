import { expect, test } from '@playwright/test';
import type { Browser, Locator, Page } from '@playwright/test';
import type { Buffer } from 'node:buffer';
import { join } from 'node:path';
import { installBridge } from './helpers';

async function ready(browser: Browser, theme = 'light', touch = false, record = false) {
    const viewport = touch ? { width: 390, height: 844 } : { width: 1440, height: 1000 };
    const directory = process.env.LIQUID_FLOW_PREVIEW_DIR;
    const context = await browser.newContext({ viewport, hasTouch: touch, isMobile: touch,
        ...(directory && record ? { recordVideo: { dir: join(directory, 'raw'), size: viewport } } : {}) });
    const page = await context.newPage(); await installBridge(page);
    await page.addInitScript(theme => localStorage.setItem('wifi-bridge-appearance', theme), theme);
    await page.goto('/'); await page.bringToFront();
    await expect(page.locator('.local-indicator')).toHaveText('本地运行');
    await page.evaluate(async () => { await document.fonts.ready; await Promise.all(document.getAnimations().map(animation => animation.finished.catch(() => {}))); });
    const cdp = touch ? await context.newCDPSession(page) : null;
    const pointer = {
        async down(x: number, y: number) {
            if (cdp) await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
            else { await page.mouse.move(x, y); await page.mouse.down(); }
        },
        async move(x: number, y: number) {
            if (cdp) await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x, y }] });
            else await page.mouse.move(x, y);
        },
        async up() { if (cdp) await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); else await page.mouse.up(); },
        async cancel() { if (cdp) await cdp.send('Input.dispatchTouchEvent', { type: 'touchCancel', touchPoints: [] }); else { await page.keyboard.press('Escape'); await page.mouse.up(); } },
    };
    return { page, context, pointer, directory, video: page.video() };
}
async function changedPixels(page: Page, first: Buffer, second: Buffer) {
    return page.evaluate(async images => {
        const pixels = await Promise.all(images.map(async base64 => {
            const image = new Image(); image.src = `data:image/png;base64,${base64}`; await image.decode();
            const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
            const ctx = canvas.getContext('2d')!; ctx.drawImage(image, 0, 0);
            return ctx.getImageData(0, 0, image.width, image.height).data;
        }));
        let count = 0;
        for (let i = 0; i < pixels[0].length; i += 4) if ([0, 1, 2].some(c => Math.abs(pixels[0][i + c] - pixels[1][i + c]) > 16)) count++;
        return count;
    }, [first, second].map(buffer => buffer.toString('base64')));
}
async function shape(lens: Locator) {
    return lens.evaluate(element => {
        const style = getComputedStyle(element), box = element.getBoundingClientRect();
        return { bend: Number(style.getPropertyValue('--liquid-bend')), flow: Number(style.getPropertyValue('--liquid-flow')),
            edge: Number(style.getPropertyValue('--liquid-edge')), width: box.width / (element as HTMLElement).offsetWidth,
            height: box.height / (element as HTMLElement).offsetHeight, skew: new DOMMatrixReadOnly(style.transform).c,
            roundness: parseFloat(style.borderTopLeftRadius) / parseFloat(style.borderTopRightRadius) };
    });
}
async function progress(track: Locator) {
    return track.evaluate(element => {
        const style = getComputedStyle(element);
        const anchor = element.querySelector<HTMLElement>('.liquid-toggle-anchor')!;
        const travel = (element as HTMLElement).clientWidth - anchor.offsetWidth - 4;
        return { p: Number(style.getPropertyValue('--toggle-progress')), tint: Number(getComputedStyle(element, '::before').opacity),
            x: new DOMMatrixReadOnly(getComputedStyle(anchor).transform).m41 / travel,
            colour: getComputedStyle(element, '::before').backgroundColor };
    });
}
async function materialShot(page: Page, locator: Locator, path: string) {
    const box = (await locator.boundingBox())!, viewport = page.viewportSize()!;
    const x = Math.max(0, box.x - 28), y = Math.max(0, box.y - 28);
    await page.screenshot({ path, clip: { x, y, width: Math.min(viewport.width - x, box.width + 56), height: Math.min(viewport.height - y, box.height + 56) } });
}

for (const theme of ['light', 'dark']) for (const touch of [false, true]) {
    const name = `${theme} ${touch ? 'phone' : 'desktop'}`;
    test(`${name} rests clear and moves as a rounded raised lens`, async ({ browser }) => {
        const { page, context, pointer, directory, video } = await ready(browser, theme, touch, true);
        try {
            const group = page.getByRole('group', { name: '电脑网络角色', exact: true });
            const lens = group.locator('.liquid-indicator');
            await expect(lens).toHaveAttribute('data-glass', 'refractive');
            const styles = '.liquid-elastic-lens::before, .liquid-raised-optics, .liquid-lens-sheen { backdrop-filter: none !important; -webkit-backdrop-filter: none !important; }';
            const rest = await group.screenshot();
            const suppression = await page.addStyleTag({ content: styles });
            expect(await changedPixels(page, rest, await group.screenshot())).toBeLessThan(3);
            await suppression.evaluate(element => element.remove());
            const definitions = await page.locator('.liquid-glass-defs').evaluateAll(elements => elements.map(element => element.outerHTML));
            const button = (await group.getByRole('button', { name: 'TCP 服务端', exact: true }).boundingBox())!;
            const x = button.x + button.width / 2, y = button.y + button.height / 2;
            await pointer.down(x, y); await page.waitForTimeout(450);
            await expect(lens).toHaveAttribute('data-lens-active', 'true');
            const held = await shape(lens);
            await expect(lens.locator('.liquid-lens-sheen')).toHaveCSS('backdrop-filter', 'none');
            const refracted = await group.screenshot();
            const flat = await page.addStyleTag({ content: styles });
            expect(await changedPixels(page, refracted, await group.screenshot())).toBeGreaterThan(15);
            await flat.evaluate(element => element.remove());
            for (let step = 1; step <= 8; step++) { await pointer.move(x + step * 4, y); await page.waitForTimeout(32); }
            const slow = await shape(lens);
            expect(slow.flow).toBeGreaterThan(.01);
            expect(slow.width / held.width).toBeGreaterThan(.98); expect(slow.width / held.width).toBeLessThan(1.06);
            expect(slow.height / held.height).toBeGreaterThan(.98); expect(slow.skew).toBe(0);
            expect(slow.roundness).toBeGreaterThan(.85); expect(slow.roundness).toBeLessThan(1.18);
            await materialShot(page, group, `test-results/flow-${theme}-${touch ? 'phone' : 'desktop'}-slow.png`);
            for (let step = 1; step <= 5; step++) await pointer.move(x + 32 - step * 13, y);
            await expect.poll(async () => (await shape(lens)).bend).toBeLessThan(-.05);
            const fast = await shape(lens);
            expect(fast.width / held.width).toBeLessThan(1.06); expect(fast.height / held.height).toBeGreaterThan(.98);
            expect(fast.skew).toBe(0); expect(fast.roundness).toBeGreaterThan(.85); expect(fast.roundness).toBeLessThan(1.18);
            await materialShot(page, group, `test-results/flow-${theme}-${touch ? 'phone' : 'desktop'}-reverse.png`);
            await page.waitForTimeout(650);
            expect((await shape(lens)).flow).toBeLessThan(.2);
            await expect(lens).toHaveAttribute('data-lens-active', 'true');
            const bounds = (await group.boundingBox())!;
            await pointer.move(bounds.x + bounds.width + 70, y);
            await expect.poll(async () => (await shape(lens)).edge).toBeGreaterThan(.7);
            await materialShot(page, group, `test-results/flow-${theme}-${touch ? 'phone' : 'desktop'}-edge.png`);
            expect(await page.locator('.liquid-glass-defs').evaluateAll(elements => elements.map(element => element.outerHTML))).toEqual(definitions);
            await pointer.cancel();
            await expect(lens).not.toHaveAttribute('data-lens-active', 'true');
            await expect(lens).toHaveCSS('backdrop-filter', 'none');
            const final = await group.screenshot();
            const clear = await page.addStyleTag({ content: styles });
            expect(await changedPixels(page, final, await group.screenshot())).toBeLessThan(3);
            await clear.evaluate(element => element.remove());
            expect(await page.evaluate(() => (window as any).__calls.filter((call: any[]) => call[0] === 'update_settings'))).toHaveLength(0);
        } finally { await context.close(); }
        if (directory && video) await video.saveAs(join(directory, `flow-${theme}-${touch ? 'phone' : 'desktop'}.webm`));
    });

    test(`${name} reconnect tint follows every drag position and click frame`, async ({ browser }) => {
        const { page, context, pointer, directory, video } = await ready(browser, theme, touch, true);
        try {
            await page.getByRole('button', { name: '打开配置面板', exact: true }).click();
            const toggle = page.getByRole('switch', { name: '设备自动重连', exact: true });
            const track = toggle.locator('..').locator('.liquid-toggle-track');
            await track.scrollIntoViewIfNeeded();
            await page.evaluate(() => Promise.all(document.getAnimations().map(animation => animation.finished.catch(() => {}))));
            await expect(toggle).toBeChecked();
            const box = (await track.boundingBox())!;
            const travel = await track.evaluate(element => (element as HTMLElement).clientWidth - element.querySelector<HTMLElement>('.liquid-toggle-anchor')!.offsetWidth - 4);
            const x = box.x + box.width - 12, y = box.y + box.height / 2;
            const colour = (await progress(track)).colour.match(/[\d.]+/g)!.map(Number);
            expect(colour[1]).toBeGreaterThan(colour[0] + 50); expect(colour[1]).toBeGreaterThan(colour[2] + 50);
            await pointer.down(x, y);
            await page.waitForTimeout(450);
            await materialShot(page, track, `test-results/rounded-switch-${theme}-${touch ? 'phone' : 'desktop'}.png`);
            for (const p of [.75, .5, .25]) {
                await pointer.move(x - (1 - p) * travel, y);
                const sample = await progress(track);
                expect(sample.p).toBeCloseTo(p, 2); expect(sample.tint).toBeCloseTo(p, 2); expect(sample.x).toBeCloseTo(p, 2);
                await expect(toggle).toBeChecked();
            }
            expect(await page.evaluate(() => (window as any).__calls.filter((call: any[]) => call[0] === 'remember_draft'))).toHaveLength(0);
            await page.keyboard.press('Escape'); await pointer.up();
            await expect.poll(async () => (await progress(track)).p).toBe(1);
            await expect(toggle).toBeChecked();
            await track.evaluate(element => {
                (window as any).__colourFrames = [];
                const start = performance.now();
                function frame() {
                    const p = Number(getComputedStyle(element).getPropertyValue('--toggle-progress'));
                    const tint = Number(getComputedStyle(element, '::before').opacity);
                    const anchor = element.querySelector<HTMLElement>('.liquid-toggle-anchor')!;
                    const travel = (element as HTMLElement).clientWidth - anchor.offsetWidth - 4;
                    const x = new DOMMatrixReadOnly(getComputedStyle(anchor).transform).m41 / travel;
                    const flow = Number(getComputedStyle(element.querySelector('.liquid-toggle-thumb')!).getPropertyValue('--liquid-flow'));
                    (window as any).__colourFrames.push({ p, tint, x, flow });
                    if (performance.now() - start < 950) requestAnimationFrame(frame);
                }
                requestAnimationFrame(frame);
            });
            await track.click(); await expect(toggle).not.toBeChecked();
            await expect.poll(async () => (await progress(track)).p).toBe(0);
            await toggle.locator('..').locator('.toggle-label').click(); await expect(toggle).toBeChecked();
            await expect.poll(async () => (await progress(track)).p).toBe(1);
            await page.waitForTimeout(350);
            const frames: { p: number; tint: number; x: number; flow: number }[] = await page.evaluate(() => (window as any).__colourFrames);
            expect(frames.filter(frame => frame.p > .1 && frame.p < .9).length).toBeGreaterThan(5);
            expect(frames.filter(frame => Math.abs(frame.p - frame.tint) >= .001 || Math.abs(frame.p - frame.x) >= .001).slice(0, 8)).toEqual([]);
            expect(Math.max(...frames.map(frame => frame.flow))).toBeGreaterThan(.01);
            expect(Math.max(...frames.map(frame => frame.flow))).toBeLessThan(.35);
            expect(await page.evaluate(() => (window as any).__calls.filter((call: any[]) => call[0] === 'remember_draft'))).toHaveLength(2);
            expect(await page.evaluate(() => (window as any).__calls.some((call: any[]) => call[0] === 'save_device'))).toBe(false);
        } finally { await context.close(); }
        if (directory && video) await video.saveAs(join(directory, `reconnect-${theme}-${touch ? 'phone' : 'desktop'}.webm`));
    });
}

test('a stretched droplet settles after release without shrinking below its resting height', async ({ browser }) => {
    const { page, context, pointer } = await ready(browser);
    try {
        const group = page.getByRole('group', { name: '电脑网络角色', exact: true });
        const lens = group.locator('.liquid-indicator');
        const button = (await group.getByRole('button', { name: 'TCP 服务端', exact: true }).boundingBox())!;
        const y = button.y + button.height / 2, x = button.x + button.width / 2;
        await pointer.down(x, y); await page.waitForTimeout(400);
        for (let step = 1; step <= 7; step++) await pointer.move(x + step * 9, y);
        await expect.poll(async () => (await shape(lens)).flow).toBeGreaterThan(.08);
        await lens.evaluate(element => {
            const start = performance.now();
            (window as any).__releaseHeights = [];
            function frame() {
                (window as any).__releaseHeights.push(element.getBoundingClientRect().height / (element as HTMLElement).offsetHeight);
                if (performance.now() - start < 550) requestAnimationFrame(frame);
            }
            requestAnimationFrame(frame);
        });
        await pointer.up(); await expect(lens).not.toHaveAttribute('data-lens-active', 'true');
        await page.waitForTimeout(100);
        const heights: number[] = await page.evaluate(() => (window as any).__releaseHeights);
        expect(Math.min(...heights)).toBeGreaterThan(.995);
        expect(Math.abs(heights.at(-1)! - 1)).toBeLessThan(.01);
    } finally { await context.close(); }
});

test('grabbing a changing switch preserves its current position and reduced motion settles both layers', async ({ browser }) => {
    const { page, context, pointer } = await ready(browser);
    try {
        await page.getByRole('button', { name: '打开配置面板', exact: true }).click();
        const toggle = page.getByRole('switch', { name: '设备自动重连', exact: true });
        const track = toggle.locator('..').locator('.liquid-toggle-track');
        await track.scrollIntoViewIfNeeded();
        await track.click(); await expect(toggle).not.toBeChecked();
        const box = (await track.boundingBox())!;
        await track.evaluate(element => element.addEventListener('pointerdown', () => {
            (window as any).__grabProgress = Number(getComputedStyle(element).getPropertyValue('--toggle-progress'));
        }, { capture: true, once: true }));
        await pointer.down(box.x + box.width / 2, box.y + box.height / 2);
        const before = await page.evaluate(() => (window as any).__grabProgress);
        const grabbed = (await progress(track)).p;
        expect(Math.abs(grabbed - before)).toBeLessThan(.001);
        await pointer.move(box.x + box.width - 2, box.y + box.height / 2); await pointer.up();
        await expect(toggle).toBeChecked();
        await page.emulateMedia({ reducedMotion: 'reduce' });
        await track.click(); await expect(toggle).not.toBeChecked();
        await expect.poll(() => progress(track)).toMatchObject({ p: 0, tint: 0, x: 0 });
        await expect(track.locator('.liquid-toggle-thumb')).not.toHaveAttribute('data-lens-active', 'true');
    } finally { await context.close(); }
});
