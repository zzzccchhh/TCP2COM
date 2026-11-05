import { expect, test } from '@playwright/test';
import { installBridge } from './helpers';

for (const width of [1440, 390]) test(`network log connects independently and config is editable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await installBridge(page, 3, false, true);
    await page.goto('/');
    await expect(page.getByText('本地运行', { exact: true })).toHaveCount(1);
    await page.getByLabel('日志设备 IPv4', { exact: true }).fill('192.168.4.1');
    await page.getByRole('button', { name: '连接网络日志', exact: true }).click();
    const panel = page.locator('[aria-label="网络日志连接"]');
    await expect(panel.locator('.badge')).toHaveText('已连接');
    expect(await page.evaluate(() => (window as any).__calls.some((c: any[]) => c[0] === 'bridge' || c[0] === 'usb'))).toBe(false);
    await page.getByRole('button', { name: '断开网络日志', exact: true }).click();
    await expect(panel.locator('.badge')).toHaveText('未连接');
    await page.getByRole('button', { name: '打开配置面板', exact: true }).click();
    const dialog = page.getByRole(width <= 760 ? 'region' : 'dialog', { name: '设备配置面板' });
    await dialog.getByRole('button', { name: '读取设备', exact: true }).click();
    const port = dialog.getByLabel('设备 TCP 日志监听端口', { exact: true });
    await expect(port).toHaveValue('19082');
    await port.fill('19090');
    expect(await page.evaluate(() => (window as any).__calls.filter((c: any[]) => c[0] === 'network_log').length)).toBe(2);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await expect(dialog.locator('input').first()).toBeVisible();
    await port.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `test-results/network-log-${width}.png` });
});

test('old firmware does not enable unsupported log settings and NET source stays distinct', async ({ page }) => {
    await installBridge(page);
    await page.goto('/');
    await page.getByRole('button', { name: '打开配置面板', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '设备配置面板' });
    await dialog.getByRole('button', { name: '读取设备', exact: true }).click();
    await expect(dialog.getByLabel('设备 TCP 日志监听端口', { exact: true })).toBeDisabled();
    await page.keyboard.press('Escape');
    await page.evaluate(() => { const state = (window as any).__bridgeState; state.logs.push({ id: 4, source: 'net', level: 'E', text: 'E (8) 网络日志验证', time: '12:00:03' }); state.cursor = 4; });
    await page.getByRole('button', { name: '运行日志', exact: true }).click();
    await page.getByRole('button', { name: 'ESP（网络）', exact: true }).click();
    await expect(page.locator('.log-row')).toHaveCount(1);
    await expect(page.locator('.log-origin')).toHaveText('NET');
    await expect(page.getByText('E (8) 网络日志验证', { exact: true })).toBeVisible();
});


test('saving an NVS log port never reconnects until the explicit reconnect action', async ({ page }) => {
    await installBridge(page, 3, false, true);
    await page.goto('/');
    await page.getByLabel('日志设备 IPv4', { exact: true }).fill('192.168.4.1');
    await page.getByRole('button', { name: '打开配置面板', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '设备配置面板' });
    await dialog.getByRole('button', { name: '读取设备', exact: true }).click();
    await dialog.getByLabel('设备 TCP 日志监听端口', { exact: true }).fill('19090');
    await dialog.getByRole('button', { name: '保存并重启', exact: true }).click();
    await page.getByRole('button', { name: '确认保存并重启', exact: true }).click();
    await expect(page.getByRole('dialog', { name: '确认保存设备配置' })).not.toBeVisible();
    expect(await page.evaluate(() => (window as any).__calls.filter((c: any[]) => c[0] === 'network_log').length)).toBe(0);
    const saves = await page.evaluate(() => (window as any).__calls.filter((c: any[]) => c[0] === 'save_device'));
    expect(saves).toHaveLength(1);
    expect(saves[0][1].log_port).toBe(19090);
    await expect(page.locator('.modal-backdrop')).toHaveCount(0);
    await dialog.getByRole('button', { name: '关闭面板', exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await page.getByRole('button', { name: '使用新端口重连', exact: true }).click();
    await expect(page.getByLabel('日志目标端口', { exact: true })).toHaveValue('19090');
    await expect(page.locator('[aria-label="网络日志连接"] .badge')).toHaveText('已连接');
    expect(await page.evaluate(() => (window as any).__calls.filter((c: any[]) => c[0] === 'network_log'))).toEqual([['network_log', true]]);
});
