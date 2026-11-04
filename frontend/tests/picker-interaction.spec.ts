import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { installBridge } from './helpers';

async function settle(page: Page) {
    await page.evaluate(() => Promise.all(document.getAnimations().map(animation => animation.finished.catch(() => {}))));
}
async function ready(page: Page) {
    await installBridge(page); await page.goto('/'); await page.bringToFront();
    await expect(page.getByText('本地运行', { exact: true })).toHaveCount(1); await settle(page);
}
async function browsing(page: Page) {
    const input = page.getByLabel('业务 COM', { exact: true });
    await expect(input).toHaveJSProperty('readOnly', true); await expect(input).toHaveAttribute('inputmode', 'none');
    await expect(input).not.toBeFocused();
}

for (const theme of ['light', 'dark']) for (const touch of [false, true]) {
    test(`${theme} ${touch ? 'phone' : 'desktop'} arrow only browses, data enters editing, selection returns to browsing`, async ({ browser }) => {
        const directory = process.env.PICKER_PREVIEW_DIR;
        if (directory) await mkdir(directory, { recursive: true });
        const viewport = touch ? { width: 390, height: 844 } : { width: 1440, height: 1000 };
        const context = await browser.newContext({ viewport, hasTouch: touch, isMobile: touch,
            ...(directory ? { recordVideo: { dir: join(directory, 'raw'), size: viewport } } : {}) });
        const page = await context.newPage(), video = page.video();
        try {
            await page.addInitScript(theme => localStorage.setItem('wifi-bridge-appearance', theme), theme);
            await ready(page);
            const input = page.getByLabel('业务 COM', { exact: true }), arrow = page.getByRole('button', { name: '选择业务 COM', exact: true });
            await arrow.scrollIntoViewIfNeeded();
            await page.evaluate(() => {
                (window as any).__editingFocus = [];
                document.addEventListener('focusin', event => {
                    if (event.target instanceof HTMLInputElement && event.target.getAttribute('aria-label') === '业务 COM') {
                        (window as any).__editingFocus.push({ readOnly: event.target.readOnly, mode: event.target.inputMode });
                    }
                });
            });
            if (touch) await arrow.tap(); else await arrow.click();
            const popup = page.locator('.picker-position');
            await expect(popup).toHaveAttribute('data-state', 'open'); await settle(page); await browsing(page);
            await expect(page.getByRole('listbox', { name: '业务 COM选项', exact: true })).toBeFocused();
            expect(await page.evaluate(() => (window as any).__editingFocus)).toEqual([]);
            if (directory) await page.screenshot({ path: join(directory, `picker-${theme}-${touch ? 'phone' : 'desktop'}-open.png`) });
            if (touch) await page.getByRole('option', { name: 'COM88 · USB Serial', exact: true }).tap();
            else await page.getByRole('option', { name: 'COM88 · USB Serial', exact: true }).click();
            await expect(input).toHaveValue('COM88'); await expect(popup).toHaveCount(0); await browsing(page);
            if (touch) await input.tap(); else await input.click();
            await expect(input).toBeEditable(); await expect(input).toBeFocused(); await expect(input).toHaveAttribute('inputmode', 'text');
            expect(await page.evaluate(() => (window as any).__editingFocus.at(-1))).toEqual({ readOnly: false, mode: 'text' });
            await input.fill('COM777');
            if (touch) await arrow.tap(); else await arrow.click();
            await browsing(page); await expect(popup).toHaveAttribute('data-state', 'open');
            await expect(page.getByRole('option', { name: 'COM88 · USB Serial', exact: true })).toBeVisible();
            await page.keyboard.press('Escape'); await expect(popup).toHaveCount(0); await expect(input).toHaveValue('COM777');
            expect(await page.evaluate(() => (window as any).__calls.some((c: any[]) => ['bridge', 'usb', 'read_device', 'save_device'].includes(c[0])))).toBe(false);
        } finally {
            await context.close();
            if (directory && video) await video.saveAs(join(directory, `picker-${theme}-${touch ? 'phone' : 'desktop'}.webm`));
        }
    });
}

test('keyboard browsing, explicit F2 editing, partial matches and custom values preserve their intent', async ({ page }) => {
    await ready(page);
    const input = page.getByLabel('业务 COM', { exact: true }), arrow = page.getByRole('button', { name: '选择业务 COM', exact: true });
    await input.focus(); await page.keyboard.press('F2'); await page.keyboard.press('Enter');
    await expect(input).toHaveValue('COM1'); await browsing(page);
    await arrow.focus(); await page.keyboard.press('ArrowDown'); await browsing(page);
    await page.keyboard.press('Enter'); await expect(input).toHaveValue('COM88'); await browsing(page);
    await input.focus(); await page.keyboard.press('F2'); await expect(input).toBeEditable();
    await input.fill('COM'); await page.keyboard.press('ArrowDown'); await page.keyboard.press('Enter');
    await expect(input).toHaveValue('COM88'); await browsing(page);
    await input.focus(); await page.keyboard.press('F2'); await input.fill('COM779'); await page.keyboard.press('Enter');
    await expect(input).toHaveValue('COM779'); await browsing(page);
    await input.focus(); await page.keyboard.press('F2'); await page.keyboard.press('Tab');
    await expect(input).toHaveJSProperty('readOnly', true); await expect(page.locator('.picker-position')).toHaveCount(0);
});

