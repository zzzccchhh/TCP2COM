import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import {
    Activity, ArrowDownLeft, ArrowRight, ArrowUpRight, Check, CircleHelp,
    Cable, Download, FileJson, History, Info, LoaderCircle, Network, Pause, Play,
    Radio, RefreshCw, RotateCcw, Search, Settings2, ShieldCheck, SlidersHorizontal,
    Square, Terminal, Trash2, Unplug, Usb, Wifi, X, Zap,
} from 'lucide-react';
import { applyDraft, call, defaults, deviceDefaults, download, emptySnapshot, isTrafficActive, labels, size } from './api';
import type { Data } from './api';
import Pairing from './Pairing';
import GlassSurface from './GlassSurface';
import GlassPicker from './GlassPicker';
import LiquidSegments from './LiquidSegments';
import SlidingContent from './SlidingContent';
import Toggle from './LiquidToggle';
import LiquidDisclosure from './LiquidDisclosure';
import { usePresence } from './usePresence';
import { useReturnFocus } from './useReturnFocus';
import { getLANStatus, isLAN } from './lan';
import { chooseAppearance, useAppearance } from './appearance';

type FieldProps = {
    label: string; value: any; onChange: (value: any) => void;
    options?: [string | number, string][]; disabled?: boolean; type?: string;
    hint?: string; list?: string; numeric?: boolean;
};

function Field({ label, value, onChange, options, disabled, type = 'text', hint, list, numeric }: FieldProps) {
    return <label className={`field ${disabled ? 'is-disabled' : ''}`}>
        <span className="field-label">{label}</span>
        {options || list ? <GlassPicker label={label} value={value ?? ''} options={options} list={list} disabled={disabled}
            onChange={next => onChange(numeric && (options || /^\d+$/.test(next)) ? Number(next) : next)} /> : <input aria-label={label} value={value ?? ''} disabled={disabled}
            type={type} list={list} autoComplete="off" spellCheck={false}
            onChange={e => onChange(numeric && /^\d+$/.test(e.target.value) ? Number(e.target.value) : e.target.value)} />}
        {hint && <small>{hint}</small>}
    </label>;
}

function Badge({ children, active = false, warning = false }: { children: ReactNode; active?: boolean; warning?: boolean }) {
    return <span className={`badge ${active ? 'active' : ''} ${warning ? 'warning' : ''}`}><i />{children}</span>;
}

function Chart({ samples, index, label, color }: { samples: number[][]; index: number; label: string; color: string }) {
    const values = samples.map(sample => sample[index]);
    const max = Math.max(1024, ...values);
    const points = values.map((v, i) => `${i * 600 / 119},${115 - v / max * 94}`).join(' ');
    return <div className="chart"><div className="chart-heading"><span><i style={{ background: color }} />{label}</span>
        <strong>{size(values.at(-1) || 0, true)}</strong></div>
        <div className="chart-plot"><span className="chart-scale">{size(max, true)}</span>
            <svg viewBox="0 0 600 130" preserveAspectRatio="none" role="img" aria-label={`${label}最近30秒速率`}>
                {[22, 53, 84, 115].map(y => <line key={y} x1="0" x2="600" y1={y} y2={y} className="grid-line" />)}
                {[0, 150, 300, 450, 600].map(x => <line key={x} x1={x} x2={x} y1="10" y2="115" className="grid-line vertical" />)}
                <polyline points={points} fill="none" stroke={color} strokeWidth="2" vectorEffect="non-scaling-stroke" />
            </svg></div><div className="chart-axis"><span>30 秒前</span><span>现在</span></div></div>;
}

