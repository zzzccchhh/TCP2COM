import { Component, createRef } from 'react';
import type { ReactNode } from 'react';

type Props = { value: string; order: readonly string[]; children: ReactNode; className?: string; fill?: boolean };
type Scroll = { name: string; top: number; left: number };
type Shot = { node: HTMLElement; context: HTMLElement; scroll: { node: HTMLElement; top: number; left: number }[];
    key: string; x: number; incomingX?: number; height: number; width: number; viewportHeight: number };

function translation(node: HTMLElement | null) {
    return node ? new DOMMatrixReadOnly(getComputedStyle(node).transform).m41 : 0;
}

/** A frozen visual copy has no React handlers, effects, accessible controls or global optics. */
function capture(source: HTMLElement): Pick<Shot, 'node' | 'context' | 'scroll'> {
    const node = source.cloneNode(true) as HTMLElement;
    const layout = getComputedStyle(source);
    node.style.display = layout.display;
    node.style.flexDirection = layout.flexDirection;
    node.style.minHeight = layout.minHeight;
    const originals = [source, ...source.querySelectorAll<HTMLElement>('*')];
    const copies = [node, ...node.querySelectorAll<HTMLElement>('*')];
    const scroll: Shot['scroll'] = [];
    originals.forEach((element, index) => {
        const copy = copies[index];
        if (element instanceof HTMLInputElement && copy instanceof HTMLInputElement) {
            copy.value = element.value; copy.checked = element.checked;
        } else if (element instanceof HTMLSelectElement && copy instanceof HTMLSelectElement) copy.value = element.value;
        else if (element instanceof HTMLTextAreaElement && copy instanceof HTMLTextAreaElement) copy.value = element.value;
        if (element.scrollTop || element.scrollLeft) scroll.push({ node: copy, top: element.scrollTop, left: element.scrollLeft });
    });
    // Keep ancestor selectors (drawer headings, responsive form grids) inside the isolated tree.
    // These shells supply style context without adding layout boxes or painted surfaces.
    let context = node;
    for (let ancestor = source.parentElement; ancestor && ancestor !== document.body; ancestor = ancestor.parentElement) {
        const shell = ancestor.cloneNode(false) as HTMLElement;
        shell.dataset.slideContext = '';
        shell.style.setProperty('display', 'contents', 'important');
        shell.append(context); context = shell;
    }
    return { node, context, scroll };
}

function snapshotStyles() {
    return [...document.styleSheets].map(sheet => {
        try { return [...sheet.cssRules].map(rule => rule.cssText).join('\n'); } catch { return ''; }
    }).join('\n') + `
        *, *::before, *::after { animation: none !important; transition: none !important; caret-color: transparent !important; }
        .glass-surface::before, .liquid-raised-optics { backdrop-filter: none !important; -webkit-backdrop-filter: none !important; }
        .liquid-elastic-lens::before { opacity: 1 !important; }
        .liquid-raised-optics { display: none !important; }
        [data-slide-context]::before, [data-slide-context]::after { content: none !important; }
    `;
}

/** Only committed value changes slide. Pointer previews never reach this component. */
export default class SlidingContent extends Component<Props> {
    private root = createRef<HTMLDivElement>();
    private live = createRef<HTMLDivElement>();
    private ghost: HTMLElement | null = null;
    private ghostKey = '';
    private animations: Animation[] = [];
    private revision = 0;
    private timer: ReturnType<typeof setTimeout> | undefined;
    private reduced = matchMedia('(prefers-reduced-motion: reduce)');
    private positions = new Map<string, Scroll[]>();

    componentDidMount() { this.reduced.addEventListener('change', this.finish); }
    componentWillUnmount() { this.reduced.removeEventListener('change', this.finish); this.finish(); }

