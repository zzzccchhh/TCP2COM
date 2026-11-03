import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { installBridge } from './helpers';

async function ready(page: Page) {
    await installBridge(page);
    await page.goto('/');
    await expect(page.getByText('本地运行', { exact: true })).toHaveCount(1);
    await page.evaluate(() => {
        (window as any).__glassReveal = (element: Element) => {
            const edges = getComputedStyle(element, '::before').clipPath.slice(6).split('round')[0]
                .replace(')', '').trim().split(/\s+/).map(value => parseFloat(value));
            return 1 - (edges[0] + (edges[2] ?? edges[0])) / 100;
        };
    });
}

for (const action of ['escape', 'selection'] as const) test(`COM text stays sharp throughout menu exit by ${action}`, async ({ page }) => {
    await ready(page);
    if (action === 'escape') {
        await page.setViewportSize({ width: 390, height: 500 });
        // Keep the enlarged trigger near the bottom to exercise an upward-opening menu.
        await page.getByLabel('业务 COM', { exact: true }).evaluate(element => {
            scrollBy(0, element.getBoundingClientRect().bottom - innerHeight + 45);
        });
    }
    const com = page.getByLabel('业务 COM', { exact: true });
    await page.getByRole('button', { name: '选择业务 COM', exact: true }).click();
    const popup = page.locator('.picker-position');
    await expect(popup).toHaveAttribute('data-state', 'open');
    expect(await popup.evaluate(element => (element as HTMLElement).style.getPropertyValue('--picker-edge'))).toBe(action === 'escape' ? '100%' : '0%');
    await popup.evaluate(element => Promise.all(element.getAnimations({ subtree: true }).map(animation => animation.finished)));
    const box = (await com.boundingBox())!;
    const clip = { x: box.x + 9, y: box.y + 8, width: 62, height: 20 };
    if (action === 'escape') await page.keyboard.press('Escape');
    else await page.getByRole('option', { name: 'COM88 · USB Serial', exact: true }).click();
    const first = await page.screenshot({ clip, path: `test-results/com-close-${action}-first.png` });
    const frames = await popup.evaluate(async element => {
        const frames: { scale: number; glass: number; textHeight: number }[] = [];
        for (let i = 0; i < 6; i++) {
            await new Promise(requestAnimationFrame);
            frames.push({ scale: new DOMMatrixReadOnly(getComputedStyle(element).transform).d,
                glass: (window as any).__glassReveal(element.querySelector('.picker-window')),
                textHeight: element.querySelector('.picker-list button')!.getBoundingClientRect().height });
        }
        return frames;
    });
    expect(frames.every(frame => Math.abs(frame.scale - 1) < .001)).toBe(true);
    expect(Math.max(...frames.map(frame => frame.textHeight)) - Math.min(...frames.map(frame => frame.textHeight))).toBeLessThan(.01);
    expect(frames.at(-1)!.glass).toBeLessThan(frames[0].glass);
    const during = await page.screenshot({ clip, path: `test-results/com-close-${action}-during.png` });
    await expect(popup).toHaveCount(0);
    const after = await page.screenshot({ clip, path: `test-results/com-close-${action}-after.png` });
    const differences = await page.evaluate(async images => {
        const pixels = await Promise.all(images.map(async base64 => {
            const img = new Image(); img.src = `data:image/png;base64,${base64}`; await img.decode();
            const canvas = document.createElement('canvas'); canvas.width = img.width; canvas.height = img.height;
            const ctx = canvas.getContext('2d')!; ctx.drawImage(img, 0, 0);
            return ctx.getImageData(0, 0, img.width, img.height).data;
        }));
        return pixels.slice(0, 2).map(image => {
            let max = 0;
            for (let i = 0; i < image.length; i += 4) {
                if (Math.min(pixels[2][i], pixels[2][i + 1], pixels[2][i + 2]) > 180) continue;
                for (let channel = 0; channel < 3; channel++) max = Math.max(max, Math.abs(image[i + channel] - pixels[2][i + channel]));
            }
            return max;
        });
    }, [first, during, after].map(buffer => buffer.toString('base64')));
    // Compare glyph pixels: fading menu shadows can change the pale background, especially above a field.
    expect(Math.max(...differences), 'exit must not cover, rescale or blur the field text').toBeLessThan(12);
    await expect(com).toHaveValue(action === 'escape' ? 'COM1' : 'COM88');
});

