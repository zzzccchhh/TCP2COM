/* Blocking first-paint bootstrap; preference handling after startup lives in appearance.ts. */
(() => {
    const valid = value => ['light', 'dark', 'system'].includes(value);
    const fragment = new URLSearchParams(location.hash.slice(1));
    const desktop = fragment.get('desktop-appearance');
    let preference = valid(desktop) ? desktop : 'light';
    if (!valid(desktop)) {
        try {
            const stored = localStorage.getItem('wifi-bridge-appearance');
            if (valid(stored)) preference = stored;
        } catch { /* Appearance still works when storage is unavailable. */ }
    }
    const theme = preference === 'system' ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : preference;
    const root = document.documentElement;
    root.dataset.theme = theme;
    root.dataset.appearance = preference;
    root.dataset.desktopAppearance = String(valid(desktop));
    root.style.colorScheme = theme;
    root.style.backgroundColor = theme === 'dark' ? '#08080a' : '#f1f0ea';
    document.querySelector('meta[name="theme-color"]').setAttribute('content', root.style.backgroundColor);
})();
