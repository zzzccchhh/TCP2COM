import { useId, useState } from 'react';
import type { ReactNode } from 'react';
import { ChevronDown, SlidersHorizontal } from 'lucide-react';

/** Keep form values mounted while grid interpolation reversibly opens/closes the content. */
export default function LiquidDisclosure({ children }: { children: ReactNode }) {
    const [open, setOpen] = useState(false);
    const id = useId();
    return <div className="advanced liquid-disclosure" data-open={open}>
        <button className="disclosure-trigger" id={`${id}-trigger`} type="button" aria-expanded={open}
            aria-controls={id} onClick={() => setOpen(!open)}>
            <SlidersHorizontal size={14} />高级参数<ChevronDown size={13} />
        </button>
        <div className="disclosure-grid" id={id} role="region" aria-labelledby={`${id}-trigger`}
            aria-hidden={!open || undefined} inert={!open}>
            <div className="disclosure-content">{children}</div>
        </div>
    </div>;
}
