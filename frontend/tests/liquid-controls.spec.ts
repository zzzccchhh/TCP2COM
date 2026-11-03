import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { installBridge } from './helpers';

async function ready(page: Page) {
    await installBridge(page);
    await page.goto('/');
    await expect(page.getByText('本地运行', { exact: true })).toHaveCount(1);
}

test('mouse lens follows the drag and commits exactly once on release', async ({ page }) => {
    await ready(page);
    const group = page.getByRole('group', { name: '电脑网络角色', exact: true });
    const client = group.getByRole('button', { name: 'TCP 服务端', exact: true });
    const udp = group.getByRole('button', { name: 'UDP', exact: true });
    const from = (await client.boundingBox())!, to = (await udp.boundingBox())!;
    const updates = () => page.evaluate(() => (window as any).__calls.filter((call: any[]) => call[0] === 'update_settings'));
    await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
    await page.mouse.down();
    await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 12 });
    await expect(group).toHaveAttribute('data-dragging', 'true');
    await expect(client).toHaveAttribute('aria-pressed', 'true');
    expect(await updates()).toHaveLength(0);
    const lens = (await group.locator('.liquid-indicator').boundingBox())!;
    expect(Math.abs(lens.x + lens.width / 2 - to.x - to.width / 2)).toBeLessThan(8);
    await page.screenshot({ path: 'test-results/liquid-drag-desktop.png' });
    await page.mouse.up();
    await expect(udp).toHaveAttribute('aria-pressed', 'true');
    await expect.poll(async () => (await updates()).length).toBe(1);
    expect(await page.evaluate(() => (window as any).__calls.some((call: any[]) => ['bridge', 'usb', 'save_device'].includes(call[0])))).toBe(false);
    await expect(group.locator('.liquid-indicator')).toHaveAttribute('data-glass', 'refractive');
});

test('hover previews and cancelled drags do not change the selected option', async ({ page }) => {
    await ready(page);
    const group = page.getByRole('group', { name: '电脑网络角色' });
    await group.getByRole('button', { name: 'UDP', exact: true }).hover();
    await expect(group.getByRole('button', { name: 'TCP 服务端' })).toHaveAttribute('aria-pressed', 'true');
    const box = (await group.boundingBox())!;
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await group.evaluate(element => element.dispatchEvent(new PointerEvent('pointercancel', { bubbles: true, pointerId: 1 })));
    await page.mouse.up();
    await expect(group.getByRole('button', { name: 'TCP 服务端' })).toHaveAttribute('aria-pressed', 'true');
    expect(await page.evaluate(() => (window as any).__calls.filter((call: any[]) => call[0] === 'update_settings'))).toHaveLength(0);
    await group.getByRole('button', { name: 'TCP 服务端' }).focus();
    await page.keyboard.press('End');
    await expect(group.getByRole('button', { name: 'UDP', exact: true })).toHaveAttribute('aria-pressed', 'true');
});

test('native-valued selects open a glass list and Escape closes only that list', async ({ page }) => {
    await ready(page);
    const device = page.getByLabel('当前设备', { exact: true });
    await device.click();
    await expect(page.getByRole('listbox', { name: '当前设备选项', exact: true })).toBeVisible();
    await page.getByRole('listbox', { name: '当前设备选项', exact: true }).getByRole('option', { name: 'ESP32-S3', exact: true }).click();
    await expect(device).toHaveValue('s3');
    await expect(device).toBeFocused();
    await page.getByRole('button', { name: '打开配置面板', exact: true }).click();
    const drawer = page.getByRole('dialog', { name: '设备配置面板', exact: true });
    const connection = drawer.getByLabel('MCU 接口', { exact: true });
    await connection.click();
    await expect(page.getByRole('listbox', { name: 'MCU 接口选项', exact: true })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('listbox', { name: 'MCU 接口选项', exact: true })).toHaveCount(0);
    await expect(drawer).toBeVisible();
    await connection.press('Space');
    await connection.press('ArrowDown');
    await connection.press('Enter');
    await expect(connection).toHaveValue('1');
    expect(await page.evaluate(() => (window as any).__calls.some((call: any[]) => ['read_device', 'save_device'].includes(call[0])))).toBe(false);
});

test('editable suggestions preserve custom baud and COM input', async ({ page }) => {
    await ready(page);
    await page.getByRole('button', { name: '选择业务 COM', exact: true }).click();
    await page.getByRole('option', { name: 'COM88 · USB Serial', exact: true }).click();
    await expect(page.getByLabel('业务 COM', { exact: true })).toHaveValue('COM88');
    const baud = page.getByLabel('波特率 / bps', { exact: true });
    await baud.fill('2500000');
    await expect(baud).toHaveValue('2500000');
    await expect(page.getByRole('listbox', { name: '波特率 / bps选项', exact: true })).toBeVisible();
    await baud.press('Enter');
    await expect(page.getByRole('listbox', { name: '波特率 / bps选项', exact: true })).toHaveCount(0);
    await expect(baud).toHaveValue('2500000');
    await page.getByRole('button', { name: '选择波特率 / bps', exact: true }).click();
    await page.getByRole('option', { name: '2000000', exact: true }).click();
    await expect(baud).toHaveValue('2000000');
});

