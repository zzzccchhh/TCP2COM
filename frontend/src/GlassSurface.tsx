import { useEffect, useLayoutEffect, useRef } from 'react';
import type { HTMLAttributes, Ref } from 'react';
import { attachLiquidGlass } from './vendor/liquid-glass-web';

type Props = HTMLAttributes<HTMLElement> & {
    as?: 'section' | 'div' | 'span'; opticsEnabled?: boolean; onGlassReady?: () => void; surfaceRef?: Ref<HTMLElement>;
};

/** Optics live on a decorative pseudo-element, independently of text and form controls. */
export default function GlassSurface({ as: Tag = 'section', className = '', children, opticsEnabled = true, onGlassReady, surfaceRef, ...props }: Props) {
    const surface = useRef<HTMLElement | null>(null);
    const readyCallback = useRef(onGlassReady);
    useLayoutEffect(() => { readyCallback.current = onGlassReady; }, [onGlassReady]);
    useEffect(() => {
        if (opticsEnabled) return attachLiquidGlass(surface.current!, onGlassReady ? () => readyCallback.current?.() : undefined);
    }, [opticsEnabled]);
    return <Tag {...props} ref={element => {
        surface.current = element;
        if (typeof surfaceRef === 'function') surfaceRef(element);
        else if (surfaceRef) surfaceRef.current = element;
    }} className={`glass-surface ${className}`}>
        {children}
    </Tag>;
}