test('the moving lens visibly refracts the actual track labels', async ({ page }) => {
    await ready(page);
    await page.addStyleTag({ content: '*, *::before, *::after { transition: none !important; animation: none !important; }' });
    const group = page.getByRole('group', { name: '电脑网络角色', exact: true });
    const server = (await group.getByRole('button', { name: 'TCP 服务端', exact: true }).boundingBox())!;
    const udp = (await group.getByRole('button', { name: 'UDP', exact: true }).boundingBox())!;
    // Midway between options, the lens rim crosses real text, without a synthetic wallpaper.
    await page.mouse.move(server.x + server.width / 2, server.y + server.height / 2);
    await page.mouse.down();
    await page.mouse.move((server.x + server.width / 2 + udp.x + udp.width / 2) / 2, server.y + server.height / 2);
    const lens = group.locator('.liquid-indicator');
    await expect(lens).toHaveAttribute('data-glass', 'refractive');
    await page.evaluate(() => document.fonts.ready);
    const refracted = await group.screenshot({ path: 'test-results/liquid-lens-refraction.png' });
    await lens.evaluate(element => (element as HTMLElement).style.setProperty('--glass-refraction', 'blur(.15px) saturate(1.35)'));
    const plain = await group.screenshot({ path: 'test-results/liquid-lens-without-refraction.png' });
    const changed = await page.evaluate(async ([first, second]) => {
        async function pixels(base64: string) {
            const img = new Image(); img.src = `data:image/png;base64,${base64}`; await img.decode();
            const canvas = document.createElement('canvas'); canvas.width = img.width; canvas.height = img.height;
            const ctx = canvas.getContext('2d')!; ctx.drawImage(img, 0, 0);
            return ctx.getImageData(0, 0, img.width, img.height).data;
        }
        const a = await pixels(first), b = await pixels(second);
        let count = 0;
        for (let i = 0; i < a.length; i += 4) if (Math.max(Math.abs(a[i] - b[i]), Math.abs(a[i + 1] - b[i + 1]), Math.abs(a[i + 2] - b[i + 2])) > 20) count++;
        return count;
    }, [refracted.toString('base64'), plain.toString('base64')]);
    expect(changed, 'refraction must move visible detail, rather than only setting a CSS flag').toBeGreaterThan(25);
    await page.mouse.up();
});

test('glass switches commit once, cancel drags, and retain native keyboard control', async ({ page }) => {
    await ready(page);
    await page.getByRole('button', { name: '高级参数', exact: true }).click();
    await page.locator('.disclosure-grid').evaluate(element => Promise.all(element.getAnimations().map(animation => animation.finished)));
    const toggle = page.getByRole('switch', { name: '调试日志', exact: true });
    const track = toggle.locator('..').locator('.liquid-toggle-track');
    await expect(toggle).not.toBeChecked();
    await track.scrollIntoViewIfNeeded();
    const box = (await track.boundingBox())!;
    await page.mouse.move(box.x + 12, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width - 8, box.y + box.height / 2, { steps: 6 });
    await expect(toggle).not.toBeChecked();
    await page.mouse.up();
    await expect(toggle).toBeChecked();
    await expect.poll(() => page.evaluate(() => (window as any).__calls.filter((c: any[]) => c[0] === 'update_settings').length)).toBe(1);
    await track.click();
    await expect(toggle).not.toBeChecked();
    await toggle.focus(); await page.keyboard.press('Space');
    await expect(toggle).toBeChecked();
    await page.mouse.move(box.x + 36, box.y + box.height / 2); await page.mouse.down();
    await page.mouse.move(box.x + 5, box.y + box.height / 2, { steps: 6 });
    await track.evaluate(element => element.dispatchEvent(new PointerEvent('pointercancel', { pointerId: 1, bubbles: true })));
    await page.mouse.up();
    await expect(toggle).toBeChecked();
    await page.mouse.move(box.x + 36, box.y + box.height / 2); await page.mouse.down();
    await page.mouse.move(box.x + 5, box.y + box.height / 2, { steps: 4 });
    await page.evaluate(() => { (window as any).__bridgeState.config_busy = true; });
    await expect(toggle).toBeDisabled();
    await expect(toggle.locator('..')).not.toHaveAttribute('data-dragging', 'true');
    await page.mouse.up();
    await expect(toggle).toBeChecked();
    const count = await page.evaluate(() => (window as any).__calls.filter((c: any[]) => c[0] === 'update_settings').length);
    await track.click({ force: true });
    expect(await page.evaluate(() => (window as any).__calls.filter((c: any[]) => c[0] === 'update_settings').length)).toBe(count);
});