test('label, focus alone and outside dismissal do not start editing or steal focus', async ({ page }) => {
    await ready(page);
    const input = page.getByLabel('业务 COM', { exact: true });
    await page.getByText('业务 COM', { exact: true }).click();
    await expect(input).toHaveJSProperty('readOnly', true); await expect(input).toHaveAttribute('inputmode', 'none');
    await page.getByRole('button', { name: '选择业务 COM', exact: true }).click();
    await page.getByLabel('电脑本地端口', { exact: true }).click();
    await expect(page.locator('.picker-position')).toHaveCount(0);
    await expect(page.getByLabel('电脑本地端口', { exact: true })).toBeFocused();
    await input.focus(); await expect(input).toHaveJSProperty('readOnly', true);
});

test('card expands directly within menu bounds and reuses its optical map', async ({ page }) => {
    await ready(page);
    await page.getByRole('button', { name: '选择业务 COM', exact: true }).click();
    const popup = page.locator('.picker-position');
    await expect(popup).toHaveAttribute('data-state', 'open');
    const result = await popup.evaluate(async element => {
        const glass = element.querySelector<HTMLElement>('.picker-window')!, morph = element.querySelector('.glass-expansion-outline')!;
        const button = element.querySelector('.picker-list button')!;
        const input = document.querySelector('input[aria-label="业务 COM"]')!.getBoundingClientRect();
        const id = glass.style.getPropertyValue('--glass-refraction').match(/#([^"')]+)/)![1];
        const image = document.getElementById(id)!.querySelector('feImage')!, map = image.getAttribute('href');
        const frames: { p: number; width: number; height: number; x: number; top: number; bottom: number; nativeHeight: number; bitmap: boolean; sampling: string }[] = [];
        const start = performance.now();
        while (performance.now() - start < 450) {
            const rect = morph.getBoundingClientRect();
            frames.push({ p: Number((element as HTMLElement).style.getPropertyValue('--expansion-progress')), width: rect.width, height: rect.height,
                x: rect.x, top: rect.top, bottom: rect.bottom, nativeHeight: button.getBoundingClientRect().height,
                bitmap: image.getAttribute('href') === map, sampling: getComputedStyle(glass, '::before').transform });
            await new Promise(requestAnimationFrame);
        }
        const menu = element.getBoundingClientRect();
        return { frames, input: { x: input.x, right: input.right, bottom: input.bottom }, menu: { x: menu.x, top: menu.top, bottom: menu.bottom, right: menu.right } };
    });
    expect(result.frames.filter(frame => frame.p > .3 && frame.p < .9).length).toBeGreaterThan(3);
    expect(result.frames.at(-1)!.width).toBeGreaterThan(result.frames[0].width * 3);
    expect(result.frames.at(-1)!.height).toBeGreaterThan(result.frames[0].height * 2);
    expect(result.frames.every(frame => frame.bitmap && frame.sampling === 'none')).toBe(true);
    expect(Math.max(...result.frames.map(frame => frame.nativeHeight)) - Math.min(...result.frames.map(frame => frame.nativeHeight))).toBeLessThan(.01);
    expect(result.frames.every(frame => frame.top >= result.menu.top - .1 && frame.x >= result.menu.x - .1 && frame.x + frame.width <= result.menu.right + .1 && frame.bottom <= result.menu.bottom + .1)).toBe(true);
    await expect(popup.locator('.picker-morph')).toHaveCount(0);
});

test('viewport changes while browsing retain the origin and reduced motion settles an interrupted morph', async ({ page }) => {
    await ready(page); await page.setViewportSize({ width: 390, height: 650 });
    const arrow = page.getByRole('button', { name: '选择业务 COM', exact: true });
    await arrow.scrollIntoViewIfNeeded(); await arrow.click();
    const popup = page.locator('.picker-position'); await expect(popup).toHaveAttribute('data-state', 'open');
    await page.setViewportSize({ width: 844, height: 390 }); await settle(page); await browsing(page);
    const rect = (await popup.boundingBox())!;
    expect(rect.x).toBeGreaterThanOrEqual(11); expect(rect.x + rect.width).toBeLessThanOrEqual(833);
    await page.keyboard.press('Escape');
    await page.emulateMedia({ reducedMotion: 'reduce' }); await expect(popup).toHaveCount(0);
    await arrow.click(); await expect(popup).toHaveAttribute('data-state', 'open');
    expect(await popup.evaluate(element => Number((element as HTMLElement).style.getPropertyValue('--expansion-progress')))).toBe(1);
    await page.keyboard.press('Escape'); await expect(popup).toHaveCount(0);
});