test('drawer exits stay mounted through the transition and can reverse smoothly', async ({ page }) => {
    await ready(page);
    const help = page.getByRole('button', { name: '使用帮助', exact: true });
    await help.click();
    const backdrop = page.locator('.drawer-backdrop');
    await expect(backdrop).toHaveAttribute('data-state', 'open');
    await expect(backdrop).toHaveCSS('opacity', '1');
    await page.getByRole('button', { name: '关闭面板', exact: true }).evaluate(button => button.click());
    await expect(backdrop).toHaveAttribute('data-state', 'closed');
    await expect(backdrop).toBeAttached();
    await help.evaluate(button => button.click());
    await expect(backdrop).toHaveAttribute('data-state', 'open');
    await expect(backdrop).toHaveCount(1);
    await page.getByRole('button', { name: '关闭面板', exact: true }).click();
    await expect(backdrop).toHaveCount(0);
    await expect(help).toBeFocused();
    await expect(page.locator('body')).not.toHaveCSS('overflow', 'hidden');
});

test('becoming busy cancels a pending drag and closes the chooser without committing', async ({ page }) => {
    await ready(page);
    const group = page.getByRole('group', { name: '电脑网络角色', exact: true });
    const server = group.getByRole('button', { name: 'TCP 服务端', exact: true });
    await server.hover();
    await page.mouse.down();
    const udp = (await group.getByRole('button', { name: 'UDP', exact: true }).boundingBox())!;
    await page.mouse.move(udp.x + udp.width / 2, udp.y + udp.height / 2);
    await page.evaluate(() => { (window as any).__bridgeState.config_busy = true; });
    await expect(server).toBeDisabled();
    await page.mouse.up();
    await expect(server).toHaveAttribute('aria-pressed', 'true');
    await page.evaluate(() => { (window as any).__bridgeState.config_busy = false; });
    await expect(server).toBeEnabled();
    await page.getByRole('button', { name: '选择波特率 / bps', exact: true }).click();
    await expect(page.getByRole('listbox', { name: '波特率 / bps选项', exact: true })).toBeVisible();
    await page.evaluate(() => { (window as any).__bridgeState.config_busy = true; });
    await expect(page.getByRole('listbox', { name: '波特率 / bps选项', exact: true })).toHaveCount(0);
    expect(await page.evaluate(() => (window as any).__calls.filter((call: any[]) => call[0] === 'update_settings'))).toHaveLength(0);
});

test('glass menus fit a narrow viewport and release all instances after closing', async ({ page }) => {
    await ready(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator('svg[data-glass-kind=surface]')).toHaveCount(0);
    const baseline = await page.locator('.liquid-glass-defs').count();
    await page.getByRole('button', { name: '打开配置面板', exact: true }).click();
    await page.getByLabel('外部主控复位线', { exact: true }).click();
    const options = page.getByRole('listbox', { name: '外部主控复位线选项', exact: true });
    await expect(options).toBeVisible();
    const bounds = (await page.locator('.picker-position').boundingBox())!;
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(390);
    expect(bounds.y).toBeGreaterThanOrEqual(0);
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(845);
    await expect(page.locator('.drawer-backdrop')).toHaveCSS('opacity', '1');
    await expect(page.locator('.picker-position')).toHaveCSS('opacity', '1');
    await page.screenshot({ path: 'test-results/liquid-picker-mobile.png' });
    await page.keyboard.press('Escape');
    await page.keyboard.press('Escape');
    await expect(page.locator('.picker-position')).toHaveCount(0);
    await expect(page.locator('.drawer-backdrop')).toHaveCount(0);
    await expect(page.locator('.liquid-glass-defs')).toHaveCount(baseline);
});

test('touch dragging retains refraction and snaps to the chosen option', async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    try {
        const page = await context.newPage();
        await ready(page);
        const group = page.getByRole('group', { name: '电脑网络角色', exact: true });
        const from = (await group.getByRole('button', { name: 'TCP 服务端', exact: true }).boundingBox())!;
        const to = (await group.getByRole('button', { name: 'UDP', exact: true }).boundingBox())!;
        const cdp = await context.newCDPSession(page);
        const y = from.y + from.height / 2;
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: from.x + from.width / 2, y }] });
        for (let step = 1; step <= 8; step++) await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: from.x + from.width / 2 + (to.x + to.width / 2 - from.x - from.width / 2) * step / 8, y }] });
        await expect(group).toHaveAttribute('data-dragging', 'true');
        await expect(group.getByRole('button', { name: 'TCP 服务端', exact: true })).toHaveAttribute('aria-pressed', 'true');
        await expect(group.locator('.liquid-indicator')).toHaveAttribute('data-glass', 'refractive');
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
        await expect(group.getByRole('button', { name: 'UDP', exact: true })).toHaveAttribute('aria-pressed', 'true');
        const selected = (await group.getByRole('button', { name: 'UDP', exact: true }).boundingBox())!;
        const x = selected.x + selected.width / 2, startY = selected.y + selected.height / 2;
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y: startY }] });
        for (let step = 1; step <= 8; step++) await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x, y: startY - step * 12 }] });
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
        await expect(group.getByRole('button', { name: 'UDP', exact: true })).toHaveAttribute('aria-pressed', 'true');
        await expect(group).not.toHaveAttribute('data-dragging', 'true');
        await expect.poll(() => page.evaluate(() => scrollY)).toBeGreaterThan(0);
    } finally { await context.close(); }
});
