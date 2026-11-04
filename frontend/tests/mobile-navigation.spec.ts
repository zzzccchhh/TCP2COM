import { expect, test } from '@playwright/test';
import type { Browser, Page } from '@playwright/test';
import { join } from 'node:path';
import { installBridge } from './helpers';

async function phone(browser: Browser, theme = 'light', record = false) {
    const directory = process.env.MOBILE_PAGE_PREVIEW_DIR;
    const viewport = { width: 390, height: 844 };
    const context = await browser.newContext({ viewport, hasTouch: true, isMobile: true,
        ...(directory && record ? { recordVideo: { dir: join(directory, 'raw'), size: viewport } } : {}) });
    const page = await context.newPage();
    await installBridge(page, 260);
    await page.addInitScript(theme => {
        localStorage.setItem('wifi-bridge-appearance', theme);
        if (!history.state) history.replaceState({ unrelated: 'keep' }, '');
    }, theme);
    await page.goto('/'); await page.bringToFront();
    await expect(page.locator('.local-indicator')).toHaveText('本地运行');
    return { page, context, video: page.video(), directory };
}
async function entered(page: Page) {
    await expect(page.locator('.mobile-page-layer')).toHaveAttribute('data-state', 'open');
    await expect.poll(() => page.locator('.mobile-secondary-page').evaluate(element =>
        Math.abs(new DOMMatrixReadOnly(getComputedStyle(element).transform).m41))).toBeLessThan(.1);
}
async function returned(page: Page) {
    await expect(page.locator('.drawer-backdrop')).toHaveCount(0);
    await expect(page.locator('body')).not.toHaveCSS('position', 'fixed');
    await expect(page.locator('.primary-page')).not.toHaveAttribute('inert', '');
}
async function pauseTravel(page: Page) {
    const pane = page.locator('.mobile-secondary-page');
    await expect.poll(() => pane.evaluate(element => element.getAnimations().length)).toBeGreaterThan(0);
    return page.evaluate(() => {
        const pane = document.querySelector('.mobile-secondary-page')!;
        const home = document.querySelector('.primary-page')!;
        [pane, home].forEach(element => element.getAnimations().forEach(animation => { animation.pause(); animation.currentTime = 110; }));
        const style = getComputedStyle(pane), matrix = new DOMMatrixReadOnly(style.transform);
        return { x: matrix.m41, scale: matrix.a, clip: style.clipPath, duration: style.transitionDuration,
            homeX: new DOMMatrixReadOnly(getComputedStyle(home).transform).m41 };
    });
}
async function resume(page: Page) {
    await page.evaluate(() => document.getAnimations().forEach(animation => { if (animation.playState === 'paused') animation.play(); }));
}

for (const theme of ['light', 'dark']) for (const destination of ['config', 'diagnostics', 'reset']) {
    test(`${theme} phone pushes and returns from ${destination} as a full page`, async ({ browser }) => {
        const { page, context, video, directory } = await phone(browser, theme, destination === 'config');
        try {
            const button = page.getByRole('button', { name: destination === 'config' ? '打开配置面板' : destination === 'reset' ? '打开远程复位' : '诊断', exact: true });
            await button.scrollIntoViewIfNeeded();
            const scroll = await page.evaluate(() => scrollY), url = page.url();
            const trigger = (await button.boundingBox())!;
            await page.touchscreen.tap(trigger.x + trigger.width / 2, trigger.y + trigger.height / 2);
            await expect(page.locator('.mobile-page-layer')).toHaveAttribute('data-state', 'open');
            const motion = await pauseTravel(page);
            expect(motion.x).toBeGreaterThan(0); expect(motion.x).toBeLessThan(390);
            expect(motion.homeX).toBeLessThan(0); expect(motion.scale).toBe(1);
            expect(motion.clip).toBe('none'); expect(motion.duration).toBe('0.32s');
            await expect(page.locator('.mobile-page-layer > .overlay-shade')).toBeHidden();
            await expect(page.locator('.mobile-page-layer')).toHaveCSS('backdrop-filter', 'none');
            await expect(page.locator('.primary-page')).toHaveAttribute('inert', '');
            await resume(page); await entered(page);
            const pane = page.getByRole('region', { name: destination === 'config' ? '设备配置面板' : destination === 'reset' ? '远程复位' : '链路诊断', exact: true });
            const box = (await pane.boundingBox())!;
            expect(box.x).toBe(0); expect(box.y).toBe(0); expect(box.width).toBe(390); expect(box.height).toBe(844);
            await page.screenshot({ path: `test-results/mobile-page-${theme}-${destination}.png` });
            expect(page.url()).toBe(url);
            expect(await page.evaluate(() => history.state.unrelated)).toBe('keep');
            const calls = await page.evaluate(() => (window as any).__calls.filter((call: any[]) => ['read_device', 'save_device', 'usb', 'reset_click'].includes(call[0])));
            expect(calls).toHaveLength(0);
            await pane.getByRole('button', { name: '返回工作台', exact: true }).evaluate(button => (button as HTMLButtonElement).click());
            await expect(page.locator('.mobile-page-layer')).toHaveAttribute('data-state', 'closed');
            const reverse = await pauseTravel(page);
            expect(reverse.x).toBeGreaterThan(0); expect(reverse.x).toBeLessThan(390);
            expect(reverse.scale).toBe(1); expect(reverse.clip).toBe('none'); expect(reverse.duration).toBe(motion.duration);
            await resume(page); await returned(page);
            expect(await page.evaluate(() => scrollY)).toBe(scroll);
            await expect(button).toBeFocused();
            expect(await page.evaluate(() => history.state.__wifiBridgePage)).toBeUndefined();
        } finally { await context.close(); }
        if (directory && video) await video.saveAs(join(directory, `mobile-page-${theme}.webm`));
    });
}