test('touch switches follow the finger and vertical scrolling cancels the gesture', async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    try {
        const page = await context.newPage();
        await ready(page);
        await page.getByRole('button', { name: '高级参数', exact: true }).click();
        await page.locator('.disclosure-grid').evaluate(element => Promise.all(element.getAnimations().map(a => a.finished)));
        const toggle = page.getByRole('switch', { name: '调试日志', exact: true });
        const track = toggle.locator('..').locator('.liquid-toggle-track');
        await track.scrollIntoViewIfNeeded();
        const box = (await track.boundingBox())!;
        const cdp = await context.newCDPSession(page);
        const x = box.x + 12, y = box.y + 15;
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
        for (let i = 1; i <= 6; i++) await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: x + i * 5, y }] });
        await expect(toggle).not.toBeChecked();
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
        await expect(toggle).toBeChecked();
        await page.screenshot({ path: 'test-results/liquid-toggle-mobile.png' });
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: x + 20, y }] });
        for (let i = 1; i <= 6; i++) await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: x + 20, y: y - i * 12 }] });
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
        await expect(toggle).toBeChecked();
        await expect(toggle.locator('..')).not.toHaveAttribute('data-dragging', 'true');
        expect(await page.evaluate(() => (window as any).__calls.filter((c: any[]) => c[0] === 'update_settings').length)).toBe(1);
    } finally { await context.close(); }
});

test('menus animate one glass sheet and confirmation focus waits for the visible state', async ({ page }) => {
    await ready(page);
    const device = page.getByLabel('当前设备', { exact: true });
    await device.click();
    const popup = page.locator('.picker-position');
    await expect(popup).toHaveAttribute('data-state', 'open');
    const frames = await popup.evaluate(async element => {
        const frames: { width: number; sheet: number }[] = [];
        for (let i = 0; i < 5; i++) {
            await new Promise(requestAnimationFrame);
            frames.push({ width: element.getBoundingClientRect().width,
                sheet: (window as any).__glassReveal(element.querySelector('.picker-window')) });
        }
        return frames;
    });
    expect(Math.max(...frames.map(frame => frame.width)) - Math.min(...frames.map(frame => frame.width))).toBeLessThan(.01);
    expect(frames.at(-1)!.sheet).toBeGreaterThan(frames[0].sheet);
    await popup.evaluate(element => Promise.all(element.getAnimations({ subtree: true }).map(animation => animation.finished)));
    await page.keyboard.press('Escape');
    await device.click();
    const reopenScales = await popup.evaluate(async element => {
        const scales: number[] = [];
        for (let i = 0; i < 6; i++) { await new Promise(requestAnimationFrame); scales.push(new DOMMatrixReadOnly(getComputedStyle(element).transform).d); }
        return scales;
    });
    expect(reopenScales.every(scale => Math.abs(scale - 1) < .001), 'reopening must not shrink the retained menu back over the field').toBe(true);
    await expect(popup).toHaveAttribute('data-state', 'open');
    await expect(popup).toHaveCount(1);
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: '打开配置面板', exact: true }).click();
    await page.getByRole('button', { name: '读取设备', exact: true }).click();
    await page.getByLabel('设备 UART 波特率 / bps', { exact: true }).fill('2000000');
    await page.getByRole('button', { name: '保存并重启', exact: true }).click();
    const confirmation = page.getByRole('dialog', { name: '确认保存设备配置', exact: true });
    await expect(confirmation.getByRole('button', { name: '取消', exact: true })).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(confirmation).toHaveCount(0);
    expect(await page.evaluate(() => (window as any).__calls.some((c: any[]) => c[0] === 'save_device'))).toBe(false);
});

