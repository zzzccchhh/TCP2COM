import { expect, test } from '@playwright/test';

test('desktop and phone pair, synchronize settings, reload and revoke a peer', async ({ browser, request }) => {
    const desktop = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    const a = await desktop.newPage(), b = await mobile.newPage();
    try {
        for (const [page, name] of [[a, '测试电脑'], [b, '测试手机']] as const) {
            await page.goto('/#service=' + encodeURIComponent('127.0.0.1:8766'));
            await expect(page.getByLabel('服务地址', { exact: true })).toHaveValue('127.0.0.1:8766');
            await page.getByLabel('终端名称', { exact: true }).fill(name);
            await page.getByRole('button', { name: '请求配对', exact: true }).click();
            await expect(page.getByText(/等待服务端允许 · 请求编号/)).toBeVisible();
            await expect(page.getByText('已配对，所有终端共享设备状态。')).toHaveCount(0);
            expect((await request.post('http://127.0.0.1:8767', { data: { name } })).ok()).toBeTruthy();
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

test('pairing handles rejection, cancellation and expiry', async ({ page, request }) => {
    await page.goto('/#service=' + encodeURIComponent('http://127.0.0.1:8766/'));
    for (const [name, action, expected] of [
        ['拒绝测试', 'reject', '服务端已拒绝申请'],
        ['取消测试', 'cancel', '已取消配对申请'],
        ['超时测试', 'expire', '配对申请已取消或超时'],
    ]) {
        await page.getByLabel('终端名称', { exact: true }).fill(name);
        await page.getByRole('button', { name: '请求配对', exact: true }).click();
        await expect(page.getByText(/等待服务端允许 · 请求编号/)).toBeVisible();
        if (action === 'cancel') await page.getByRole('button', { name: '取消申请' }).click();
        else expect((await request.post('http://127.0.0.1:8767', { data: { name, allow: false, expire: action === 'expire' } })).ok()).toBeTruthy();
        await expect(page.getByRole('status').filter({ hasText: expected })).toBeVisible();
        await expect(page.getByRole('button', { name: '请求配对', exact: true })).toBeEnabled();
    }
});
