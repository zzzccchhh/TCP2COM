import { expect, test } from '@playwright/test';

test('desktop and phone pair, synchronize settings, reload and revoke a peer', async ({ browser }) => {
    const desktop = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    const a = await desktop.newPage(), b = await mobile.newPage();
    try {
        for (const [page, name] of [[a, '测试电脑'], [b, '测试手机']] as const) {
            await page.goto('/');
            await page.getByRole('button', { name: '局域网配对', exact: true }).click();
            await page.getByLabel('服务地址', { exact: true }).fill('http://127.0.0.1:8766');
            await page.getByLabel('终端名称', { exact: true }).fill(name);
            await page.getByLabel('配对码', { exact: true }).fill('TEST1234');
            await page.getByRole('button', { name: '配对并连接' }).click();
            await expect(page.getByText('已配对，所有终端共享设备状态。')).toBeVisible();
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
            await page.getByRole('button', { name: '关闭配对面板' }).click();
        }
        await a.getByLabel('业务 COM', { exact: true }).fill('COM88');
        await expect(b.getByLabel('业务 COM', { exact: true })).toHaveValue('COM88');
        await b.reload();
        await expect(b.getByLabel('业务 COM', { exact: true })).toHaveValue('COM88');
        await a.getByRole('button', { name: '局域网配对', exact: true }).click();
        await expect(a.getByRole('listitem').filter({ hasText: '测试手机' })).toBeVisible();
        await a.getByRole('listitem').filter({ hasText: '测试手机' }).getByRole('button', { name: '撤销配对' }).click();
        await expect(a.getByRole('listitem').filter({ hasText: '测试手机' })).toHaveCount(0);
        await expect(b.getByRole('status').filter({ hasText: '配对已失效' })).toBeVisible();
        await a.getByRole('button', { name: '关闭配对面板' }).click();
        await a.screenshot({ path: 'test-results/lan-desktop.png', fullPage: true });
        await b.screenshot({ path: 'test-results/lan-mobile.png', fullPage: true });
    } finally { await desktop.close(); await mobile.close(); }
});