export default function App() {
    const appearance = useAppearance();
    const [settings, setSettings] = useState<Data>(defaults);
    const settingsRef = useRef(settings);
    const pendingSettings = useRef<Promise<any>>(Promise.resolve());
    const settingsError = useRef('');
    const pendingLAN = useRef(0);
    const pendingDraft = useRef<Promise<any>>(Promise.resolve());
    const [snap, setSnap] = useState<Data>(emptySnapshot);
    const [native, setNative] = useState(false);
    const [ready, setReady] = useState(false);
    const [ports, setPorts] = useState<Data[]>([]);
    const [logs, setLogs] = useState<Data[]>([]);
    const [tab, setTab] = useState('traffic');
    const [source, setSource] = useState('all');
    const [search, setSearch] = useState('');
    const [autoscroll, setAutoscroll] = useState(true);
    const [toast, setToast] = useState('');
    const [working, setWorking] = useState(false);
    const [drawer, setDrawer] = useState<'config' | 'diagnostics' | 'reset' | 'help' | null>(null);
    const [config, setConfig] = useState<Data>(deviceDefaults('c5'));
    const [drafts, setDrafts] = useState<Data>({});
    const [configTab, setConfigTab] = useState('common');
    const [passwords, setPasswords] = useState<Data>({ ap: '', pc: '', shared: '' });
    const [clear, setClear] = useState<string[]>([]);
    const [confirm, setConfirm] = useState<Data | null>(null);
    const drawerMotion = usePresence(drawer);
    const confirmMotion = usePresence(confirm);
    const overlayPresent = drawerMotion.present || confirmMotion.present;
    useReturnFocus(drawerMotion.present);
    useReturnFocus(confirmMotion.present);
    const shownDrawer = drawerMotion.value;
    const shownConfirm = confirmMotion.value;
    const [resetHost, setResetHost] = useState('');
    const [resetPort, setResetPort] = useState('8081');
    const [samples, setSamples] = useState<number[][]>([]);
    const logBody = useRef<HTMLDivElement>(null);
    const importInput = useRef<HTMLInputElement>(null);
    const cursor = useRef(0);
    const logGeneration = useRef(0);
    const lastEpoch = useRef(0);
    const mounted = useRef(false);
    const device = settings.device as string;
    const caps = device !== 'esp01s';

    const notify = useCallback((message: string) => setToast(message), []);
    const refreshPorts = useCallback(async () => {
        if (window.pywebview?.api) {
            try { setPorts(await call('ports')); } catch (error) { notify(String(error)); }
        } else notify('浏览器预览无法枚举本地串口，请从桌面启动器打开。');
    }, [notify]);

    useEffect(() => {
        mounted.current = true;
        let stopped = false;
        let polling: ReturnType<typeof setTimeout>;
        let initialized = false;
        let generation = 0;
        async function poll(turn = generation) {
            if (stopped || turn !== generation) return;
            try {
                const next = await call<Data>('poll', cursor.current);
                if (stopped || turn !== generation) return;
                if (next.epoch < lastEpoch.current) {
                    polling = setTimeout(() => poll(turn), 250);
                    return;
                }
                setSnap(next);
                if (isLAN() && next.settings && !pendingLAN.current) {
                    if (next.settings.device !== settingsRef.current.device) setConfig(deviceDefaults(next.settings.device));
                    settingsRef.current = { ...defaults, ...next.settings };
                    setSettings(settingsRef.current);
                }
                if (next.epoch !== lastEpoch.current) {
                    setPasswords({ ap: '', pc: '', shared: '' }); setClear([]); setConfirm(null);
                    lastEpoch.current = next.epoch;
                }
                const resetLogs = next.log_generation !== logGeneration.current;
                if (next.logs.length || resetLogs) {
                    setLogs(previous => (resetLogs ? next.logs : [...previous, ...next.logs]).slice(-3000));
                }
                logGeneration.current = next.log_generation;
                cursor.current = next.cursor;
                if (isTrafficActive(next, settingsRef.current.mode)) {
                    setSamples(previous => [...previous, next.rates].slice(-120));
                }
            } catch (error) { if (!stopped && turn === generation) {
                notify(`界面连接异常：${String(error)}`);
                if (isLAN()) setSnap(previous => ({ ...previous, connected: false, running: false, state_status: '与局域网服务失去联系，请检查连接或重新配对' }));
            } }
            if (!stopped && turn === generation && getLANStatus().state !== 'revoked') polling = setTimeout(() => poll(turn), 250);
        }
        async function initialize() {
            const api = window.pywebview?.api;
            if ((initialized && !isLAN()) || !api || !['initial', 'poll', 'ports'].every(method => typeof api[method] === 'function')) return;
            const turn = ++generation;
            clearTimeout(polling);
            if (isLAN()) { cursor.current = 0; lastEpoch.current = 0; logGeneration.current = 0; setLogs([]); setSamples([]); }
            initialized = true;
            try {
                const data = await call<Data>('initial');
                if (stopped || turn !== generation) return;
                settingsRef.current = { ...defaults, ...data.settings };
                setSettings(settingsRef.current); setConfig(data.config); setDrafts(data.drafts);
                setNative(true); setReady(true);
                await refreshPorts();
                await poll(turn);
            } catch (error) { if (!stopped && turn === generation) { initialized = false; notify(String(error)); } }
        }
        window.addEventListener('pywebviewready', initialize);
        initialize();
        const fallback = setTimeout(() => { if (!initialized) setReady(true); }, 500);
        return () => {
            stopped = true; mounted.current = false;
            window.removeEventListener('pywebviewready', initialize);
            clearTimeout(fallback); clearTimeout(polling);
        };
    }, [notify, refreshPorts]);

    useEffect(() => { if (toast) { const timer = setTimeout(() => setToast(''), 6000); return () => clearTimeout(timer); } }, [toast]);
    useEffect(() => { if (autoscroll && logBody.current) logBody.current.scrollTop = logBody.current.scrollHeight; }, [logs, autoscroll, source, search, tab]);
    useEffect(() => {
        function key(event: KeyboardEvent) { if (event.key === 'Escape') { if (confirmMotion.present) setConfirm(null); else setDrawer(null); } }
        window.addEventListener('keydown', key);
        return () => window.removeEventListener('keydown', key);
    }, [confirmMotion.present]);
    useEffect(() => { window.dispatchEvent(new Event('workspace-overlay-change')); }, [drawer, confirm]);
    useEffect(() => {
        if (!overlayPresent) return;
        const previousOverflow = document.body.style.overflow, previousPadding = document.body.style.paddingRight;
        const gutter = innerWidth - document.documentElement.clientWidth;
        if (gutter > 0) document.body.style.paddingRight = `${parseFloat(getComputedStyle(document.body).paddingRight) + gutter}px`;
        document.body.style.overflow = 'hidden';
        const selector = 'button:not(:disabled), input:not(:disabled):not([hidden]), select:not(:disabled), [tabindex="0"]';
        function trap(event: KeyboardEvent) {
            const modal = document.querySelector('.modal-backdrop[data-state="open"] .confirm-dialog')
                ?? document.querySelector('.drawer-backdrop[data-state="open"] .drawer');
            if (event.key !== 'Tab' || !modal) return;
            const controls = [...modal.querySelectorAll<HTMLElement>(selector)].filter(element => element.offsetParent !== null);
            const first = controls[0], last = controls.at(-1);
            if (!modal.contains(document.activeElement)) { event.preventDefault(); (event.shiftKey ? last : first)?.focus({ preventScroll: true }); }
            else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus({ preventScroll: true }); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus({ preventScroll: true }); }
        }
        window.addEventListener('keydown', trap);
        return () => {
            document.body.style.overflow = previousOverflow; document.body.style.paddingRight = previousPadding;
            window.removeEventListener('keydown', trap);
        };
    }, [overlayPresent]);
    useEffect(() => {
        // A morphing dialog starts hidden; move focus after its entrance state becomes visible.
        if (!drawerMotion.entered && !confirmMotion.entered) return;
        const modal = confirmMotion.entered ? document.querySelector('.confirm-dialog')
            : !confirmMotion.present ? document.querySelector('.drawer') : null;
        modal?.querySelector<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled)')?.focus({ preventScroll: true });
    }, [drawerMotion.entered, confirmMotion.entered]);

    function change(key: string, value: any) {
        const next = { ...settingsRef.current, [key]: value };
        settingsRef.current = next;
        setSettings(next);
        if (['device', 'config_transport', 'config_url', 'usb_port'].includes(key)) {
            setPasswords({ ap: '', pc: '', shared: '' }); setClear([]); setConfirm(null);
            setSnap(previous => ({ ...previous, baseline: null }));
        }
        if (key === 'device') {
            const draft = drafts[value];
            const nextConfig = deviceDefaults(value);
            setConfig(draft ? applyDraft(nextConfig, draft, value) : nextConfig);
        }
        if (native) {
            if (isLAN()) pendingLAN.current++;
            pendingSettings.current = pendingSettings.current.then(async () => {
            try {
                const result = await call('update_settings', isLAN() ? { [key]: value } : next);
                if (typeof result?.epoch === 'number') lastEpoch.current = Math.max(lastEpoch.current, result.epoch);
                settingsError.current = '';
            }
            catch (error) {
                settingsError.current = String(error); notify(String(error));
                if (isLAN()) {
                    try { const data = await call<Data>('initial'); settingsRef.current = { ...defaults, ...data.settings }; setSettings(settingsRef.current); settingsError.current = ''; }
                    catch { /* Keep error visible until connection is restored. */ }
                }
            }
            finally { if (isLAN()) pendingLAN.current = Math.max(0, pendingLAN.current - 1); }
        });
        }
    }

    async function run(action: () => Promise<any>) {
        if (working) return;
        setWorking(true);
        try { await pendingSettings.current; await pendingDraft.current; if (settingsError.current) throw new Error(settingsError.current); await action(); }
        catch (error) { notify(error instanceof Error ? error.message : String(error)); }
        finally { if (mounted.current) setWorking(false); }
    }

    function editConfig(key: string, value: any, group?: string) {
        const next = group ? { ...config, [group]: { ...config[group], [key]: value } } : { ...config, [key]: value };
        setConfig(next); setConfirm(null);
        cacheDraft(next);
    }

    function cacheDraft(next: Data, persist = true) {
        const capturedDevice = device;
        if (native && persist) pendingDraft.current = pendingDraft.current.then(() => call('remember_draft', capturedDevice, next)).catch(error => notify(String(error)));
        const draft = { common: {}, ap: {}, pc: { ...next.pc }, shared: { ...next.shared } } as Data;
        Object.entries(next).forEach(([k, v]) => { if (!['pc', 'shared'].includes(k)) draft[k.startsWith('ap_') ? 'ap' : 'common'][k] = v; });
        setDrafts(previous => ({ ...previous, [capturedDevice]: draft }));
    }

    async function readConfig() {
        const result = await call<Data>('read_device');
        const latest = await call<Data>('poll', cursor.current);
        lastEpoch.current = latest.epoch;
        setConfig(result); setPasswords({ ap: '', pc: '', shared: '' }); setClear([]);
        setSnap(latest);
        if (!drafts[device]) cacheDraft(result, false);
        notify('已读取当前设备配置');
    }

    function restoreDraft() {
        if (!drafts[device]) return;
        const result = applyDraft(config, drafts[device], device);
        setConfig(result); setPasswords({ ap: '', pc: '', shared: '' }); setClear([]);
        notify('已恢复上次草稿；保存前仍需读取当前设备');
    }

    const field = (key: string, label: string, extra: Partial<FieldProps> = {}) => <Field key={key} label={label}
        value={settings[key]} onChange={value => change(key, value)} {...extra} />;
    const linkOptions: [number, string][] = [[0, 'UART 原始透传'], device === 'c5' ? [4, '四线 SPI 简易写入'] : [1, 'SPI WBR/1']];
    if (device === 'c5') linkOptions.push([3, 'UART GPIO 流控透传']);
    if (device !== 'c5') linkOptions.push([2, 'UART WBR/1']);
    if (device === 'c5' && config.link_mode === -1) linkOptions.unshift([-1, '请选择接口（旧接口已移除）']);
    const optional = ['flow_pin', 'reset_pin', 'net_role', 'auto_reconnect', 'remote_ip', 'remote_port', 'local_port', 'reconnect_ms', 'connect_timeout_ms', 'up_kb', 'down_kb'];
    const cf = (key: string, label: string, extra: Partial<FieldProps> = {}, group?: string) => <Field key={`${group || ''}${key}`} label={label}
        value={group ? config[group]?.[key] : config[key]} onChange={value => editConfig(key, value, group)}
        disabled={working || snap.config_busy || (!!snap.baseline && optional.includes(key) && !(key in snap.baseline))} {...extra} />;
    const waiting = working || snap.busy || snap.config_busy;
    const usbActive = !!snap.usb;
    const usbOnline = snap.usb?.connected && !snap.usb?.closing;
    const usbStale = snap.usb?.age != null && snap.usb.age > 15;
    const visibleLogs = useMemo(() => logs.filter(row => (source === 'all' || row.source === source)
        && `${row.text} ${row.time}`.toLocaleLowerCase().includes(search.toLocaleLowerCase())), [logs, source, search]);
    const logRows = useMemo(() => visibleLogs.map(row => <div className={`log-row level-${row.level}`} key={row.id}><time>{row.time}</time><span className="log-origin">{row.source === 'host' ? 'HOST' : 'ESP'}</span><span className="log-message">{row.text}</span></div>), [visibleLogs]);
    const rates = snap.rates as number[];
    const activeMode = snap.running_settings?.mode ?? settings.mode;
    const udpMode = activeMode === 'udp';
    const tcpConnected = snap.running && snap.connected;
    const trafficActive = isTrafficActive(snap, settings.mode);
    const connectionState = udpMode ? (snap.running ? 'udp' : 'stopped') : tcpConnected ? 'connected' : snap.running ? 'waiting' : 'disconnected';
    const connectionLabel = udpMode ? (snap.running ? 'UDP 已绑定' : 'UDP 未启动') : tcpConnected ? 'TCP 已连接'
        : snap.running ? (activeMode === 'tcp-server' ? 'TCP 未连接 · 等待设备' : 'TCP 未连接 · 重连中') : 'TCP 未连接';
    const stats = snap.stats;
    const dropped = (stats.tcp_to_com_drop || 0) + (stats.com_to_tcp_drop || 0);
    const configDirty = !!snap.baseline && (Object.values(passwords).some(Boolean) || clear.length > 0 ||
        Object.keys(snap.baseline).some(key => !['revision', 'saved_revision', 'unsaved', 'migration_requires_setup', 'config_source', 'config_api'].includes(key)
            && JSON.stringify(snap.baseline[key]) !== JSON.stringify(config[key])));

    return <div className="app-shell">
        <header className="masthead">
            <div className="brand"><span className="brand-mark"><Wifi size={23} strokeWidth={1.7} /></span><div><strong>Wi-Fi Bridge<span className="brand-period">.</span></strong><small>WIRELESS SERIAL WORKSPACE</small></div></div>
            <div className="header-right"><Pairing /><span className="local-indicator"><ShieldCheck size={14} />{isLAN() ? '局域网已配对' : native ? '本地运行' : '界面预览'}</span>
                <span className="header-divider" /><button className="icon-button" title="使用帮助" aria-label="使用帮助" onClick={() => setDrawer('help')}><CircleHelp size={19} /></button>
                <span className="version">DESKTOP / 1.0</span></div>
        </header>

        <section className="page-heading"><div className="device-switch appearance-switch"><span>外观</span><GlassPicker label="外观" value={appearance}
            onChange={next => { void chooseAppearance(next).catch(() => notify('外观已切换，但未能保存；重启后可能恢复原设置。')); }}
            options={[["light", "原始浅色"], ["dark", "深色"], ["system", "跟随系统"]]} /></div>
            <div className="device-switch"><span>当前设备</span><GlassPicker label="当前设备" disabled={waiting} value={device}
                onChange={next => change('device', next)} options={Object.entries(labels).map(([key, text]) => [key, String(text)])} /></div>
        </section>

        {!native && ready && <div className="preview-banner"><Info size={14} /><span>浏览器界面预览 · 打开服务端的连接链接或扫码可自动连接，也可通过右上角「局域网连接」重试。</span></div>}

        <main className="workspace">
            <aside className="connection-column">
                <GlassSurface className="panel connection-panel"><div className="panel-title"><span><Network size={17} />连接设置</span><span className="section-number">01</span></div>
                    <fieldset disabled={waiting}>
                        <LiquidSegments className="mode-switch" label="电脑网络角色" value={settings.mode} disabled={waiting}
                            onChange={next => change('mode', next)} items={[
                                { value: 'tcp-client', label: 'TCP 客户端' }, { value: 'tcp-server', label: 'TCP 服务端' }, { value: 'udp', label: 'UDP' },
                            ]} />
                        <SlidingContent value={settings.mode} order={['tcp-client', 'tcp-server', 'udp']} className="network-content">
                        <div className="form-stack">
                            {settings.mode !== 'tcp-server' && field('host', '设备 IPv4 地址')}
                            <div className={settings.mode !== 'tcp-client' ? 'form-grid' : ''}>
                                {settings.mode !== 'tcp-client' && field('listen', '电脑监听地址')}
                                {field('port', settings.mode === 'tcp-client' ? '目标端口' : '电脑本地端口')}
                            </div>
                            {settings.mode === 'udp' && field('peer_port', 'UDP 对端端口')}
                        </div>
                        </SlidingContent>
                        <div className="section-rule" /><div className="subheading"><Cable size={15} /><span>本地串口</span><button className="icon-button small" title="刷新串口" aria-label="刷新串口" onClick={refreshPorts}><RefreshCw size={14} /></button></div>
                        <div className="form-grid">{field('com', '业务 COM', { list: 'serial-ports' })}
                            {field('baud', '波特率 / bps', { list: 'baud-rates' })}
                        </div>
                        <LiquidDisclosure>
                            <div className="form-stack">
                                <SlidingContent value={settings.mode} order={['tcp-client', 'tcp-server', 'udp']}>{settings.mode === 'tcp-server' && field('host', '来源 IP 筛选（可选）', { hint: '留空允许任意来源连接' })}</SlidingContent>
                                <div className="form-grid">{field('bytesize', '数据位', { options: ['5', '6', '7', '8'].map(v => [v, v]) })}{field('parity', '校验位', { options: ['N', 'E', 'O', 'M', 'S'].map(v => [v, v]) })}</div>
                                <div className="form-grid">{field('stopbits', '停止位', { options: ['1', '1.5', '2'].map(v => [v, v]) })}{field('flow', '流控', { options: [['none', '无'], ['rtscts', 'RTS/CTS'], ['dsrdtr', 'DSR/DTR']] })}</div>
                                <SlidingContent value={settings.mode} order={['tcp-client', 'tcp-server', 'udp']}>{settings.mode === 'udp' && field('udp_payload', 'UDP 分包载荷 / B')}</SlidingContent>
                                {([['timeout', 'COM 读超时 / s'], ['write_timeout', 'COM 写超时 / s'], ['reconnect', '重连间隔 / s'], ['connect_timeout', '连接超时 / s'], ['keepalive_idle', 'Keepalive 空闲 / s'], ['keepalive_interval', 'Keepalive 间隔 / s'], ['keepalive_count', 'Keepalive 次数'], ['bufsize', '单次读缓冲 / B'], ['stats_interval', '统计刷新间隔 / s']] as string[][]).map(([key, label]) => field(key, label))}
                                {device === 'esp01s' && <>{field('control_port', '复位控制端口')}<Toggle checked={!!settings.no_reset_ui} onChange={value => change('no_reset_ui', value)}>关闭远程复位入口</Toggle></>}
                                <Toggle checked={!!settings.verbose} onChange={value => change('verbose', value)}>调试日志</Toggle>
                            </div>
                        </LiquidDisclosure>
                    </fieldset>
                    <div className="connection-actions">
                        {!snap.running ? <button className="button primary full" disabled={waiting || !ready} onClick={() => run(() => call('bridge', 'start'))}>{working ? <LoaderCircle size={15} className="spin" /> : <Play size={14} fill="currentColor" />}启动桥接<ArrowRight size={15} /></button>
                            : <div className="form-grid"><button className="button primary" disabled={waiting} onClick={() => run(() => call('bridge', 'restart'))}><RefreshCw size={14} />应用并重启</button><button className="button" disabled={waiting} title="停止业务桥接并关闭 USB" onClick={() => run(() => call('bridge', 'stop'))}><Square size={12} />停止</button></div>}
                        <p className="connection-note"><ShieldCheck size={12} />{snap.running ? snap.applied ? '当前参数已应用' : '参数有修改，应用并重启后生效' : '手动启动连接 · 不创建虚拟串口'}</p>
                    </div>
                </GlassSurface>
            </aside>

            <section className="center-column">
                <div className="workspace-toolbar"><LiquidSegments className="workspace-tabs" label="工作区视图" value={tab} onChange={setTab}
                    items={[{ value: 'logs', label: <><Terminal size={15} />运行日志</> }, { value: 'traffic', label: <><Activity size={15} />流量监测</> }]} /><div className="connection-status" data-state={connectionState} role="status" aria-label="网络连接状态" aria-live="polite" title={snap.address}>
                    {udpMode ? <Radio size={15} /> : tcpConnected ? <Wifi size={15} /> : snap.running ? <RefreshCw size={15} /> : <Unplug size={15} />}
                    <span>{connectionLabel}</span>
                </div></div>
                <section className="panel terminal-panel">
                    <SlidingContent value={tab} order={['logs', 'traffic']} fill className="workspace-content">
                    {tab === 'logs' ? <>
                        <div className="log-toolbar"><label className="search-field"><Search size={14} /><input aria-label="搜索日志" placeholder="搜索日志…" value={search} onChange={e => setSearch(e.target.value)} /></label>
                            <div className="log-tools"><button className={`icon-button ${!autoscroll ? 'pressed' : ''}`} title={autoscroll ? '暂停自动滚动' : '恢复自动滚动'} aria-label={autoscroll ? '暂停自动滚动' : '恢复自动滚动'} onClick={() => setAutoscroll(!autoscroll)}>{autoscroll ? <Pause size={15} /> : <Play size={15} />}</button>
                                <button className="icon-button" title="导出全部缓存日志" aria-label="导出日志" onClick={() => run(async () => download('wifi-bridge.log', await call('export_logs')))}><Download size={15} /></button>
                                <button className="icon-button" title="清空日志" aria-label="清空日志" onClick={() => run(async () => { await call('clear_logs'); setLogs([]); })}><Trash2 size={15} /></button></div>
                        </div>
                        <div className="log-sources"><LiquidSegments className="log-filter" label="日志来源" value={source} onChange={setSource}
                            items={[{ value: 'all', label: <>全部<span>{logs.length}</span></> }, { value: 'host', label: '上位机' }, { value: 'usb', label: 'ESP（USB）' }]} /><span className="log-retention">最近 3,000 行</span></div>
                        <SlidingContent value={source} order={['all', 'host', 'usb']} fill className="log-content">
                        <div className="log-body" data-slide-scroll="logs" ref={logBody} role="log" aria-label="运行日志" aria-live="off">
                            {visibleLogs.length ? logRows : <div className="empty-log"><span className="empty-symbol"><Terminal size={27} strokeWidth={1.3} /></span><h2>{search || source !== 'all' ? '没有匹配的日志' : '等待连接，准备就绪'}</h2><p>{search || source !== 'all' ? '试试其他关键词或切换日志来源。' : '启动桥接，或单独连接 USB。\n连接事件与设备日志将在这里显示。'}</p><span className="terminal-prompt"><i />READY WHEN YOU ARE</span></div>}
                        </div>
                        </SlidingContent>
                        <div className="terminal-footer"><span><i className={`status-dot ${snap.connected ? 'online' : ''}`} />{autoscroll ? '自动滚动' : '已暂停滚动'}</span><span>UTF-8<span className="footer-dot">·</span>{visibleLogs.length} 行{snap.log_dropped > 0 && ` · 队列丢弃 ${snap.log_dropped}`}</span></div>
                    </> : <div className="traffic-view" data-slide-scroll="traffic"><div className="traffic-title"><div><h2>链路流量</h2><p>桥接实际字节统计 · 最近 30 秒</p></div><span className={`live-label ${trafficActive ? 'active' : snap.running ? 'paused' : ''}`}><i />{trafficActive ? 'LIVE' : snap.running ? 'PAUSED' : 'IDLE'}</span></div>
                        <Chart samples={samples} index={0} label="网络接收" color="var(--chart-receive)" />
                        <Chart samples={samples} index={1} label="COM 写入" color="var(--chart-write)" />
                        <Chart samples={samples} index={2} label="网络发送" color="var(--chart-send)" />
                        {udpMode && <p className="traffic-note">UDP 桥接运行时更新曲线；已绑定不代表设备在线。</p>}
                    </div>}
                    </SlidingContent>
                </section>
                <div className="runtime-line"><Network size={13} /><span>{snap.address}</span>{snap.running_settings && <span className="runtime-com">{snap.running_settings.com} / {snap.running_settings.baud}</span>}</div>
            </section>

            <aside className="device-column">
                <GlassSurface className="panel usb-panel"><div className="panel-title"><span><Usb size={17} />USB 连接</span><Badge active={usbOnline} warning={usbActive && !usbOnline}>{snap.usb?.closing ? '关闭中' : usbOnline ? '已连接' : usbActive ? '重试中' : '未连接'}</Badge></div>
                    {caps ? <><div className="form-stack">{field('usb_port', '原生 USB COM', { list: 'serial-ports', disabled: waiting })}</div>
                        <button className={`button full ${usbActive ? '' : 'secondary'}`} disabled={working || snap.busy || (snap.config_busy && !usbActive)} onClick={() => run(() => call('usb', !usbActive))}>{usbActive ? <Unplug size={15} /> : <Usb size={15} />}{usbActive ? '断开 USB' : '连接 USB'}</button>
                        <div className="usb-caption"><span>独立于业务桥接</span><button className="text-button" onClick={() => setDrawer('diagnostics')}>诊断<ArrowUpRight size={12} /></button></div></> : <p className="muted small-copy">ESP-01S 使用业务串口和网络连接。</p>}
                </GlassSurface>

                <GlassSurface className="panel device-panel"><div className="panel-title"><span><Settings2 size={17} />设备配置</span><span className="section-number">02</span></div>
                    <div className="device-identity"><span className="chip-icon"><Radio size={24} strokeWidth={1.4} /></span><div><h2>{labels[device]}</h2><p>{device === 'c5' ? '双频 Wi-Fi · UART / SPI' : device === 's3' ? '2.4 GHz Wi-Fi · UART / SPI' : '2.4 GHz Wi-Fi · UART'}</p></div></div>
                    {caps ? <><LiquidSegments className="transport-switch" label="设备配置连接方式" value={settings.config_transport} disabled={waiting}
                        onChange={next => change('config_transport', next)} items={[{ value: 'usb', label: 'USB 配置' }, { value: 'http', label: 'Wi-Fi / HTTP' }]} />
                        <SlidingContent value={settings.config_transport} order={['usb', 'http']} className="transport-content">
                        {settings.config_transport === 'http' && <div className="http-field">{field('config_url', '设备配置地址', { disabled: waiting })}</div>}
                        <div className="device-read-state"><span className={`status-dot ${snap.baseline ? 'online' : ''}`} />{snap.baseline ? `已读取 · 修订 ${snap.baseline.revision ?? '—'}` : '尚未读取设备配置'}</div>
                        </SlidingContent>
                        <div className="device-facts"><div><span>业务接口</span><strong>{snap.baseline ? (linkOptions.find(([value]) => value === snap.baseline.link_mode)?.[1] ?? '接口已移除') : '—'}</strong></div><div><span>联网模式</span><strong>{snap.baseline ? ['自建热点 AP', '电脑热点 STA', '共享 Wi-Fi STA'][snap.baseline.mode] : '—'}</strong></div></div>
                        <button className="button secondary full" disabled={waiting} onClick={() => run(readConfig)}>{working ? <LoaderCircle size={14} className="spin" /> : <RefreshCw size={14} />}读取设备配置</button>
                        <button className="button full" onClick={() => setDrawer('config')}><SlidersHorizontal size={14} />打开配置面板<ArrowRight size={14} /></button>
                        <p className="setup-note"><Info size={12} /><span>配网：{device === 'c5' ? 'Key' : 'GPIO4'} 长按 3 秒进入配网。</span></p>
                    </> : <p className="setup-note">ESP-01S 保留透传和远程复位功能。</p>}
                </GlassSurface>

                <GlassSurface className="reset-card"><div><span><Zap size={15} />远程复位</span><small>复位连接的外部主控</small></div><button className="icon-button" title="打开远程复位" aria-label="打开远程复位" onClick={() => { setResetHost(settings.host || ''); setDrawer('reset'); }}><ArrowUpRight size={18} /></button></GlassSurface>
            </aside>
        </main>

        <GlassSurface className="metrics-strip" aria-label="桥接统计">
            {rates.map((rate, index) => <div className="metric" key={index}><span className="metric-label">{index === 0 ? <ArrowDownLeft size={14} /> : index === 1 ? <Cable size={14} /> : <ArrowUpRight size={14} />}{['网络接收', 'COM 写入', '网络发送'][index]}</span><strong>{size(rate, true)}</strong><small>{['来自 Wi-Fi 链路', '交付本地串口', '发往 Wi-Fi 链路'][index]}</small></div>)}
            <div className="metric"><span className="metric-label"><History size={14} />待写队列</span><strong>{size(snap.queued)}</strong><small>峰值 {size(snap.peak)}</small></div>
            <div className={`metric ${dropped ? 'has-warning' : ''}`}><span className="metric-label"><Activity size={14} />累计丢弃</span><strong>{size(dropped)}</strong><button className="text-button" onClick={() => setDrawer('diagnostics')}>查看链路诊断<ArrowUpRight size={11} /></button></div>
        </GlassSurface>
        <footer className="page-footer"><span><Check size={12} />{native ? snap.state_status : '本地工具 · 数据留在你的电脑'}</span><span>ESP-01S / ESP32-C5 / ESP32-S3</span></footer>

        <datalist id="serial-ports">{ports.map(port => <option key={port.device} value={port.device}>{port.description}</option>)}</datalist>
        <datalist id="baud-rates">{[9600, 115200, 460800, 921600, 1152000, 2000000, 3000000].map(value => <option key={value} value={value} />)}</datalist>

        {shownDrawer && <div className="drawer-backdrop liquid-overlay" data-state={drawerMotion.entered ? 'open' : 'closed'} aria-hidden={!drawer || undefined} inert={!drawer} onClick={() => { if (!confirm) setDrawer(null); }}>
            <div className="overlay-shade" aria-hidden="true" />
            <GlassSurface className={`drawer ${shownDrawer === 'config' ? 'wide' : ''}`} role="dialog" aria-modal="true" aria-label={{ config: '设备配置面板', diagnostics: '链路诊断', reset: '远程复位', help: '使用帮助' }[shownDrawer]} onClick={e => e.stopPropagation()}
                onTransitionEnd={event => {
                    if (!drawer && event.target === event.currentTarget && ['transform', 'clip-path'].includes(event.propertyName)) drawerMotion.finishExit();
                }}>
            <div className="drawer-heading"><div><div className="eyebrow">{shownDrawer === 'config' ? 'DEVICE CONFIGURATION' : shownDrawer === 'diagnostics' ? 'LINK DIAGNOSTICS' : shownDrawer === 'reset' ? 'REMOTE RESET' : 'QUICK START'}</div><h2>{{ config: `${labels[device]} 设备配置`, diagnostics: '链路诊断', reset: '远程复位', help: '开始使用工作台' }[shownDrawer]}</h2></div><button className="icon-button" aria-label="关闭面板" onClick={() => setDrawer(null)}><X size={20} /></button></div>
            <div className="drawer-body">
                {shownDrawer === 'config' && <>
                    <div className="notice"><Info size={16} /><span>{snap.config_status}{!snap.baseline && '。保存前必须从当前设备重新读取。'}</span></div>
                    <div className="config-toolbar"><button className="button primary" disabled={waiting} onClick={() => run(readConfig)}><RefreshCw size={14} />读取设备</button><button className="button" disabled={waiting || !snap.baseline} onClick={() => { const next = structuredClone(snap.baseline); setConfig(next); cacheDraft(next); setPasswords({ ap: '', pc: '', shared: '' }); setClear([]); }}><RotateCcw size={14} />撤销编辑</button><button className="button" disabled={waiting || !drafts[device]} onClick={restoreDraft}><History size={14} />恢复草稿</button></div>
                    {snap.baseline && <p className={`config-edit-state ${configDirty ? 'dirty' : ''}`}>{configDirty ? '已修改，尚未保存到设备' : snap.baseline.unsaved || snap.baseline.migration_requires_setup ? '当前运行配置待明确保存并重启' : '与本次读取的配置一致'}</p>}
                    <LiquidSegments className="config-tabs" label="配置分页" value={configTab} onChange={setConfigTab}
                        items={[{ value: 'common', label: '接口与联网' }, { value: 'ap', label: '自建热点' }, { value: 'pc', label: '电脑热点' }, { value: 'shared', label: '共享 Wi-Fi' }]} />
                    <SlidingContent value={configTab} order={['common', 'ap', 'pc', 'shared']} className="config-content">
                    {configTab === 'common' ? <div className="config-sections">
                        <div className="config-section"><h3>业务接口</h3><div className="form-grid">
                            {cf('link_mode', 'MCU 接口', { numeric: true, options: linkOptions })}
                            {device === 'c5' && cf('flow_pin', '串口流控线（高暂停 / 低允许）', { numeric: true, options: [[0, '未选择'], [1, 'SCK / GPIO23'], [2, 'MOSI / GPIO10'], [3, 'MISO / GPIO9'], [4, 'CS / GPIO5']] })}
                            {cf('baud', '设备 UART 波特率 / bps', { numeric: true })}
                            {device !== 'esp01s' && cf('reset_pin', '外部主控复位线', { numeric: true, options: [[0, '关闭'], [1, `SCK / GPIO${device === 's3' ? 18 : 23}`], [2, 'MOSI / GPIO10'], [3, 'MISO / GPIO9'], [4, 'CS / GPIO5']] })}
                        </div>{device === 'c5' && config.link_mode === 4 && <p>四线 SPI 仅发送 MCU 数据到网络；TCP 支持连续批量写入，UDP 每包最多 512 字节。UART 波特率、串口流控线和外部复位线在此模式下不启用。</p>}</div>
                        <div className="config-section"><h3>Wi-Fi 与网络</h3><div className="form-grid">
                            {cf('mode', '设备联网模式', { numeric: true, options: [[0, '自建热点 AP'], [1, '连接电脑热点'], [2, '连接共享 Wi-Fi']] })}
                            {cf('band', 'Wi-Fi 频段', { numeric: true, options: device === 's3' ? [[0, '2.4 GHz']] : [[0, '2.4 GHz'], [1, '5 GHz'], [2, '自动（AP 使用 2.4 GHz）']] })}
                            {cf('net_role', '设备网络角色', { numeric: true, options: [[0, 'TCP 服务端'], [1, 'TCP 客户端'], [2, '固定对端 UDP']] })}
                            {cf('tcp_port', 'TCP 服务端口', { numeric: true })}{cf('remote_ip', '设备目标 IP')}
                            {cf('remote_port', '设备目标端口', { numeric: true })}{cf('local_port', '设备本地端口（0 自动）', { numeric: true })}
                        </div></div>
                        <div className="config-section"><h3>恢复与缓冲</h3><Toggle checked={!!config.auto_reconnect} disabled={waiting || (!!snap.baseline && !('auto_reconnect' in snap.baseline))} onChange={value => editConfig('auto_reconnect', value)}>设备自动重连</Toggle><div className="form-grid">
                            {cf('reconnect_ms', '重连间隔 / ms', { numeric: true })}{cf('connect_timeout_ms', '连接超时 / ms', { numeric: true })}
                            {cf('up_kb', '上行缓冲 / KiB', { numeric: true })}{cf('down_kb', '下行缓冲 / KiB', { numeric: true })}
                        </div><p className="muted small-copy">上行与下行缓冲总容量不超过 6144 KiB。</p></div>
                    </div> : configTab === 'ap' ? <div className="config-section"><h3>自建热点 AP</h3><div className="form-grid">
                        {cf('ap_ssid', '热点 SSID')}{cf('ap_channel', '信道', { numeric: true })}
                        <Field label="新密码（留空保留）" type="password" value={passwords.ap} disabled={waiting} onChange={value => setPasswords({ ...passwords, ap: value })} />
                        {cf('ap_ip', '热点 IP')}{cf('ap_gateway', '网关')}{cf('ap_netmask', '子网掩码')}
                    </div></div> : <div className="config-section"><h3>{configTab === 'pc' ? '电脑热点 STA' : '共享 Wi-Fi STA'}</h3><div className="form-grid">
                        {cf('ssid', 'SSID', {}, configTab)}<Field label="新密码（留空保留）" type="password" value={passwords[configTab]} disabled={waiting} onChange={value => setPasswords({ ...passwords, [configTab]: value })} />
                    </div><Toggle confirmation disabled={waiting} checked={clear.includes(configTab)} onChange={value => setClear(value ? [...clear, configTab] : clear.filter(name => name !== configTab))}>开放网络 / 明确清空原密码</Toggle>
                        <Toggle disabled={waiting} checked={!!config[configTab]?.static_ip} onChange={value => editConfig('static_ip', value, configTab)}>使用静态 IP（关闭时使用 DHCP）</Toggle>
                        <div className="form-grid">{[['ip', 'IP 地址'], ['gateway', '网关'], ['netmask', '子网掩码'], ['dns', 'DNS']].map(([key, label]) => cf(key, label, { disabled: waiting || !config[configTab]?.static_ip }, configTab))}</div>
                    </div>}
                    </SlidingContent>
                    <div className="backup-actions"><button className="text-button" disabled={waiting} onClick={() => run(async () => { const data = await call('backup_config', config); download(`${device}-config-backup.json`, JSON.stringify(data, null, 2), 'application/json'); })}><Download size={14} />备份配置</button><button className="text-button" disabled={waiting} onClick={() => importInput.current?.click()}><FileJson size={14} />导入备份</button><small>备份与草稿均不含密码</small></div>
                    <input type="file" hidden ref={importInput} accept=".json,application/json" onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) run(async () => { if (file.size > 131072) throw new Error('备份文件过大'); const next = await call('import_config', await file.text()); setConfig(next); cacheDraft(next); setPasswords({ ap: '', pc: '', shared: '' }); setClear([]); await pendingDraft.current; notify('已导入编辑区；尚未写入设备'); }); }} />
                </>}
                {shownDrawer === 'diagnostics' && <>
                    <h3>串口与队列</h3><dl className="diagnostic-list">{[
                        ['网络→COM 待写', size(snap.queued)], ['队列历史峰值', size(snap.peak)],
                        ['COM 写平均 / 最大', `${stats.serial_write_calls ? Math.round(stats.serial_write_us / stats.serial_write_calls) : 0} / ${stats.serial_write_max_us || 0} μs`],
                        ['正在写入', `${size(stats.serial_write_pending_bytes || 0)} / ${stats.serial_write_pending_us || 0} μs`],
                        ['短写 / 超时', `${stats.serial_write_short || 0} / ${stats.serial_write_timeouts || 0}`],
                        ['UDP 收 / 发 / 拒绝', `${stats.udp_rx_packets || 0} / ${stats.udp_tx_packets || 0} / ${stats.udp_rejected || 0}`],
                        ['日志队列丢弃', `${snap.log_dropped} 条`],
                    ].map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>
                    {caps && <><h3>USB 设置</h3><fieldset disabled={waiting} className="form-stack">{field('usb_baud', 'USB 波特率')}{field('usb_log', '追加日志文件路径（可选）')}</fieldset><button className="button" disabled={waiting} onClick={() => run(() => call('usb', true))}>应用 USB 参数</button>
                        <h3>固件诊断字段</h3><div className={`notice ${usbStale ? 'warning' : ''}`}><Info size={15} /><span>{!snap.usb ? 'USB 未连接' : !usbOnline ? 'USB 离线 / 重试中，旧数值不代表设备在线' : snap.usb.age == null ? '尚未收到 stats' : `stats ${snap.usb.age.toFixed(1)} 秒前更新${usbStale ? '（已过期）' : ''}`}</span></div>
                        <dl className="diagnostic-list">{Object.entries(snap.usb?.fields || {}).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>)}</dl></>}
                </>}
                {shownDrawer === 'reset' && <><div className="notice"><Zap size={16} /><span>在 3 秒内连续点击 5 次触发复位。</span></div>{device !== 'esp01s' && <div className="form-stack"><Field label="设备 IPv4 地址" value={resetHost} disabled={snap.reset.busy} onChange={setResetHost} /><Field label="控制端口" value={resetPort} disabled={snap.reset.busy} onChange={setResetPort} /></div>}
                    <div className="reset-progress">{Array.from({ length: 5 }, (_, i) => <i key={i} className={i < snap.reset.count ? 'filled' : ''} />)}</div>
                    <button className="button primary full" disabled={snap.reset.busy} onClick={() => run(() => call('reset_click', resetHost, resetPort))}><Zap size={15} />{snap.reset.busy ? '复位请求进行中…' : `复位外部主控 · ${snap.reset.count}/5`}</button><p className="small-copy">{snap.reset.message}</p>
                </>}
                {shownDrawer === 'help' && <><div className="help-step"><span>01</span><div><h3>选择设备与连接角色</h3><p>TCP 客户端主动连接设备；TCP 服务端等待设备连接；UDP 使用固定对端。</p></div></div><div className="help-step"><span>02</span><div><h3>设置业务 COM，启动桥接</h3><p>程序打开已有串口。需要虚拟串口时，请先创建串口对。电脑本地波特率不会修改设备 UART。</p></div></div><div className="help-step"><span>03</span><div><h3>按需连接 USB</h3><p>C5/S3 的 USB 用于日志和配置，可独立运行。使用 ESP-IDF monitor 前先断开 USB，等待释放端口。</p></div></div><div className="help-step"><span>04</span><div><h3>读取、编辑、明确保存</h3><p>进入配网后读取当前设备，再编辑并保存。保存会写入 NVS 并重启；保存结果不明时先重新读取核对。</p></div></div><div className="notice"><Info size={16} /><span>关闭主窗口会停止桥接并释放串口。最小化窗口继续转发。停止按钮同时关闭业务桥接与 USB。</span></div></>}
            </div>
            {shownDrawer === 'config' && <div className="drawer-footer"><span><ShieldCheck size={14} />明确保存后写入设备 NVS</span><button className="button primary" disabled={waiting || !snap.baseline || (device === 'c5' && config.link_mode === -1)} onClick={() => run(async () => { const preview = await call<Data>('preview_save', config, passwords, clear); if (!preview.needed) notify('没有修改，无需保存'); else setConfirm({ ...preview, config: structuredClone(config), passwords: { ...passwords }, clear: [...clear] }); })}><Check size={15} />保存并重启</button></div>}
        </GlassSurface></div>}

        {shownConfirm && <div className="modal-backdrop liquid-overlay" data-state={confirmMotion.entered ? 'open' : 'closed'} aria-hidden={!confirm || undefined} inert={!confirm}><div className="overlay-shade" aria-hidden="true" /><GlassSurface className="confirm-dialog" role="dialog" aria-modal="true" aria-label="确认保存设备配置" onTransitionEnd={event => {
            if (!confirm && event.target === event.currentTarget && ['transform', 'opacity'].includes(event.propertyName)) confirmMotion.finishExit();
        }}><div className="confirm-icon"><Settings2 size={22} /></div><h2>保存配置并重启？</h2><p>以下修改将写入 {labels[device]} 的 NVS。<br />设备重启期间业务连接会中断。</p><div className="change-list">{shownConfirm.changes.length ? shownConfirm.changes.map((item: Data) => <div key={item.field}><span>{item.field}</span><strong>{item.value}</strong></div>) : <p>明确保存当前运行配置</p>}</div><div className="dialog-actions"><button className="button" disabled={working} onClick={() => setConfirm(null)}>取消</button><button className="button primary" disabled={working} onClick={() => run(async () => { const pending = shownConfirm; setConfirm(null); setPasswords({ ap: '', pc: '', shared: '' }); setClear([]); await call('save_device', pending.config, pending.passwords, pending.clear, pending.epoch); notify('设备已确认保存并请求重启，请重新读取核对'); })}>{working && <LoaderCircle size={14} className="spin" />}确认保存并重启</button></div></GlassSurface></div>}
        {toast && <div className="toast" role="status"><Info size={16} /><span>{toast}</span><button className="icon-button" aria-label="关闭提示" onClick={() => setToast('')}><X size={14} /></button></div>}
    </div>;
}
