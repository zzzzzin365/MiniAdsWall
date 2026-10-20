import { usePerformance } from '../contexts/PerformanceContext';
import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { hostedIdentity, hostedSessions, hostedNewSession, hostedHistory, hostedCreateRun,
    hostedRun, hostedEvents, hostedCancel, hostedResume, hostedApprove, hostedRecordText, HostedSession, HostedRun } from '../api';
import { Ad, AssistantMessage } from '../types';
import { hostedMemoryPreferences, hostedMemorySet, hostedMemoryClear, hostedMemoryCandidates,
    hostedMemoryDecide, hostedMemoryJobs, hostedMemoryReplay, hostedProcedures, hostedProcedureCreate,
    hostedProcedureDecide, MemoryPreference, MemoryCandidate, MemoryJob, ReviewedProcedure } from '../api';


const QUICK_PROMPTS = ['分析当前广告表现，给出三个优化动作', '哪些广告应该提高出价，哪些应该先改素材？', '帮我生成下一轮 A/B 测试计划'];
const TERMINAL = new Set(['succeeded', 'failed', 'cancelled', 'timed_out', 'interrupted']);
const LABELS: Record<string, string> = { queued: '等待执行', running: '正在处理', preflight: '正在检查请求', model: '正在生成回复',
    stopping: '正在停止并清理任务', succeeded: '已完成', failed: '执行失败', cancelled: '已取消', timed_out: '执行超时', interrupted: '任务中断', waiting_approval: '等待确认' };

function AssistantText({ text }: { text: string }) {
    const inline = (value: string) => value.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
        part.startsWith('**') && part.endsWith('**') ? <strong key={i}>{part.slice(2, -2)}</strong> : part);
    return <div className="assistant-answer">{text.replace(/([。；;])(?=\S)/g, '$1\n').replace(/([^\n])\s*(窗口总计|缺失数据|统计范围|参考依据)[：:]/g, '$1\n$2：').split(/\n/).map((line, i) => {
        if (!line.trim()) return null;
        const heading = line.match(/^#{1,6}\s+(.+)/);
        if (heading) return <h4 key={i}>{inline(heading[1])}</h4>;
        const value = line.replace(/^\s*[-*]\s+/, '');
        const label = value.match(/^(建议|怎么做|为什么|看什么|统计范围|参考依据)[：:](.*)/);
        return <p key={i} className={label?.[1] === '建议' ? 'assistant-answer-summary' : undefined}>{label ? <><strong>{label[1]}：</strong>{inline(label[2])}</> : inline(value)}</p>;
    })}</div>;
}

