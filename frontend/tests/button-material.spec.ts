import { expect, test } from '@playwright/test';
import type { Locator, Page } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { installBridge } from './helpers';

async function settle(page: Page) { await page.evaluate(() => Promise.all(document.getAnimations().map(a => a.finished.catch(() => {})))); }
async function geometry(button: Locator) {
    return button.evaluate(button => {
        const box = button.getBoundingClientRect(), icon = button.querySelector('svg')!.getBoundingClientRect();
        const text = [...button.childNodes].find(node => node.nodeType === Node.TEXT_NODE && node.textContent?.trim())!;
        const range = document.createRange(); range.selectNode(text);
        const glyph = range.getBoundingClientRect();
        return { x: box.x, y: box.y, width: box.width, height: box.height,
            icon: { x: icon.x, y: icon.y, width: icon.width }, glyph: { x: glyph.x, y: glyph.y, width: glyph.width },
            scale: getComputedStyle(button).scale, filter: getComputedStyle(button).filter };
    });
}

for (const theme of ['light', 'dark']) for (const touch of [false, true]) {
    test(`${theme} ${touch ? 'phone' : 'desktop'} button lifts only its material and triggers one native action`, async ({ browser }) => {
        const directory = process.env.BUTTON_GEOMETRY_PREVIEW_DIR;
        if (directory) await mkdir(directory, { recursive: true });
        const viewport = touch ? { width: 390, height: 844 } : { width: 1440, height: 1000 };
        const context = await browser.newContext({ viewport, hasTouch: touch, isMobile: touch,
            ...(directory ? { recordVideo: { dir: join(directory, 'raw'), size: viewport } } : {}) });
        const page = await context.newPage(), video = page.video();
        try {
            await installBridge(page); await page.addInitScript(theme => localStorage.setItem('wifi-bridge-appearance', theme), theme);
            await page.goto('/'); await page.bringToFront(); await settle(page);
            const button = page.getByRole('button', { name: '启动桥接', exact: true });
            await button.scrollIntoViewIfNeeded(); await button.hover(); await settle(page);
            const before = await geometry(button), material = button.locator('.liquid-button-material');
            const resting = (await material.boundingBox())!;
            await expect(material).toHaveCSS('border-top-left-radius', await button.evaluate(button => getComputedStyle(button).borderTopLeftRadius));
            if (directory) await page.screenshot({ path: join(directory, `buttons-${theme}-${touch ? 'phone' : 'desktop'}-rest.png`) });
            const definitions = await page.locator('.liquid-glass-defs').evaluateAll(nodes => nodes.map(n => n.outerHTML));
            const cdp = touch ? await context.newCDPSession(page) : null;
            const box = (await button.boundingBox())!, point = { x: box.x + box.width * .3, y: box.y + box.height / 2 };
            if (cdp) await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [point] });
            else { await page.mouse.move(point.x, point.y); await page.mouse.down(); }
            await page.waitForTimeout(300);
            await expect(material).toHaveAttribute('data-lens-active', 'true');
            const held = (await material.boundingBox())!;
            expect(held.width / resting.width).toBeGreaterThan(1.03); expect(held.height / resting.height).toBeGreaterThan(1.08);
            expect(await geometry(button)).toEqual(before);
            if (directory) await page.screenshot({ path: join(directory, `buttons-${theme}-${touch ? 'phone' : 'desktop'}-held.png`) });
            await expect(material).toHaveAttribute('data-glass', 'refractive');
            expect(await page.locator('.liquid-glass-defs').evaluateAll(nodes => nodes.map(n => n.outerHTML))).toEqual(definitions);
            expect(await page.evaluate(() => (window as any).__calls.filter((c: any[]) => c[0] === 'bridge'))).toHaveLength(0);
            if (cdp) await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); else await page.mouse.up();
            await expect.poll(() => page.evaluate(() => (window as any).__calls.filter((c: any[]) => c[0] === 'bridge'))).toEqual([['bridge', 'start']]);
            await expect(material).not.toHaveAttribute('data-lens-active', 'true');
            expect(await geometry(button)).toEqual(before);
            await button.focus(); await page.keyboard.press('Space');
            await expect.poll(() => page.evaluate(() => (window as any).__calls.filter((c: any[]) => c[0] === 'bridge').length)).toBe(2);
        } finally { await context.close(); if (directory && video) await video.saveAs(join(directory, `buttons-${theme}-${touch ? 'phone' : 'desktop'}.webm`)); }
    });
}

test('cancelled pointer or keyboard presses never invoke the button', async ({ page }) => {
    await installBridge(page); await page.goto('/'); await page.bringToFront(); await settle(page);
    const button = page.getByRole('button', { name: '启动桥接', exact: true });
    await button.hover(); await page.mouse.down(); await page.keyboard.press('Escape'); await page.mouse.up();
    await button.focus(); await page.keyboard.down('Space'); await page.keyboard.press('Escape'); await page.keyboard.up('Space');
    await button.hover(); await page.mouse.down(); await page.mouse.move(2, 2); await page.mouse.up();
    await expect(button.locator('.liquid-button-material')).not.toHaveAttribute('data-lens-active', 'true');
    expect(await page.evaluate(() => (window as any).__calls.filter((c: any[]) => c[0] === 'bridge'))).toHaveLength(0);
    await button.click();
    expect(await page.evaluate(() => (window as any).__calls.filter((c: any[]) => c[0] === 'bridge'))).toEqual([['bridge', 'start']]);
});

test('touch scrolling cancels the material without starting the bridge', async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: 390, height: 700 }, hasTouch: true, isMobile: true });
    const page = await context.newPage();
    try {
        await installBridge(page); await page.goto('/'); await page.bringToFront();
        const button = page.getByRole('button', { name: '启动桥接', exact: true });
        await button.scrollIntoViewIfNeeded(); const box = (await button.boundingBox())!;
        const cdp = await context.newCDPSession(page), point = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [point] });
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: point.x, y: point.y - 80 }] });
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
        await expect(button.locator('.liquid-button-material')).not.toHaveAttribute('data-lens-active', 'true');
        expect(await page.evaluate(() => (window as any).__calls.filter((c: any[]) => c[0] === 'bridge'))).toHaveLength(0);
    } finally { await context.close(); }
});

test('disabled fieldsets and reduced motion preserve native operation', async ({ page }) => {
    await installBridge(page); await page.emulateMedia({ reducedMotion: 'reduce' }); await page.goto('/');
    const button = page.getByRole('button', { name: '启动桥接', exact: true });
    await button.evaluate(button => {
        const fieldset = document.createElement('fieldset'); fieldset.disabled = true;
        button.replaceWith(fieldset); fieldset.append(button); button.click();
    });
    expect(await page.evaluate(() => (window as any).__calls.filter((c: any[]) => c[0] === 'bridge'))).toHaveLength(0);
    await expect(button.locator('.liquid-button-material')).not.toHaveAttribute('data-lens-active', 'true');
    await button.evaluate(button => (button.parentElement as HTMLFieldSetElement).disabled = false);
    await button.click();
    expect(await page.evaluate(() => (window as any).__calls.filter((c: any[]) => c[0] === 'bridge'))).toHaveLength(1);
    await expect(button.locator('.liquid-button-material')).not.toHaveAttribute('data-lens-active', 'true');
});
