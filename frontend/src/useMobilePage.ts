import { useCallback, useLayoutEffect, useRef, useState } from 'react';

export type Drawer = 'config' | 'diagnostics' | 'reset' | 'help';
type Page = 'config' | 'diagnostics' | 'reset';
type Entry = { owner: string; page: Page };
const key = '__wifiBridgePage';
const narrow = () => matchMedia('(max-width: 760px)').matches;
const isPage = (value: unknown): value is Page => value === 'config' || value === 'diagnostics' || value === 'reset';
const entry = (): Entry | undefined => history.state?.[key];

/** Navigation uses history state only, leaving pairing URLs and appearance fragments intact. */
export function useMobilePage(drawer: Drawer | null, update: (value: Drawer | null) => void,
    confirmation: boolean, dismissConfirmation: () => void) {
    const [mobile, setMobile] = useState(false);
    // LAN pages may use plain HTTP, where crypto.randomUUID is unavailable.
    const owner = useRef(`page-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    const registered = useRef(false);
    const pendingBack = useRef(false);
    const queued = useRef<{ page: Drawer; source?: HTMLElement } | null>(null);
    const trigger = useRef<HTMLElement | null>(null);
    const current = useRef({ drawer, confirmation, dismissConfirmation, mobile });
    const offsets = useRef(new Map<Drawer, number>());
    useLayoutEffect(() => { current.current = { drawer, confirmation, dismissConfirmation, mobile }; });

    const remember = useCallback(() => {
        const page = current.current.drawer;
        const body = document.querySelector('.mobile-secondary-page .drawer-body');
        if (page && body) offsets.current.set(page, body.scrollTop);
    }, []);
    const dismissTop = useCallback(() => {
        if (!window.dispatchEvent(new Event('mobile-page-back', { cancelable: true }))) return true;
        if (current.current.confirmation) { current.current.dismissConfirmation(); return true; }
        return false;
    }, []);

    const navigate = useCallback((next: Drawer | null, source?: HTMLElement) => {
        if (next === null) {
            if (current.current.mobile && dismissTop()) return;
            remember(); update(null);
            if (entry()?.owner === owner.current && !pendingBack.current) {
                pendingBack.current = true; history.back();
            }
            return;
        }
        if (pendingBack.current) { queued.current = { page: next, source }; return; }
        trigger.current = source ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null);
        const mobile = narrow() && isPage(next);
        setMobile(mobile);
        if (mobile) {
            registered.current = true;
            const state = { ...history.state, [key]: { owner: owner.current, page: next } };
            if (entry()?.owner === owner.current) history.replaceState(state, '');
            else history.pushState(state, '');
        }
        update(next);
    }, [dismissTop, remember, update]);

    useLayoutEffect(() => {
        // Reload retains the page level, while React/device state initializes normally.
        const saved = entry();
        if (saved && isPage(saved.page)) {
            registered.current = true;
            history.replaceState({ ...history.state, [key]: { owner: owner.current, page: saved.page } }, '');
            setMobile(narrow()); update(saved.page);
        }
        function pop() {
            if (pendingBack.current) {
                pendingBack.current = false; registered.current = false;
                const next = queued.current; queued.current = null;
                if (next) navigate(next.page, next.source);
                return;
            }
            const next = entry();
            if (next?.owner === owner.current && isPage(next.page)) {
                registered.current = true;
                if (!current.current.drawer || !current.current.mobile) setMobile(narrow());
                update(next.page);
            } else if (current.current.drawer && registered.current) {
                if (dismissTop()) {
                    history.pushState({ ...history.state, [key]: { owner: owner.current, page: current.current.drawer } }, '');
                } else { registered.current = false; remember(); update(null); }
            }
        }
        window.addEventListener('popstate', pop);
        return () => window.removeEventListener('popstate', pop);
    }, [dismissTop, remember, update, navigate]);

    useLayoutEffect(() => {
        if (!mobile || !drawer) return;
        const body = document.querySelector('.mobile-secondary-page .drawer-body');
        if (body) body.scrollTop = offsets.current.get(drawer) ?? 0;
    }, [drawer, mobile]);
    return { mobile, navigate, trigger };
}

/** Freeze the actual home page at its current scroll position, including on iOS. */
export function useMobilePageViewport(active: boolean) {
    useLayoutEffect(() => {
        if (!active) return;
        const body = document.body, x = scrollX, y = scrollY;
        const previous = { position: body.style.position, top: body.style.top, left: body.style.left, width: body.style.width };
        const restoration = history.scrollRestoration;
        history.scrollRestoration = 'manual';
        Object.assign(body.style, { position: 'fixed', top: `${-y}px`, left: `${-x}px`, width: '100%' });
        function resize() {
            const layer = document.querySelector<HTMLElement>('.mobile-page-layer');
            const viewport = window.visualViewport;
            layer?.style.setProperty('--page-height', `${viewport?.height ?? innerHeight}px`);
            layer?.style.setProperty('--page-top', `${viewport?.offsetTop ?? 0}px`);
        }
        resize();
        window.addEventListener('resize', resize);
        window.visualViewport?.addEventListener('resize', resize);
        window.visualViewport?.addEventListener('scroll', resize);
        return () => {
            window.removeEventListener('resize', resize);
            window.visualViewport?.removeEventListener('resize', resize);
            window.visualViewport?.removeEventListener('scroll', resize);
            Object.assign(body.style, previous);
            window.scrollTo(x, y);
            history.scrollRestoration = restoration;
        };
    }, [active]);
}