function MemoryControls({ sid, disabled }: { sid: string; disabled: boolean }) {
    const [expanded, setExpanded] = useState(false);
    const [section, setSection] = useState<'preferences' | 'procedures' | 'status'>('preferences');
    const [preferences, setPreferences] = useState<MemoryPreference[]>([]);
    const [candidates, setCandidates] = useState<MemoryCandidate[]>([]);
    const [jobs, setJobs] = useState<MemoryJob[]>([]);
    const [procedures, setProcedures] = useState<ReviewedProcedure[]>([]);
    const [field, setField] = useState('优化目标');
    const [value, setValue] = useState('');
    const [notice, setNotice] = useState('');
    const [saving, setSaving] = useState(false);
    const [revision, setRevision] = useState(0);
    const [draft, setDraft] = useState({ title: '', objective: '', conditions: '', steps: '', limitations: '', source_title: '' });
    useEffect(() => {
        if (!expanded) return;
        let cancelled = false;
        setNotice('正在读取…');
        Promise.all([hostedMemoryPreferences(sid), hostedMemoryCandidates(sid), hostedMemoryJobs(sid), hostedProcedures(sid)])
            .then(([p, c, j, r]) => { if (!cancelled) { setPreferences(p); setCandidates(c); setJobs(j); setProcedures(r); setNotice(''); } })
            .catch(() => { if (!cancelled) setNotice('记忆暂时不可用，请稍后刷新'); });
        return () => { cancelled = true; };
    }, [sid, expanded, revision]);
    async function change(action: () => Promise<unknown>) {
        setSaving(true); setNotice('');
        try { await action(); setRevision(n => n + 1); }
        catch { setNotice('未完成：请检查权限，或刷新后重试；内容可能已被更新。'); }
        finally { setSaving(false); }
    }
    const locked = disabled || saving;
    const current = (name: string) => preferences.find(p => p.field === name);
    const activePreferences = preferences.filter(p => p.status === 'active');
    const pendingJobs = jobs.filter(j => j.status === 'dead' || j.status === 'retry');
    return <details className="assistant-memory" open={expanded} onToggle={event => setExpanded(event.currentTarget.open)}>
        <summary className="assistant-memory-heading">
            <span className="assistant-memory-icon" aria-hidden="true"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M8 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-2M8 3h8v4H8zM8 12h8M8 16h5" /></svg></span>
            <span className="assistant-memory-heading-text"><strong>偏好与操作经验</strong><span>让建议更贴合你的投放习惯</span></span>
            <span className="assistant-memory-toggle">{expanded ? '收起' : '管理'}<svg aria-hidden="true" width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="m5 3 5 5-5 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg></span>
        </summary>
        <div className="assistant-memory-body">
            <div className="assistant-memory-toolbar">
                <div className="assistant-memory-nav" aria-label="记忆设置分类">
                    {([{ id: 'preferences', label: '长期偏好' }, { id: 'procedures', label: '操作经验' }, { id: 'status', label: '处理状态' }] as const).map(item =>
                        <button key={item.id} type="button" aria-pressed={section === item.id} onClick={() => setSection(item.id)}>{item.label}{item.id === 'status' && pendingJobs.length > 0 && <span className="assistant-memory-count">{pendingJobs.length}</span>}</button>)}
                </div>
                <button className="assistant-memory-text-button" type="button" disabled={locked} onClick={() => setRevision(n => n + 1)}>刷新</button>
            </div>
            {notice && <p className="assistant-memory-notice" role="status">{notice}</p>}
            {section === 'preferences' && <>
                <p className="assistant-memory-hint">本轮要求优先，已保存的偏好仅用于辅助建议。</p>
                <div className="assistant-memory-fields">
                    <label>偏好类型<select value={field} disabled={locked} onChange={event => setField(event.target.value)}>
                        {['优化目标','关注指标','素材偏好','风险偏好','商品类目','广告活动','目标受众','素材类型'].map(name => <option key={name}>{name}</option>)}
                    </select></label>
                    <label>偏好内容<input placeholder="例如：优先使用真人口播素材" value={value} maxLength={120} disabled={locked} onChange={event => setValue(event.target.value)} /></label>
                    <button className="assistant-memory-primary" type="button" disabled={locked || !value.trim()} onClick={() => void change(() => hostedMemorySet(sid, field, [value.trim()], current(field)?.version || 0))}>{saving ? '保存中…' : '保存偏好'}</button>
                </div>
                <div className="assistant-memory-list">
                    {activePreferences.length === 0 && !notice && <p className="assistant-memory-empty">还没有长期偏好，添加后可用于后续建议。</p>}
                    {activePreferences.map(p => <div className="assistant-memory-item" key={p.id}><div><span className="assistant-memory-label">{p.field}</span><p>{JSON.parse(p.value_json).join('、')}</p></div>
                        <button className="assistant-memory-text-button" type="button" disabled={locked} onClick={() => void change(() => hostedMemorySet(sid, p.field, [], p.version))}>撤回</button></div>)}
                    {candidates.map(c => <div className="assistant-memory-candidate" key={c.id}><span className="assistant-memory-label">待确认 · {c.field}</span><p>{JSON.parse(c.value_json).join('、')}</p><div className="assistant-memory-actions">
                        <button type="button" disabled={locked} onClick={() => void change(() => hostedMemoryDecide(sid, c.id, true, current(c.field)?.version || 0))}>采用新偏好</button>
                        <button className="assistant-memory-text-button" type="button" disabled={locked} onClick={() => void change(() => hostedMemoryDecide(sid, c.id, false, current(c.field)?.version || 0))}>保留原偏好</button></div></div>)}
                </div>
                <div className="assistant-memory-footer"><span>记忆不会授权修改广告</span><button className="assistant-memory-danger" type="button" disabled={locked} onClick={() => void change(() => hostedMemoryClear(sid))}>清除摘要并停止积累</button></div>
            </>}
            {section === 'procedures' && <>
                <p className="assistant-memory-hint">保存通用做法，审核后供建议参考；不代表已经执行或效果有效。</p>
                <details className="assistant-memory-create"><summary>＋ 新增操作经验</summary><div className="assistant-memory-procedure-fields">
                    {(['title','objective','conditions','steps','limitations','source_title'] as const).map(key => <label key={key}>
                        {{title:'标题',objective:'适用目标',conditions:'必须满足的条件',steps:'操作步骤',limitations:'限制',source_title:'来源标题'}[key]}
                        <textarea rows={key === 'title' || key === 'objective' || key === 'source_title' ? 1 : 2} placeholder={key === 'conditions' || key === 'steps' || key === 'limitations' ? '每行填写一项' : undefined} value={draft[key]} maxLength={key === 'title' || key === 'objective' || key === 'source_title' ? 120 : 1200} disabled={locked}
                            onChange={event => setDraft(previous => ({ ...previous, [key]: event.target.value }))} />
                    </label>)}
                </div><button className="assistant-memory-primary" type="button" disabled={locked || Object.values(draft).some(v => !v.trim())} onClick={() => void change(() => hostedProcedureCreate(sid, {
                    ...draft, conditions: draft.conditions.split('\n').filter(Boolean), steps: draft.steps.split('\n').filter(Boolean), limitations: draft.limitations.split('\n').filter(Boolean)
                }))}>保存待审核流程</button></details>
                {procedures.length === 0 && !notice && <p className="assistant-memory-empty">还没有操作经验，可添加常用的投放方法。</p>}
                {procedures.map(p => <div className="assistant-memory-procedure" key={p.id}><div className="assistant-memory-item-heading"><strong>{p.title}</strong><span className={`assistant-memory-badge is-${p.status}`}>{{draft:'待审核',reviewed:'审核通过',withdrawn:'已撤回'}[p.status] || p.status}</span></div>
                    <p className="assistant-memory-label">来源：{p.source_title}</p>
                    <p>适用：{JSON.parse(p.conditions_json).join('；')}</p><p>步骤：{JSON.parse(p.steps_json).join('；')}</p><p>限制：{JSON.parse(p.limitations_json).join('；')}</p>
                    <div className="assistant-memory-actions">{p.status === 'draft' && <button type="button" disabled={locked} onClick={() => void change(() => hostedProcedureDecide(sid, p.id, 'review', p.version))}>确认审核通过</button>}
                    {p.status !== 'withdrawn' && <button className="assistant-memory-text-button" type="button" disabled={locked} onClick={() => void change(() => hostedProcedureDecide(sid, p.id, 'withdraw', p.version))}>撤回流程</button>}</div></div>)}
            </>}
            {section === 'status' && <>
                <p className="assistant-memory-hint">查看需要重新处理的记忆提取任务。</p>
                {pendingJobs.length === 0 && !notice && <p className="assistant-memory-empty">暂无需要重试的任务</p>}
                {pendingJobs.map(j => <div className="assistant-memory-item" key={j.id}><div><strong>记忆提取待重试</strong><p className="assistant-memory-label">已尝试 {j.attempts} 次</p></div>
                    <button type="button" disabled={locked} onClick={() => void change(() => hostedMemoryReplay(sid, j.id))}>重新处理</button></div>)}
            </>}
        </div>
    </details>;
}

