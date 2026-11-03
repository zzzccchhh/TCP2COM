import { useEffect, useRef } from 'react';
import type { HTMLAttributes } from 'react';
import { attachLiquidGlass } from './vendor/liquid-glass-web';

type Props = HTMLAttributes<HTMLElement> & { as?: 'section' | 'div' };

/** Optics live on a decorative pseudo-element, independently of text and form controls. */
export default function GlassSurface({ as: Tag = 'section', className = '', children, ...props }: Props) {
    const surface = useRef<HTMLElement | null>(null);
    useEffect(() => attachLiquidGlass(surface.current!), []);
    return <Tag {...props} ref={element => { surface.current = element; }} className={`glass-surface ${className}`}>
        {children}
    </Tag>;
}