    getSnapshotBeforeUpdate(previous: Props): Shot | null {
        const root = this.root.current, live = this.live.current;
        if (previous.value === this.props.value || !root || !live) return null;
        this.positions.set(previous.value, [...live.querySelectorAll<HTMLElement>('[data-slide-scroll]')].map(node => ({
            name: node.dataset.slideScroll!, top: node.scrollTop, left: node.scrollLeft,
        })));
        if (this.reduced.matches || root.closest('[inert]') || !root.offsetWidth) return null;
        return { ...capture(live), key: previous.value, x: translation(live),
            incomingX: this.ghostKey === this.props.value ? translation(this.ghost) : undefined,
            height: live.offsetHeight, width: root.clientWidth, viewportHeight: root.getBoundingClientRect().height };
    }

    componentDidUpdate(previous: Props, _: unknown, shot: Shot | null) {
        if (previous.value === this.props.value) return;
        const root = this.root.current!, live = this.live.current!;
        const saved = this.positions.get(this.props.value);
        live.querySelectorAll<HTMLElement>('[data-slide-scroll]').forEach(node => {
            const position = saved?.find(position => position.name === node.dataset.slideScroll);
            if (position) { node.scrollTop = position.top; node.scrollLeft = position.left; }
        });
        this.finish();
        if (!shot || this.reduced.matches) return;
        const revision = ++this.revision;
        const before = this.props.order.indexOf(previous.value), after = this.props.order.indexOf(this.props.value);
        const direction = after >= before ? 1 : -1;
        const ghost = document.createElement('div');
        ghost.className = 'slide-outgoing'; ghost.setAttribute('aria-hidden', 'true'); ghost.inert = true;
        ghost.style.height = `${shot.height}px`;
        // A closed visual tree cannot duplicate live labels, IDs or form queries in the document.
        const shadow = ghost.attachShadow({ mode: 'closed' });
        const styles = document.createElement('style'); styles.textContent = snapshotStyles();
        shot.node.style.width = `${shot.width}px`; shot.node.style.height = `${shot.height}px`;
        shadow.append(styles, shot.context);
        root.append(ghost); this.ghost = ghost; this.ghostKey = shot.key;
        shot.scroll.forEach(({ node, top, left }) => { node.scrollTop = top; node.scrollLeft = left; });
        root.dataset.sliding = 'true'; root.dataset.direction = direction > 0 ? 'forward' : 'backward';
        const options: KeyframeAnimationOptions = { duration: 270, easing: 'cubic-bezier(.22, 1, .36, 1)', fill: 'both' };
        this.animations = [
            live.animate([{ transform: `translateX(${shot.incomingX ?? direction * shot.width}px)` }, { transform: 'translateX(0px)' }], options),
            ghost.animate([{ transform: `translateX(${shot.x}px)` }, { transform: `translateX(${-direction * shot.width}px)` }], options),
        ];
        if (!this.props.fill) {
            const height = live.offsetHeight;
            this.animations.push(root.animate([{ height: `${shot.viewportHeight}px` }, { height: `${height}px` }], options));
        }
        const finish = () => { if (this.revision === revision) this.finish(); };
        Promise.all(this.animations.map(animation => animation.finished)).then(finish).catch(() => {});
        this.timer = setTimeout(finish, 370);
    }

    private finish = () => {
        this.revision++;
        clearTimeout(this.timer); this.timer = undefined;
        this.animations.forEach(animation => animation.cancel()); this.animations = [];
        this.ghost?.remove(); this.ghost = null; this.ghostKey = '';
        const root = this.root.current;
        if (root?.hasAttribute('data-sliding')) {
            delete root.dataset.sliding; delete root.dataset.direction;
            root.dispatchEvent(new Event('content-slide-end', { bubbles: true }));
        }
    };

    render() {
        return <div ref={this.root} className={`sliding-content ${this.props.fill ? 'sliding-fill' : ''} ${this.props.className ?? ''}`} data-page={this.props.value}>
            <div ref={this.live} className="slide-live">{this.props.children}</div>
        </div>;
    }
}