function AIAssistantPanel({ ads }: { ads: Ad[] }) {
    const {measures,range}=usePerformance();
    const [open, setOpen] = useState(false);
    const shell = useRef<HTMLDivElement>(null);
    const [position, setPosition] = useState<{ right: number; bottom: number } | null>(null);
    const drag = useRef<{ id: number; x: number; y: number; right: number; bottom: number; moved: boolean } | null>(null);
    const suppressClick = useRef(false);
    const [dragging, setDragging] = useState(false);
    const clampPosition = (right: number, bottom: number) => {
        const rect = shell.current?.getBoundingClientRect();
        if (!rect) return { right, bottom };
        return { right: Math.max(12, Math.min(right, window.innerWidth - rect.width - 12)),
            bottom: Math.max(12, Math.min(bottom, window.innerHeight - rect.height - 12)) };
    };
    useLayoutEffect(() => {
        const constrain = () => {
            const rect = shell.current?.getBoundingClientRect();
            if (!rect || !rect.width) return;
            const right = window.innerWidth - rect.right, bottom = window.innerHeight - rect.bottom;
            const next = clampPosition(right, bottom);
            if (Math.abs(next.right - right) > .5 || Math.abs(next.bottom - bottom) > .5) setPosition(next);
        };
        constrain();
        const observer = new ResizeObserver(constrain);
        if (shell.current) observer.observe(shell.current);
        window.addEventListener('resize', constrain);
        return () => { observer.disconnect(); window.removeEventListener('resize', constrain); };
    }, [open]);
    function startDrag(event: React.PointerEvent<HTMLElement>, header = false) {
        if (!event.isPrimary || event.button !== 0 || (header && (event.target as Element).closest('button, input, select, textarea, a'))) return;
        const rect = shell.current?.getBoundingClientRect();
        if (!rect) return;
        suppressClick.current = false;
        drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY,
            right: window.innerWidth - rect.right, bottom: window.innerHeight - rect.bottom, moved: false };
        event.currentTarget.setPointerCapture(event.pointerId);
    }
    function moveDrag(event: React.PointerEvent<HTMLElement>) {
        const current = drag.current;
        if (!current || current.id !== event.pointerId) return;
        const dx = event.clientX - current.x, dy = event.clientY - current.y;
        if (!current.moved && Math.hypot(dx, dy) < 6) return;
        current.moved = true;
        setDragging(true);
        setPosition(clampPosition(current.right - dx, current.bottom - dy));
    }
    function endDrag(event: React.PointerEvent<HTMLElement>) {
        if (!drag.current || drag.current.id !== event.pointerId) return;
        suppressClick.current = drag.current.moved;
        drag.current = null;
        setDragging(false);
        if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    }
    const [panelSize, setPanelSize] = useState<{ width: number; height: number }>();
    const resizing = useRef<{ edge: string; x: number; y: number; width: number; height: number; right: number; bottom: number }>();
    function beginResize(event: React.PointerEvent<HTMLDivElement>, edge: string) {
        if (event.button !== 0) return;
        const panel = event.currentTarget.parentElement!.getBoundingClientRect();
        const rect = shell.current!.getBoundingClientRect();
        resizing.current = { edge, x: event.clientX, y: event.clientY, width: panel.width, height: panel.height,
            right: window.innerWidth - rect.right, bottom: window.innerHeight - rect.bottom };
        event.preventDefault();
        event.stopPropagation();
        event.currentTarget.setPointerCapture(event.pointerId);
    }
    function resizePanel(event: React.PointerEvent<HTMLDivElement>) {
        const r = resizing.current;
        if (!r) return;
        const dx = event.clientX - r.x, dy = event.clientY - r.y;
        const width = Math.max(420, Math.min(window.innerWidth - 24, r.width + (r.edge.includes('w') ? -dx : r.edge.includes('e') ? dx : 0)));
        const height = Math.max(460, Math.min(window.innerHeight - 180, r.height + (r.edge.includes('n') ? -dy : r.edge.includes('s') ? dy : 0)));
        setPanelSize({ width, height });
        setPosition({ right: Math.max(12, Math.min(window.innerWidth - width - 12, r.right - (r.edge.includes('e') ? width - r.width : 0))),
            bottom: Math.max(12, r.bottom - (r.edge.includes('s') ? height - r.height : 0)) });
    }
    function finishResize(event: React.PointerEvent<HTMLDivElement>) {
        resizing.current = undefined;
        if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    }
    const [input, setInput] = useState('');
    const [sessions, setSessions] = useState<HostedSession[]>([]);
    const [session, setSession] = useState<HostedSession>();
    const [identity, setIdentity] = useState<{ user_id: string; workspace_id: string }>();
    const [messages, setMessages] = useState<AssistantMessage[]>([]);
    const [historyCursor, setHistoryCursor] = useState<string>();
    const [run, setRun] = useState<HostedRun>();
    const [liveText, setLiveText] = useState('');
    const [stage, setStage] = useState('');
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const [connecting, setConnecting] = useState(false);
    const [approval, setApproval] = useState<string>();
    const [elapsed, setElapsed] = useState(0);
    const controller = useRef<AbortController>();
    const submitting = useRef(false);
    const mounted = useRef(true);
    const watching = useRef<string>();
    const summary = useMemo(() => ({ clicks: ads.reduce((n, ad) => n + measures(ad).clicks, 0),
        videos: ads.reduce((n, ad) => n + (ad.videos?.length || 0), 0) }), [ads,range]);

    useEffect(() => { mounted.current = true; return () => { mounted.current = false; controller.current?.abort(); }; }, []);
    useEffect(() => {
        if (!busy) return;
        const start = Date.now(); setElapsed(0);
        const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 1000);
        return () => window.clearInterval(timer);
    }, [busy]);

    async function loadHistory(id: string, cursor?: string) {
        const page = await hostedHistory(id, cursor);
        const items = await Promise.all(page.items.filter(item => item.record_type === 'message').reverse()
            .map(async item => ({ role: item.role!, content: await hostedRecordText(item) })));
        if (!mounted.current) return;
        setMessages(previous => cursor ? [...items, ...previous] : items);
        setHistoryCursor(page.next_cursor);
    }

    async function watch(initial: HostedRun) {
        controller.current?.abort();
        const abort = new AbortController(); controller.current = abort; watching.current = initial.id;
        setRun(initial); setLiveText(''); setError(''); setBusy(true); setStage(initial.status); setApproval(undefined);
        let after = 0; let stopped = false; let failures = 0;
        const current = () => mounted.current && controller.current === abort;
        try {
            while (!abort.signal.aborted && !stopped) {
                try {
                    await hostedEvents(initial.id, after, abort.signal, (seq, type, data) => {
                        if (!current()) return;
                        after = seq;
                        if (type === 'assistant.delta') setLiveText(text => text + data.text);
                        if (type === 'run.stage') setStage(data.stage);
                        if (type === 'tool.started') setStage(`正在执行 ${data.tool_name}`);
                        if (type === 'run.started') setStage('running');
                        if (type === 'approval.required') { setApproval(data.approval_id); setStage('waiting_approval'); stopped = true; abort.abort(); }
                        if (type === 'run.finished') { stopped = true; setStage(data.status); }
                    });
                    failures = 0;
                } catch (exception) {
                    if (abort.signal.aborted && !stopped) return;
                    if (!stopped) {
                        const state = await hostedRun(initial.id);
                        if (TERMINAL.has(state.status)) { stopped = true; setRun(state); setStage(state.status); }
                        else if (state.status === 'waiting_approval') { stopped = true; setApproval(state.approval_id); setStage(state.status); }
                        else if (exception instanceof Error && exception.message === 'events:410') {
                            throw new Error('进度记录已过期，请通过会话历史查看结果。');
                        } else if (++failures >= 5) throw new Error('进度连接中断，任务可能仍在执行。可以重新连接或取消。');
                    }
                }
                if (!stopped && !abort.signal.aborted) await new Promise(resolve => setTimeout(resolve, 1000));
            }
            if (current()) {
                const state = await hostedRun(initial.id); setRun(state); setStage(state.status);
                if (state.status === 'succeeded') window.dispatchEvent(new Event('ads-changed'));
                if (state.status === 'waiting_approval') setApproval(state.approval_id);
                await loadHistory(initial.session_id); setLiveText('');
                setSessions(await hostedSessions());
            }
        } catch (exception) {
            if (current()) setError(exception instanceof Error ? exception.message : '无法读取任务进度');
        } finally { if (current()) { setBusy(false); watching.current = undefined; } }
    }

    async function selectSession(selected: HostedSession) {
        controller.current?.abort(); setSession(selected); setRun(undefined); setApproval(undefined); setLiveText(''); setError('');
        await loadHistory(selected.id);
        if (selected.active_run_id) void watch(await hostedRun(selected.active_run_id));
    }
    async function connect() {
        if (connecting) return;
        setConnecting(true); setError('');
        try {
            const who = await hostedIdentity(); setIdentity(who);
            const list = await hostedSessions(); setSessions(list);
            if (list[0]) await selectSession(list[0]);
        } catch (exception) { setError(exception instanceof Error ? exception.message : '托管服务不可用'); }
        finally { setConnecting(false); }
    }
    // Opening the panel is an explicit user action, so requesting the existing operator credential is appropriate.
    function toggle() { setOpen(value => !value); if (!open && !identity) void connect(); }

    async function sendMessage(text: string) {
        if (!text.trim() || submitting.current || busy || !identity) return;
        submitting.current = true; setBusy(true); setError('');
        try {
            let target = session;
            if (!target) {
                target = await hostedNewSession(identity.workspace_id, text.slice(0, 80));
                setSession(target); setSessions(items => [target!, ...items]);
            }
            const storageKey = `agent:pending:${identity.user_id}:${target.id}`;
            const previous = sessionStorage.getItem(storageKey);
            const pending = previous ? JSON.parse(previous) : { text, range, key: crypto.randomUUID() };
            if (pending.text !== text) throw new Error('上一次提交结果待确认，请先重试原消息。');
            sessionStorage.setItem(storageKey, JSON.stringify(pending));
            const created = await hostedCreateRun(target.id, text, pending.key, pending.range ?? '7');
            sessionStorage.removeItem(storageKey); setInput('');
            await loadHistory(target.id); void watch(created);
        } catch (exception) {
            setBusy(false); setError(exception instanceof Error ? exception.message : '提交结果待确认，请重试原消息');
        } finally { submitting.current = false; }
    }
    async function cancel() {
        if (!run) return;
        try { const state = await hostedCancel(run.id); setRun(state); setStage(state.status); if (!busy) void watch(state); }
        catch { setError('取消请求未确认，请重试。'); }
    }
    async function decide(allow: boolean) {
        if (!approval || submitting.current) return;
        submitting.current = true;
        try { const state = await hostedApprove(approval, allow); setApproval(undefined); void watch(state); }
        catch { setError('审批未确认，请重试。'); }
        finally { submitting.current = false; }
    }
    async function resume() {
        if (!run || submitting.current) return;
        submitting.current = true;
        const key = `agent:resume:${run.id}`;
        const id = sessionStorage.getItem(key) || crypto.randomUUID(); sessionStorage.setItem(key, id);
        try { const state = await hostedResume(run.id, id); sessionStorage.removeItem(key); void watch(state); }
        catch { setError('恢复请求未确认或不可恢复，请重试或新建会话。'); }
        finally { submitting.current = false; }
    }
    return (
        <div ref={shell} className={`assistant-shell ${open ? 'open' : ''} ${dragging ? 'dragging' : ''}`} style={position ?? undefined}>
            {open && <section className="assistant-panel" aria-label="广告运营助手" title="拖动任意边缘或角落调整大小" style={panelSize}>
                <div className="assistant-header" title="拖拽移动助手" onPointerDown={event => startDrag(event, true)} onPointerMove={moveDrag} onPointerUp={endDrag} onPointerCancel={endDrag}>
                    <div><div className="assistant-title">广告运营助手</div>
                        <div className="assistant-meta">{ads.length} 条广告 · 最近 {range} 天投放指标 · {summary.clicks} 次点击</div></div>
                    <button className="assistant-icon-btn" type="button" onClick={() => setOpen(false)} aria-label="关闭助手">×</button>
                </div>
                <div className={`assistant-status ${identity ? 'online' : 'offline'}`}>
                    <span className="assistant-status-dot" /><span>{identity ? '会话已连接' : '尚未连接托管服务'}</span>
                    {!identity && <button type="button" onClick={() => void connect()} disabled={connecting}>连接</button>}
                </div>
                {identity && <div className="assistant-session-controls">
                    <select aria-label="选择历史会话" value={session?.id || ''} disabled={busy} onChange={event => {
                        const selected = sessions.find(item => item.id === event.target.value); if (selected) void selectSession(selected);
                    }}><option value="">新会话</option>{sessions.map(item => <option value={item.id} key={item.id}>{item.title}</option>)}</select>
                    <button type="button" disabled={busy || !!approval} onClick={() => { setSession(undefined); setMessages([]); setRun(undefined); setHistoryCursor(undefined); setStage(''); }}>新建会话</button>
                </div>}
                {identity && session && <MemoryControls key={session.id} sid={session.id} disabled={busy} />}
                <div className="assistant-prompts">{QUICK_PROMPTS.map(prompt => <button key={prompt} type="button" onClick={() => void sendMessage(prompt)} disabled={busy || !identity || !!approval}>{prompt}</button>)}</div>
                <div className="assistant-messages" aria-live="polite">
                    {historyCursor && session && <button type="button" onClick={() => void loadHistory(session.id, historyCursor)}>加载更早记录</button>}
                    {!messages.length && <div className="assistant-message assistant"><div className="assistant-bubble">结合当前广告、看板所选周期的投放指标和营销知识库，给出有数据与来源依据的优化建议。</div></div>}
                    {messages.map((message, index) => <div key={index} className={`assistant-message ${message.role}`}><div className="assistant-bubble">{message.role === 'assistant' ? <AssistantText text={message.content} /> : message.content}</div></div>)}
                    {liveText && <div className="assistant-message assistant"><div className="assistant-bubble"><AssistantText text={liveText} /></div></div>}
                    {error && <div role="alert">{error}</div>}
                </div>
                {stage && <div className="assistant-run-status" role="status">{LABELS[stage] || stage}{busy && ` · ${elapsed} 秒`}</div>}
                {run && !TERMINAL.has(run.status) && <div className="assistant-decision-actions">
                    <button type="button" onClick={() => void cancel()}>取消任务</button>
                    {!busy && !approval && <button type="button" onClick={() => void watch(run)}>重新连接</button>}
                </div>}
                {run && TERMINAL.has(run.status) && run.status !== 'succeeded' && !busy && <div className="assistant-decision-actions"><button type="button" onClick={() => void resume()}>恢复任务</button></div>}
                {approval && <div className="assistant-decision-actions" role="group" aria-label="确认高风险操作">
                    <button className="confirm" type="button" onClick={() => void decide(true)}>确认执行</button>
                    <button className="cancel" type="button" onClick={() => void decide(false)}>取消操作</button>
                </div>}
                <form className="assistant-input-row" onSubmit={event => { event.preventDefault(); void sendMessage(input); }}>
                    <input aria-label="发送给助手的消息" value={input} onChange={event => setInput(event.target.value)} placeholder="询问投放、素材或出价建议" disabled={busy || !!approval} />
                    <button type="submit" disabled={busy || !input.trim() || !identity || !!approval}>发送</button>
                </form>
                {['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'].map(edge => <div key={edge}
                    className={`assistant-resize assistant-resize-${edge}`} aria-hidden="true"
                    onPointerDown={event => beginResize(event, edge)} onPointerMove={resizePanel}
                    onPointerUp={finishResize} onPointerCancel={finishResize} />)}
            </section>}
            <button className="assistant-fab" type="button" onPointerDown={event => startDrag(event)} onPointerMove={moveDrag} onPointerUp={endDrag} onPointerCancel={endDrag} onClick={() => { if (suppressClick.current) { suppressClick.current = false; return; } toggle(); }} aria-label={open ? '关闭广告运营助手' : '打开广告运营助手'} aria-expanded={open} title="点击打开助手，拖拽移动">
                <picture>
                    <source media="(prefers-reduced-motion: reduce)" srcSet="/assets/assistant-rabbit-still.png" />
                    <img src="/assets/assistant-rabbit.webp" alt="" aria-hidden="true" draggable={false} />
                </picture>
            </button>
        </div>
    );
}
export default AIAssistantPanel;
