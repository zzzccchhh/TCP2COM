import { useEffect } from 'react';
import type { RefObject } from 'react';

export function useReturnFocus(present: boolean, trigger?: RefObject<HTMLElement | null>) {
    useEffect(() => {
        if (!present) return;
        const previous = document.activeElement as HTMLElement | null;
        return () => {
            const active = document.activeElement;
            if (active?.isConnected && active !== document.body && active !== document.documentElement) return;
            const target = trigger?.current ?? previous;
            if (target?.isConnected) target.focus({ preventScroll: true });
        };
    }, [present, trigger]);
}
