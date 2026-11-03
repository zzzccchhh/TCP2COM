import { expect, test } from '@playwright/test';

test('desktop refraction changes the rendered backdrop without filtering text', async ({ page }) => {
    await page.goto('/');
    const card = page.locator('.connection-panel');
    await expect(card).toHaveAttribute('data-glass', 'refractive');
    // A high-contrast background makes a silently unsupported SVG filter observable.
    await page.addStyleTag({ content: `
        body { background: repeating-linear-gradient(110deg, #6b8ab5 0 18px, #f4f7ff 18px 36px); }
        *, *::before, *::after { animation: none !important; transition: none !important; }
    ` });
    await page.evaluate(() => document.fonts.ready);
    const settle = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await settle();
    const refracted = await card.screenshot();
    await card.evaluate(element => element.style.setProperty('--glass-refraction', 'blur(12px) saturate(1.35)'));
    await settle();
    const frosted = await card.screenshot();
    expect(refracted.equals(frosted), 'SVG refraction should visibly change the backdrop').toBe(false);
    expect(await card.locator('.panel-title').evaluate(element => getComputedStyle(element).filter)).toBe('none');
    const license = await page.request.get('/third-party-licenses.txt');
    expect(license.ok()).toBe(true);
    expect(await license.text()).toContain('Copyright (c) 2025 Shu Ding');
});

test('resizing and reopening panels release optical resources and preserve input', async ({ page }) => {
    // Three animated overlay cycles, large screenshots and two viewport changes share this test.
    test.setTimeout(60000);
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('/');
    const defs = page.locator('svg.liquid-glass-defs[data-glass-kind="surface"]');
    await expect(defs).toHaveCount(5);
    await expect(page.getByText('浏览器界面预览', { exact: false })).toBeVisible();
    await page.screenshot({ path: 'test-results/glass-desktop.png', fullPage: true });
    const com = page.getByLabel('业务 COM', { exact: true });
    await com.fill('COM91');
    await page.getByText('高级参数', { exact: true }).click();
    await expect.poll(() => page.locator('.connection-panel').evaluate(element => {
        const value = (element as HTMLElement).style.getPropertyValue('--glass-refraction');
        const id = value.match(/#([^"\)]+)/)?.[1];
        return id ? Number(document.getElementById(id)?.getAttribute('height')) === (element as HTMLElement).offsetHeight : false;
    })).toBe(true);
    for (let i = 0; i < 3; i++) {
        await page.getByRole('button', { name: '局域网连接', exact: true }).click();
        await expect(defs).toHaveCount(6);
        await page.getByRole('button', { name: '关闭连接面板', exact: true }).click();
        await expect(defs).toHaveCount(5);
        await page.getByRole('button', { name: '打开配置面板', exact: true }).click();
        await expect(defs).toHaveCount(6);
        if (i === 0) await page.screenshot({ path: 'test-results/glass-config.png', animations: 'disabled' });
        await page.keyboard.press('Escape');
        await expect(defs).toHaveCount(5);
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(defs).toHaveCount(0);
    await expect(page.locator('.connection-panel')).toHaveAttribute('data-glass', 'frosted');
    await expect(com).toHaveValue('COM91');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.getByText('高级参数', { exact: true }).click();
    await page.screenshot({ path: 'test-results/glass-mobile.png', fullPage: true });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await expect(defs).toHaveCount(5);
    await expect(com).toHaveValue('COM91');
    expect(errors).toEqual([]);
});

test('unsupported filters and reduced motion keep controls usable', async ({ page }) => {
    await page.addInitScript(() => {
        const supports = CSS.supports.bind(CSS);
        CSS.supports = ((property: string, value?: string) => {
            if (property === 'backdrop-filter' && value?.includes('url(')) return false;
            return value === undefined ? supports(property) : supports(property, value);
        }) as typeof CSS.supports;
    });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto('/');
    await expect(page.locator('.connection-panel')).toHaveAttribute('data-glass', 'frosted');
    await expect(page.locator('svg.liquid-glass-defs')).toHaveCount(0);
    await page.getByLabel('业务 COM', { exact: true }).fill('COM92');
    await page.locator('.connection-panel').hover();
    expect(await page.locator('.connection-panel').evaluate(element => (element as HTMLElement).style.getPropertyValue('--glass-light-x'))).toBe('');
    const button = page.getByRole('button', { name: '局域网连接', exact: true });
    await button.hover();
    await page.mouse.down();
    try { await expect(button).toHaveCSS('scale', 'none'); }
    finally { await page.mouse.up(); }
    await expect(page.getByLabel('服务地址', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: '关闭连接面板', exact: true }).click();
    await expect(page.getByLabel('业务 COM', { exact: true })).toHaveValue('COM92');
});
