import { useEffect, useRef, useState } from 'react';
import { call } from './api';
import { disconnect, hasSharedAddress, isLAN, pair, serviceAddress } from './lan';

export default function Pairing() {
    const [open, setOpen] = useState(hasSharedAddress);
    const [address, setAddress] = useState(serviceAddress);
    const [name, setName] = useState('我的浏览器');
    const [message, setMessage] = useState('');
    const [busy, setBusy] = useState(false);
    const [peers, setPeers] = useState<any[]>([]);
    const pending = useRef<AbortController | null>(null);
    useEffect(() => () => pending.current?.abort(), []);
    function cancel() { if (pending.current) { pending.current.abort(); setMessage('已取消配对申请'); } }
    async function connect() {
        if (busy) return;
        const controller = new AbortController(); pending.current = controller;
        setBusy(true); setMessage('正在连接服务…');
        try { await pair(address, name, controller.signal, id => setMessage(`等待服务端允许 · 请求编号 ${id}（2 分钟内有效）`)); setMessage('已配对，所有终端共享设备状态。'); setPeers(await call('peers')); }
        catch (error) { setMessage(String(error)); }
        finally { setBusy(false); pending.current = null; }
    }
    async function refresh() {
        try { setPeers(await call('peers')); } catch (error) { setMessage(String(error)); }
    }
    return <div className="lan-entry">
        <button className="button" onClick={() => { if (open) cancel(); setOpen(!open); if (isLAN()) void refresh(); }}>局域网配对</button>
        {open && <section className="panel lan-panel" aria-label="局域网配对">
            <div className="panel-title">局域网多端控制<button className="icon-button" aria-label="关闭配对面板" onClick={() => { cancel(); setOpen(false); }}>×</button></div>
            <p>在连接 ESP 的电脑上运行「启动_局域网服务.vbs」，输入服务地址并请求配对，在服务管理窗口允许后自动连接。串口属于服务电脑。</p>
            <form onSubmit={e => { e.preventDefault(); void connect(); }}>
                <label className="field"><span className="field-label">服务地址</span><input aria-label="服务地址" placeholder="192.168.1.100:8765" disabled={busy} value={address} onChange={e => setAddress(e.target.value)} required /></label>
                <label className="field"><span className="field-label">终端名称</span><input aria-label="终端名称" disabled={busy} value={name} onChange={e => setName(e.target.value)} maxLength={60} required /></label>
                <button className="button primary" disabled={busy} type="submit">{busy ? '等待服务端允许…' : '请求配对'}</button>
                {busy && <button className="button" type="button" onClick={cancel}>取消申请</button>}
            </form>
            <p role="status">{message}</p>
            {isLAN() && <><p>当前服务：{serviceAddress()}</p><button className="button" onClick={disconnect}>断开本终端</button><button className="button" onClick={refresh}>刷新已配对终端</button>
                <ul>{peers.map(peer => <li key={peer.id}>{peer.name}{peer.self ? '（本终端）' : ''}<button className="button" onClick={async () => {
                    try { await call('revoke', peer.id); if (peer.self) disconnect(); else await refresh(); } catch (error) { setMessage(String(error)); }
                }}>撤销配对</button></li>)}</ul></>}
            <p>在线页面无法连接时，请在浏览器直接打开上面的服务地址。关闭此网页不会停止桥接。</p>
        </section>}
    </div>;
}
