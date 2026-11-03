import { useLayoutEffect } from 'react';
import type { RefObject } from 'react';

/** Preserve the displayed rectangle while a retained layer exits, including viewport/keyboard changes. */
export function useExitBounds(open: boolean, present: boolean, element: RefObject<HTMLElement | null>) {
    useLayoutEffect(() => {
        const node = element.current;
        if (open || !present || !node) return;
        const rect = node.getBoundingClientRect();
        const frozen: Record<string, string> = {
            position: 'fixed', left: `${rect.left}px`, top: `${rect.top}px`, right: 'auto', bottom: 'auto',
            width: `${rect.width}px`, height: `${rect.height}px`, 'max-height': 'none', overflow: 'hidden',
        };
        const previous = Object.keys(frozen).map(key => [key, node.style.getPropertyValue(key)]);
        Object.entries(frozen).forEach(([key, value]) => node.style.setProperty(key, value));
        return () => previous.forEach(([key, value]) => {
            if (value) node.style.setProperty(key, value); else node.style.removeProperty(key);
        });
    }, [open, present, element]);
}