test('first open waits for the final-size optics and has no geometry or bitmap switch while visible', async ({ page }) => {
    await page.addInitScript(() => {
        const decode = HTMLImageElement.prototype.decode;
        (window as any).__glassDecodes = [];
        HTMLImageElement.prototype.decode = function () {
            if (!this.src.startsWith('data:image/png')) return decode.call(this);
            return new Promise<void>((resolve, reject) => {
                (window as any).__glassDecodes.push(() => decode.call(this).then(resolve, reject));
            });
        };
    });
    await ready(page);
    await page.getByRole('button', { name: '选择业务 COM', exact: true }).click();
    const popup = page.locator('.picker-position');
    await expect.poll(() => page.evaluate(() => (window as any).__glassDecodes.length)).toBeGreaterThan(0);
    await expect(popup).toHaveAttribute('data-state', 'closed');
    await expect(popup).toHaveCSS('visibility', 'hidden');
    expect(await popup.evaluate(element => element.getAnimations({ subtree: true }).length)).toBe(0);
    await page.evaluate(() => (window as any).__glassDecodes.forEach((release: () => void) => release()));
    await expect(popup).toHaveAttribute('data-state', 'open');
    const frames = await popup.evaluate(async element => {
        const glass = element.querySelector<HTMLElement>('.picker-window')!;
        const id = glass.style.getPropertyValue('--glass-refraction').match(/#([^"\)]+)/)?.[1];
        const image = id ? document.getElementById(id)?.querySelector('feImage') : null;
        const initialMap = image?.getAttribute('href');
        const start = performance.now();
        const frames: { width: number; height: number; text: number; bitmapStable: boolean; ready: boolean }[] = [];
        while (performance.now() - start < 450) {
            await new Promise(requestAnimationFrame);
            const rect = element.getBoundingClientRect();
            frames.push({ width: rect.width, height: rect.height,
                text: element.querySelector('.picker-list button')!.getBoundingClientRect().height,
                bitmapStable: initialMap === image?.getAttribute('href'), ready: element.getAttribute('data-ready') === 'true' });
        }
        return frames;
    });
    expect(frames.every(frame => frame.ready && frame.bitmapStable)).toBe(true);
    for (const key of ['width', 'height', 'text'] as const) {
        expect(Math.max(...frames.map(frame => frame[key])) - Math.min(...frames.map(frame => frame[key]))).toBeLessThan(.01);
    }
    await page.screenshot({ path: 'test-results/picker-open-stable.png' });
});

test('quick reversals continue the current glass and text progress without an opacity restart', async ({ page }) => {
    await ready(page);
    await page.getByRole('button', { name: '选择业务 COM', exact: true }).click();
    const popup = page.locator('.picker-position');
    await expect(popup).toHaveAttribute('data-state', 'open');
    await popup.evaluate(element => Promise.all(element.getAnimations({ subtree: true }).map(animation => animation.finished)));
    const result = await page.evaluate(async () => {
        const menu = document.querySelector('.picker-position')!;
        const glass = menu.querySelector('.picker-window')!, text = menu.querySelector('.picker-content')!;
        const entering = { duration: getComputedStyle(glass, '::before').transitionDuration,
            curve: getComputedStyle(glass, '::before').transitionTimingFunction };
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        const until = performance.now() + 60;
        while (performance.now() < until) await new Promise(requestAnimationFrame);
        const before = parseFloat(getComputedStyle(text).opacity);
        const exiting = { duration: getComputedStyle(glass, '::before').transitionDuration,
            curve: getComputedStyle(glass, '::before').transitionTimingFunction };
        document.querySelector<HTMLButtonElement>('button[aria-label="选择业务 COM"]')!.click();
        const frames: { opacity: number; sheet: number; delay: string }[] = [];
        for (let i = 0; i < 10; i++) {
            await new Promise(requestAnimationFrame);
            frames.push({ opacity: parseFloat(getComputedStyle(text).opacity),
                sheet: (window as any).__glassReveal(glass),
                delay: getComputedStyle(text).transitionDelay });
        }
        return { before, entering, exiting, frames };
    });
    expect(result.entering).toEqual(result.exiting);
    expect(result.before).toBeGreaterThan(.05);
    expect(Math.min(...result.frames.map(frame => frame.opacity))).toBeGreaterThanOrEqual(result.before - .03);
    expect(result.frames[2].opacity).toBeGreaterThan(result.before);
    expect(result.frames.every(frame => frame.delay.split(',').every(delay => parseFloat(delay) === 0))).toBe(true);
    for (let i = 1; i < result.frames.length; i++) {
        expect(result.frames[i].opacity).toBeGreaterThanOrEqual(result.frames[i - 1].opacity - .001);
        expect(result.frames[i].sheet).toBeGreaterThanOrEqual(result.frames[i - 1].sheet - .001);
    }
    await expect(popup).toHaveAttribute('data-state', 'open');
    await page.keyboard.press('Escape');
    await expect(popup).toHaveCount(0);
});

test('disclosure animates both directions, reverses, and keeps collapsed fields inert', async ({ page }) => {
    await ready(page);
    const trigger = page.getByRole('button', { name: '高级参数', exact: true });
    const region = page.locator('.disclosure-grid');
    const frames = await trigger.evaluate(async button => {
        const grid = document.querySelector('.disclosure-grid')!;
        (button as HTMLButtonElement).click();
        const heights: number[] = [];
        for (let i = 0; i < 8; i++) { await new Promise(requestAnimationFrame); heights.push(grid.getBoundingClientRect().height); }
        return heights;
    });
    expect(frames.at(-1)!).toBeGreaterThan(frames[0] + 10);
    await expect(trigger).toHaveAttribute('aria-expanded', 'true');
    const timeout = page.getByLabel('COM 读超时 / s', { exact: true });
    await timeout.fill('0.25');
    const exit = await trigger.evaluate(async button => {
        const grid = document.querySelector('.disclosure-grid')!;
        (button as HTMLButtonElement).click();
        const start = grid.getBoundingClientRect().height;
        for (let i = 0; i < 5; i++) await new Promise(requestAnimationFrame);
        const during = grid.getBoundingClientRect().height;
        (button as HTMLButtonElement).click();
        return { start, during };
    });
    expect(exit.during).toBeLessThan(exit.start);
    expect(exit.during).toBeGreaterThan(0);
    await expect(trigger).toHaveAttribute('aria-expanded', 'true');
    await expect(timeout).toHaveValue('0.25');
    await trigger.click();
    await expect(region).toHaveAttribute('inert', '');
    await expect.poll(() => region.evaluate(element => element.getBoundingClientRect().height)).toBe(0);
});

test('confirmation remains a checkbox and only edits the pending device draft', async ({ page }) => {
    await ready(page);
    await page.getByRole('button', { name: '打开配置面板', exact: true }).click();
    await page.getByRole('group', { name: '配置分页', exact: true }).getByRole('button', { name: '电脑热点', exact: true }).click();
    const checkbox = page.getByRole('checkbox', { name: '开放网络 / 明确清空原密码', exact: true });
    await checkbox.locator('..').click();
    await expect(checkbox).toBeChecked();
    await expect(checkbox.locator('..').locator('.liquid-check-mark > svg')).toHaveCSS('opacity', '1');
    await checkbox.focus(); await page.keyboard.press('Space');
    await expect(checkbox).not.toBeChecked();
    expect(await page.evaluate(() => (window as any).__calls.some((c: any[]) => c[0] === 'save_device'))).toBe(false);
});

test('reduced motion and forced colors preserve the new controls', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await ready(page);
    const trigger = page.getByRole('button', { name: '高级参数', exact: true });
    await trigger.click();
    expect(await page.locator('.disclosure-grid').evaluate(element => parseFloat(getComputedStyle(element).transitionDuration))).toBeLessThan(.001);
    const toggle = page.getByRole('switch', { name: '调试日志', exact: true });
    await toggle.focus(); await page.keyboard.press('Space');
    await expect(toggle).toBeChecked();
    await page.emulateMedia({ forcedColors: 'active' });
    await expect(toggle).toBeVisible();
    await expect(toggle.locator('..').locator('.liquid-toggle-track')).toBeHidden();
    await page.keyboard.press('Space');
    await expect(toggle).not.toBeChecked();
});
