import { expect, test } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { installBridge } from './helpers';

for (const theme of ['light', 'dark']) for (const device of ['desktop', 'tablet', 'phone']) {
    test(`${theme} ${device} LAN card directly expands without a trigger seed or changing text and maps`, async ({ browser }) => {
        const directory = process.env.GLASS_EXPANSION_PREVIEW_DIR;
        if (directory) await mkdir(directory, { recursive: true });
        const viewport = device === 'desktop' ? { width: 1440, height: 1000 }
            : device === 'tablet' ? { width: 1024, height: 1366 } : { width: 390, height: 844 };
        const context = await browser.newContext({ viewport, hasTouch: device !== 'desktop', isMobile: device !== 'desktop',
            ...(directory ? { recordVideo: { dir: join(directory, 'raw'), size: viewport } } : {}) });
        const page = await context.newPage(), video = page.video();
        try {
            await page.addInitScript(theme => localStorage.setItem('wifi-bridge-appearance', theme), theme);
            await installBridge(page); await page.goto('/'); await page.bringToFront();
            await expect(page.getByText('本地运行', { exact: true })).toHaveCount(1);
            await page.evaluate(() => Promise.all(document.getAnimations().map(animation => animation.finished.catch(() => {}))));
            const trigger = page.getByRole('button', { name: '局域网连接', exact: true });
            if (device === 'desktop') await trigger.click(); else await trigger.tap();
            const panel = page.locator('.lan-panel');
            await expect(panel).toHaveAttribute('data-state', 'open');
            const frames = await panel.evaluate(async node => {
                const element = node as HTMLElement, text = element.querySelector('input')!;
                const id = element.style.getPropertyValue('--glass-refraction').match(/#([^"')]+)/)?.[1];
                const image = id ? document.getElementById(id)?.querySelector('feImage') : null;
                const initialMap = image?.getAttribute('href');
                const result: { p: number; w: number; h: number; contained: boolean; text: number; stable: boolean; transform: string; opacity: string }[] = [];
                const end = performance.now() + 450;
                while (performance.now() < end) {
                    const bounds = element.getBoundingClientRect(), shape = element.querySelector('.glass-expansion-outline')!.getBoundingClientRect();
                    result.push({ p: Number(element.style.getPropertyValue('--expansion-progress')), w: shape.width, h: shape.height,
                        contained: shape.left >= bounds.left - .1 && shape.right <= bounds.right + .1 && shape.top >= bounds.top - .1 && shape.bottom <= bounds.bottom + .1,
                        text: text.getBoundingClientRect().height, stable: image?.getAttribute('href') === initialMap,
                        transform: getComputedStyle(element, '::before').transform, opacity: getComputedStyle(element).opacity });
                    await new Promise(requestAnimationFrame);
                }
                return result;
            });
            expect(frames.filter(frame => frame.p > 0 && frame.p < .9).length).toBeGreaterThan(3);
            expect(frames.at(-1)!.w).toBeGreaterThan(frames[0].w * 2);
            expect(frames.at(-1)!.h).toBeGreaterThan(frames[0].h * 2);
            expect(frames.filter(frame => !frame.contained), 'outline must stay within the card').toEqual([]);
            expect(frames.every(frame => frame.stable), 'optical map must stay stable').toBe(true);
            expect(frames.every(frame => frame.transform === 'none' && frame.opacity === '1'), 'sampling plane must stay fixed').toBe(true);
            expect(Math.max(...frames.map(frame => frame.text)) - Math.min(...frames.map(frame => frame.text))).toBeLessThan(.01);
            await expect(panel.locator('.picker-morph')).toHaveCount(0);
            expect(await panel.locator('.glass-expansion-outline').evaluate(element => getComputedStyle(element, '::before').content)).toBe('none');
            if (directory) await page.screenshot({ path: join(directory, `expansion-${theme}-${device}.png`) });
            await page.getByLabel('终端名称', { exact: true }).fill('保留局域网输入');
            await page.getByRole('button', { name: '关闭连接面板', exact: true }).evaluate(button => button.click());
            await expect(panel).toHaveCount(0); await expect(trigger).toBeFocused();
            await trigger.click(); await expect(panel).toHaveAttribute('data-state', 'open');
            await expect(page.getByLabel('终端名称', { exact: true })).toHaveValue('保留局域网输入');
            await page.getByRole('button', { name: '关闭连接面板', exact: true }).evaluate(button => button.click());
            await expect(panel).toHaveCount(0);
            const arrow = page.getByRole('button', { name: '选择业务 COM', exact: true });
            await arrow.scrollIntoViewIfNeeded(); await arrow.click();
            await expect(page.locator('.picker-position')).toHaveAttribute('data-state', 'open');
            await page.evaluate(() => Promise.all(document.getAnimations().map(animation => animation.finished.catch(() => {}))));
            await page.keyboard.press('Escape'); await expect(page.locator('.picker-position')).toHaveCount(0);
            expect(await page.evaluate(() => (window as any).__calls.some((call: any[]) => ['start', 'save_device', 'read_device', 'usb_open'].includes(call[0])))).toBe(false);
        } finally { await context.close(); }
        if (directory && video) await video.saveAs(join(directory, `expansion-${theme}-${device}.webm`));
    });
}

test('LAN reduced motion interrupts entry and exits without retaining invisible layers', async ({ page }) => {
    await installBridge(page); await page.goto('/'); await page.bringToFront();
    await page.getByRole('button', { name: '局域网连接', exact: true }).click();
    const panel = page.locator('.lan-panel'); await expect(panel).toHaveAttribute('data-state', 'open');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    expect(await panel.evaluate(element => Number((element as HTMLElement).style.getPropertyValue('--expansion-progress')))).toBe(1);
    await page.getByRole('button', { name: '关闭连接面板', exact: true }).click();
    await expect(panel).toHaveCount(0);
    await page.getByRole('button', { name: '局域网连接', exact: true }).click();
    await expect(panel).toHaveAttribute('data-state', 'open');
    expect(await panel.evaluate(element => Number((element as HTMLElement).style.getPropertyValue('--expansion-progress')))).toBe(1);
});