test('browser back dismisses the picker before returning, and forward restores the page', async ({ browser }) => {
    const { page, context } = await phone(browser);
    try {
        await page.getByRole('button', { name: '打开配置面板', exact: true }).click(); await entered(page);
        await page.getByLabel('MCU 接口', { exact: true }).click();
        await expect(page.getByRole('listbox')).toBeVisible();
        await page.goBack();
        await expect(page.getByRole('listbox')).toHaveCount(0);
        await expect(page.getByRole('region', { name: '设备配置面板', exact: true })).toBeVisible();
        expect(await page.evaluate(() => history.state.__wifiBridgePage.page)).toBe('config');
        await page.goBack(); await returned(page);
        await page.goForward(); await entered(page);
        await expect(page.getByRole('region', { name: '设备配置面板', exact: true })).toBeVisible();
        await page.getByRole('button', { name: '返回工作台', exact: true }).click(); await returned(page);
    } finally { await context.close(); }
});

test('browser back dismisses save confirmation without saving or leaving config', async ({ browser }) => {
    const { page, context } = await phone(browser);
    try {
        await page.getByRole('button', { name: '打开配置面板', exact: true }).click(); await entered(page);
        await page.getByRole('button', { name: '读取设备', exact: true }).click();
        await expect(page.getByRole('button', { name: '保存并重启', exact: true })).toBeEnabled();
        await page.getByRole('button', { name: '保存并重启', exact: true }).click();
        await expect(page.getByRole('dialog', { name: '确认保存设备配置', exact: true })).toBeVisible();
        await page.goBack();
        await expect(page.getByRole('dialog', { name: '确认保存设备配置', exact: true })).toHaveCount(0);
        await expect(page.getByRole('region', { name: '设备配置面板', exact: true })).toBeVisible();
        expect(await page.evaluate(() => (window as any).__calls.filter((call: any[]) => call[0] === 'save_device'))).toHaveLength(0);
        await page.goBack(); await returned(page);
    } finally { await context.close(); }
});

