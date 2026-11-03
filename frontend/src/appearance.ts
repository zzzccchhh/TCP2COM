import { useSyncExternalStore } from 'react';

type Preference = 'light' | 'dark' | 'system';
const key = 'wifi-bridge-appearance';
const root = document.documentElement;
const system = matchMedia('(prefers-color-scheme: dark)');
const desktop = root.dataset.desktopAppearance === 'true';
const valid = (value: string | null | undefined): value is Preference => ['light', 'dark', 'system'].includes(value ?? '');
let preference: Preference = valid(root.dataset.appearance) ? root.dataset.appearance : 'light';
let pending: Promise<unknown> = Promise.resolve();
const listeners = new Set<() => void>();

function apply() {
    const theme = preference === 'system' ? (system.matches ? 'dark' : 'light') : preference;
    root.dataset.theme = theme;
    root.dataset.appearance = preference;
    root.style.colorScheme = theme;
    root.style.backgroundColor = theme === 'dark' ? '#08080a' : '#f1f0ea';
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', root.style.backgroundColor);
    listeners.forEach(listener => listener());
}
apply();
system.addEventListener('change', () => { if (preference === 'system') apply(); });
window.addEventListener('storage', event => {
    if (!desktop && (event.key === key || event.key === null)) {
        preference = valid(event.newValue) ? event.newValue : 'light'; apply();
    }
});

async function persistDesktop(value: Preference) {
    if (!window.pywebview?.api?.set_appearance) await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => { window.removeEventListener('pywebviewready', ready); reject(new Error()); }, 8000);
        function ready() { clearTimeout(timer); resolve(); }
        window.addEventListener('pywebviewready', ready, { once: true });
    });
    const reply = await window.pywebview!.api.set_appearance(value);
    if (!reply.ok) throw new Error(reply.error);
}

export function chooseAppearance(value: string): Promise<unknown> {
    if (!valid(value)) return Promise.resolve();
    preference = value; apply();
    if (desktop) {
        const fragment = new URLSearchParams(location.hash.slice(1));
        fragment.set('desktop-appearance', value);
        history.replaceState(null, '', location.pathname + location.search + '#' + fragment);
        // Serialize native writes so quick choices cannot persist an older selection last.
        pending = pending.catch(() => {}).then(() => persistDesktop(value));
        return pending;
    }
    try { localStorage.setItem(key, value); } catch { return Promise.reject(new Error()); }
    return Promise.resolve();
}

function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
export function useAppearance() { return useSyncExternalStore(subscribe, () => preference); }
