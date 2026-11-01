import { useState } from 'react';
import { call } from './api';
import { disconnect, isLAN, pair, serviceAddress } from './lan';

export default function Pairing() {
    const [open, setOpen] = useState(false);
    const [address, setAddress] = useState(serviceAddress);
    const [code, setCode] = useState('');
    const [name, setName] = useState('我的浏览器');
    const [message, setMessage] = useState('');
    const [busy, setBusy] = useState(false);
    const [peers, setPeers] = useState<any[]>([]);
    async function connect() {
        setBusy(true); setMessage('正在连接…');
        try { await pair(address, code, name); setCode(''); setMessage('已配对，所有终端共享设备状态。'); setPeers(await call('peers')); }
        catch (error) { setMessage(String(error)); }
        finally { setBusy(false); }
    }
    async function refresh() {
        try { setPeers(await call('peers')); } catch (error) { setMessage(String(error)); }
    }
    return <div className="lan-entry">
        <button className="button" onClick={() => { setOpen(!open); if (isLAN()) void refresh(); }}>局域网配对</button>
        {open && <section className="panel lan-panel" aria-label="局域网配对">
            <div className="panel-title">局域网多端控制<button className="icon-button" aria-label="关闭配对面板" onClick={() => setOpen(false)}>×</button></div>
            <p>在连接 ESP 的电脑上运行「启动_局域网服务.bat」，输入显示的地址和配对码。串口属于服务电脑。</p>
            <form onSubmit={e => { e.preventDefault(); void connect(); }}>
                <label className="field"><span className="field-label">服务地址</span><input aria-label="服务地址" value={address} onChange={e => setAddress(e.target.value)} required /></label>
                <label className="field"><span className="field-label">终端名称</span><input aria-label="终端名称" value={name} onChange={e => setName(e.target.value)} maxLength={60} required /></label>
                <label className="field"><span className="field-label">配对码</span><input aria-label="配对码" value={code} onChange={e => setCode(e.target.value)} autoComplete="off" required /></label>
                <button className="button primary" disabled={busy} type="submit">配对并连接</button>
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
