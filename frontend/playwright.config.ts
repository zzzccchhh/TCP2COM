import { defineConfig } from '@playwright/test';

export default defineConfig({
    testDir: './tests',
    fullyParallel: true,
    workers: 2,
    timeout: 30000,
    reporter: 'list',
    use: {
        baseURL: 'http://127.0.0.1:4173',
        channel: process.platform === 'win32' ? 'msedge' : undefined,
        viewport: { width: 1440, height: 1000 },
        trace: 'retain-on-failure',
    },
    webServer: [{
        command: 'npm run preview -- --port 4173 --strictPort',
        url: 'http://127.0.0.1:4173',
        reuseExistingServer: !process.env.CI,
    }, {
        command: process.platform === 'win32' ? 'py -3 -B -X utf8 ../tools/test-lan-server.py' : 'python3 -B ../tools/test-lan-server.py',
        url: 'http://127.0.0.1:8766',
        reuseExistingServer: false,
    }],
});
