import { useLayoutEffect, useState } from 'react';

/** Retain content through exit; CSS transitions can reverse if reopened mid-flight. */
export function usePresence<T>(value: T | null, duration = 380) {
    const [retained, setRetained] = useState<T | null>(value);
    const [entered, setEntered] = useState(false);
    useLayoutEffect(() => {
        let first = 0, second = 0;
        let timer: ReturnType<typeof setTimeout> | undefined;
        if (value !== null) {
            setRetained(value);
            first = requestAnimationFrame(() => { second = requestAnimationFrame(() => setEntered(true)); });
        } else {
            setEntered(false);
            const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
            timer = setTimeout(() => setRetained(null), reduced ? 0 : duration);
        }
        return () => { cancelAnimationFrame(first); cancelAnimationFrame(second); clearTimeout(timer); };
    }, [value, duration]);
    return { value: value ?? retained, present: value !== null || retained !== null, entered: value !== null && entered };
}
