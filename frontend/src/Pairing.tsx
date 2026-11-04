import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { call } from './api';
import GlassSurface from './GlassSurface';
import LiquidButton from './LiquidButton';
import { usePresence } from './usePresence';
import { useExitBounds } from './useExitBounds';
import { useReturnFocus } from './useReturnFocus';
import { useGlassExpansion } from './useGlassExpansion';
import type { ExpansionOrigin } from './useGlassExpansion';
import { autoConnect, defaultTerminalName, disconnect, getLANStatus, isLAN, normalizeAddress, retryConnection, serviceAddress } from './lan';

export default function Pairing() {
    const [open, setOpen] = useState(false);
    const [opticsReady, setOpticsReady] = useState(false);
    const [closingContent, setClosingContent] = useState<ReactNode>(null);
    const [position, setPosition] = useState<CSSProperties>({ visibility: 'hidden' });
    const [positioned, setPositioned] = useState(false);
    const panelMotion = usePresence(open ? true : null, 380, opticsReady && positioned);
    const panel = useRef<HTMLElement>(null);
    const trigger = useRef<HTMLButtonElement>(null);
    const panelId = useId();
    const origin = useRef<ExpansionOrigin>({ x: 0, upwards: false });
    const [geometry, setGeometry] = useState('');
    // Fix one allocation per opening. Async status/peer updates scroll inside it instead of moving the card.
    useLayoutEffect(() => {
        if (!open || !panelMotion.present) return;
        let preferredHeight = 0;
        function measure() {
            const node = panel.current, button = trigger.current;
            if (!node || !button) return;
            const viewport = window.visualViewport;
            const vx = viewport?.offsetLeft ?? 0, vy = viewport?.offsetTop ?? 0;
            const vw = viewport?.width ?? innerWidth, vh = viewport?.height ?? innerHeight;
            const source = button.getBoundingClientRect();
            const width = Math.min(460, vw - 24);
            const left = Math.max(vx + 12, Math.min(source.right - width, vx + vw - width - 12));
            const top = Math.max(vy + 12, Math.min(source.bottom + 8, vy + vh - 140));
            const body = node.querySelector<HTMLElement>('.popover-content')!;
            preferredHeight ||= body.scrollHeight + 46;
            const height = Math.max(80, Math.min(preferredHeight, vy + vh - top - 12));
            origin.current = { x: source.left + source.width / 2 - left, upwards: false };
            setPosition({ position: 'fixed', left, top, width, height });
            setGeometry(`${origin.current.x}:${width}:${height}`);
            if (Math.abs(node.offsetWidth - width) < 1) setPositioned(true);
        }
        // Constrain width before measuring wrapped text. Both passes happen before entry.
        const node = panel.current!;
        node.style.width = `${Math.min(460, (window.visualViewport?.width ?? innerWidth) - 24)}px`;
        measure();
        const observer = new ResizeObserver(() => { if (!positioned) measure(); });
        observer.observe(node);
        window.addEventListener('resize', measure);
        window.addEventListener('scroll', measure, true);
        window.visualViewport?.addEventListener('resize', measure);
        window.visualViewport?.addEventListener('scroll', measure);
        return () => {
            observer.disconnect(); window.removeEventListener('resize', measure); window.removeEventListener('scroll', measure, true);
            window.visualViewport?.removeEventListener('resize', measure); window.visualViewport?.removeEventListener('scroll', measure);
        };
    }, [open, panelMotion.present]);
    useExitBounds(open, panelMotion.present, panel);
    useReturnFocus(panelMotion.present, trigger);
    useGlassExpansion(panel, origin, panelMotion.present, panelMotion.entered, open && opticsReady, geometry, panelMotion.finishExit);
    useLayoutEffect(() => {
        if (!panelMotion.present) { setOpticsReady(false); setPositioned(false); setClosingContent(null); setPosition({ visibility: 'hidden' }); }
    }, [panelMotion.present]);
    const [address, setAddress] = useState(serviceAddress);
    const [name, setName] = useState(defaultTerminalName);
    const [status, setStatus] = useState(getLANStatus);
    const [message, setMessage] = useState('');
    const [peers, setPeers] = useState<any[]>([]);
    const pending = useRef<AbortController | null>(null);
    useEffect(() => {
        const changed = () => { const next = getLANStatus(); setStatus(next); if (next.address) setAddress(next.address); };
        window.addEventListener('lanstatus', changed);
        void autoConnect().catch(error => setMessage(String(error)));
        return () => { window.removeEventListener('lanstatus', changed); pending.current?.abort(); };
    }, []);
    async function connect() {
        if (status.state === 'connecting') return;
        const controller = new AbortController(); pending.current = controller;
        setMessage('');
        await retryConnection(address, name, controller.signal);
        pending.current = null;
    }
    async function refresh() {
        try { setPeers(await call('peers')); } catch (error) { setMessage(String(error)); }
    }
    function openLocal() {
        try { location.assign(normalizeAddress(address) + '/'); } catch (error) { setMessage(String(error)); }
    }
    const busy = status.state === 'connecting';
    function closePanel() {
        if (!open) return;
        setClosingContent(content);
        setOpen(false);
    }
    const content = <>
            <div className="panel-title">局域网多端控制<LiquidButton className="icon-button" aria-label="关闭连接面板" onClick={closePanel}>×</LiquidButton></div>
            <p>服务端的打开网页、复制网址或二维码均可自动连接。首次使用请打开完整连接链接；局域网工作台可直接访问。</p>
            <form onSubmit={e => { e.preventDefault(); void connect(); }}>
                <label className="field"><span className="field-label">服务地址</span><input aria-label="服务地址" placeholder="192.168.1.100:8765" disabled={busy} value={address} onChange={e => setAddress(e.target.value)} required /></label>
                <label className="field"><span className="field-label">终端名称</span><input aria-label="终端名称" disabled={busy} value={name} onChange={e => setName(e.target.value)} maxLength={60} required /></label>
                <LiquidButton className="button primary" disabled={busy} type="submit">{busy ? '正在连接…' : '连接服务 / 重试'}</LiquidButton>
            </form>
            <p role="status">{message || status.message}</p>
            <LiquidButton className="button" onClick={openLocal}>打开局域网工作台</LiquidButton>
            {isLAN() && <><p>当前服务：{serviceAddress()}</p><LiquidButton className="button" onClick={disconnect}>断开本终端</LiquidButton><LiquidButton className="button" onClick={refresh}>刷新已连接终端</LiquidButton>
                <ul>{peers.map(peer => <li key={peer.id}>{peer.name}{peer.self ? '（本终端）' : ''}<LiquidButton className="button" onClick={async () => {
                    try { await call('revoke', peer.id); if (peer.self) disconnect(); else await refresh(); } catch (error) { setMessage(String(error)); }
                }}>撤销连接</LiquidButton></li>)}</ul></>}
            <p>网页仅自动连接服务，不会自动启动串口桥接或 USB。关闭网页不会停止桥接。</p>
    </>;
    return <div className="lan-entry">
        <LiquidButton ref={trigger} className="button" aria-expanded={open} aria-controls={panelMotion.present ? panelId : undefined}
            onClick={() => {
                if (open) closePanel();
                else { setClosingContent(null); setOpen(true); if (isLAN()) void refresh(); }
            }}>局域网连接</LiquidButton>
        {status.state !== 'idle' && <span className={`lan-connection-state ${status.state}`} role="status" aria-label="服务连接状态">{status.state === 'connected' ? '服务已连接' : status.message}</span>}
        {panelMotion.present && createPortal(<GlassSurface surfaceRef={panel} className="panel lan-panel liquid-popover" aria-label="局域网连接"
            style={position} id={panelId} data-ready={opticsReady || undefined} onGlassReady={() => setOpticsReady(true)}
            data-state={panelMotion.entered ? 'open' : 'closed'} aria-hidden={!open || undefined} inert={!open}>
            <span className="glass-expansion-clock" aria-hidden="true" />
            <div className="glass-expansion-outline" aria-hidden="true" />
            <div className="popover-content glass-expansion-content">{open ? content : closingContent ?? content}</div>
        </GlassSurface>, document.body)}
    </div>;
}
