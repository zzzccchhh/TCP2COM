import { useCallback, useLayoutEffect, useRef, useState } from 'react';

/** Retain content through exit; CSS transitions can reverse if reopened mid-flight. */
export function usePresence<T>(value: T | null, duration = 380, ready = true) {
    const [retained, setRetained] = useState<T | null>(value);
    const [entered, setEntered] = useState(false);
    const hasEntered = useRef(false);
    const valueRef = useRef(value);
    const exitTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
    const canEnter = value !== null && ready;
    useLayoutEffect(() => { valueRef.current = value; }, [value]);
    const finishExit = useCallback(() => {
        if (valueRef.current !== null) return;
        clearTimeout(exitTimer.current);
        hasEntered.current = false;
        setRetained(null);
    }, []);
    useLayoutEffect(() => {
        let first = 0, second = 0;
        if (value !== null) {
            setRetained(value);
            if (!canEnter) setEntered(false);
            else if (hasEntered.current) setEntered(true);
            else first = requestAnimationFrame(() => { second = requestAnimationFrame(() => {
                hasEntered.current = true;
                setEntered(true);
            }); });
        } else {
            setEntered(false);
            const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
            if (retained !== null) exitTimer.current = setTimeout(finishExit, reduced ? 0 : duration + 100);
        }
        return () => { cancelAnimationFrame(first); cancelAnimationFrame(second); clearTimeout(exitTimer.current); };
    }, [value, duration, canEnter, finishExit]);
    return { value: value ?? retained, present: value !== null || retained !== null,
        entered: value !== null && ready && entered, finishExit };
}
