import { useEffect, useRef, useState } from 'react';
import { call } from './api';
import { autoConnect, defaultTerminalName, disconnect, getLANStatus, isLAN, normalizeAddress, retryConnection, serviceAddress } from './lan';

export default function Pairing() {
    const [open, setOpen] = useState(false);
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
    return <div className="lan-entry">
        <button className="button" onClick={() => { setOpen(!open); if (isLAN()) void refresh(); }}>局域网连接</button>
        {status.state !== 'idle' && <span className={`lan-connection-state ${status.state}`} role="status" aria-label="服务连接状态">{status.state === 'connected' ? '服务已连接' : status.message}</span>}
        {open && <section className="panel lan-panel" aria-label="局域网连接">
            <div className="panel-title">局域网多端控制<button className="icon-button" aria-label="关闭连接面板" onClick={() => setOpen(false)}>×</button></div>
            <p>服务端的打开网页、复制网址或二维码均可自动连接。首次使用请打开完整连接链接；局域网工作台可直接访问。</p>
            <form onSubmit={e => { e.preventDefault(); void connect(); }}>
                <label className="field"><span className="field-label">服务地址</span><input aria-label="服务地址" placeholder="192.168.1.100:8765" disabled={busy} value={address} onChange={e => setAddress(e.target.value)} required /></label>
                <label className="field"><span className="field-label">终端名称</span><input aria-label="终端名称" disabled={busy} value={name} onChange={e => setName(e.target.value)} maxLength={60} required /></label>
                <button className="button primary" disabled={busy} type="submit">{busy ? '正在连接…' : '连接服务 / 重试'}</button>
            </form>
            <p role="status">{message || status.message}</p>
            <button className="button" onClick={openLocal}>打开局域网工作台</button>
            {isLAN() && <><p>当前服务：{serviceAddress()}</p><button className="button" onClick={disconnect}>断开本终端</button><button className="button" onClick={refresh}>刷新已连接终端</button>
                <ul>{peers.map(peer => <li key={peer.id}>{peer.name}{peer.self ? '（本终端）' : ''}<button className="button" onClick={async () => {
                    try { await call('revoke', peer.id); if (peer.self) disconnect(); else await refresh(); } catch (error) { setMessage(String(error)); }
                }}>撤销连接</button></li>)}</ul></>}
            <p>网页仅自动连接服务，不会自动启动串口桥接或 USB。关闭网页不会停止桥接。</p>
        </section>}
    </div>;
}