test('returning preserves config drafts, page scroll and the paused log view', async ({ browser }) => {
    const { page, context } = await phone(browser);
    try {
        await page.getByRole('button', { name: '运行日志', exact: true }).click();
        await expect(page.locator('[data-sliding]')).toHaveCount(0);
        await page.getByRole('button', { name: '暂停自动滚动', exact: true }).click();
        await page.getByRole('log').evaluate(element => { element.scrollTop = 145; });
        await page.getByRole('button', { name: '打开配置面板', exact: true }).click(); await entered(page);
        await page.getByRole('button', { name: '电脑热点', exact: true }).click();
        await page.getByLabel('SSID', { exact: true }).fill('phone-draft');
        await page.getByLabel('新密码（留空保留）', { exact: true }).fill('draft-only');
        await page.getByRole('button', { name: '接口与联网', exact: true }).click();
        await expect(page.locator('[data-sliding]')).toHaveCount(0);
        await page.locator('.drawer-body').evaluate(element => { element.scrollTop = 350; });
        const offset = await page.locator('.drawer-body').evaluate(element => element.scrollTop);
        expect(offset).toBeGreaterThan(0);
        await page.getByRole('button', { name: '返回工作台', exact: true }).click(); await returned(page);
        expect(await page.getByRole('log').evaluate(element => element.scrollTop)).toBe(145);
        await page.getByRole('button', { name: '打开配置面板', exact: true }).click(); await entered(page);
        expect(await page.locator('.drawer-body').evaluate(element => element.scrollTop)).toBe(offset);
        await page.getByRole('button', { name: '电脑热点', exact: true }).click();
        await expect(page.getByLabel('SSID', { exact: true })).toHaveValue('phone-draft');
        await expect(page.getByLabel('新密码（留空保留）', { exact: true })).toHaveValue('draft-only');
    } finally { await context.close(); }
});

test('quick return and reopen retains one page and one history level', async ({ browser }) => {
    const { page, context } = await phone(browser);
    try {
        await page.getByRole('button', { name: '打开配置面板', exact: true }).click(); await entered(page);
        const count = await page.evaluate(() => history.length);
        await page.evaluate(() => {
            (document.querySelector('.page-back') as HTMLButtonElement).click();
            ([...document.querySelectorAll('.primary-page button')].find(button => button.textContent?.includes('打开配置面板')) as HTMLButtonElement).click();
        });
        await entered(page);
        await expect(page.locator('.mobile-secondary-page')).toHaveCount(1);
        expect(await page.evaluate(() => history.length)).toBe(count);
        await page.goBack(); await returned(page);
        await expect(page.getByRole('button', { name: '打开配置面板', exact: true })).toBeFocused();
    } finally { await context.close(); }
});

test('phone page stays full screen through rotation and shorter keyboard-sized viewports', async ({ browser }) => {
    const { page, context } = await phone(browser, 'dark');
    try {
        await page.getByRole('button', { name: '诊断', exact: true }).click(); await entered(page);
        for (const viewport of [{ width: 844, height: 390 }, { width: 390, height: 430 }, { width: 430, height: 932 }]) {
            await page.setViewportSize(viewport);
            const body = page.locator('.mobile-secondary-page');
            await expect.poll(async () => Math.round((await body.boundingBox())!.height)).toBe(viewport.height);
            expect((await body.boundingBox())!.width).toBe(viewport.width);
            await expect(page.getByRole('button', { name: '返回工作台', exact: true })).toBeInViewport();
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        }
        await page.getByRole('button', { name: '返回工作台', exact: true }).click(); await returned(page);
        await page.setViewportSize({ width: 844, height: 390 });
        await page.goForward();
        await expect(page.getByRole('dialog', { name: '链路诊断', exact: true })).toBeVisible();
        await expect(page.locator('.mobile-page-layer')).toHaveCount(0);
        await page.goBack(); await returned(page);
    } finally { await context.close(); }
});

test('reload retains the secondary level and reduced motion disables travel', async ({ browser }) => {
    const { page, context } = await phone(browser);
    try {
        await page.emulateMedia({ reducedMotion: 'reduce' });
        await page.getByRole('button', { name: '诊断', exact: true }).click(); await entered(page);
        await expect(page.locator('.mobile-secondary-page')).toHaveCSS('transition-duration', '0s');
        await page.reload(); await entered(page);
        await expect(page.getByRole('region', { name: '链路诊断', exact: true })).toBeVisible();
        await page.goBack(); await returned(page);
    } finally { await context.close(); }
});

test('enabling reduced motion during return immediately releases the home page', async ({ browser }) => {
    const { page, context } = await phone(browser);
    try {
        await page.getByRole('button', { name: '诊断', exact: true }).click(); await entered(page);
        await page.getByRole('button', { name: '返回工作台', exact: true }).evaluate(button => (button as HTMLButtonElement).click());
        await expect(page.locator('.mobile-page-layer')).toHaveAttribute('data-state', 'closed');
        await page.emulateMedia({ reducedMotion: 'reduce' });
        await expect(page.locator('.mobile-page-layer')).toHaveCount(0, { timeout: 200 });
        await returned(page);
    } finally { await context.close(); }
});
