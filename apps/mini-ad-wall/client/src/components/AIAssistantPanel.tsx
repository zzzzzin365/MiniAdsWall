import React, { useEffect, useMemo, useRef, useState } from 'react';
import { hostedIdentity, hostedSessions, hostedNewSession, hostedHistory, hostedCreateRun,
    hostedRun, hostedEvents, hostedCancel, hostedResume, hostedApprove, HostedSession, HostedRun } from '../api';
import { Ad, AssistantMessage } from '../types';

const QUICK_PROMPTS = ['分析当前广告表现，给出三个优化动作', '哪些广告应该提高出价，哪些应该先改素材？', '帮我生成下一轮 A/B 测试计划'];
const TERMINAL = new Set(['succeeded', 'failed', 'cancelled', 'timed_out', 'interrupted']);
const LABELS: Record<string, string> = { queued: '等待执行', running: '正在处理', preflight: '正在检查请求', model: '正在生成回复',
    stopping: '正在停止并清理任务', succeeded: '已完成', failed: '执行失败', cancelled: '已取消', timed_out: '执行超时', interrupted: '任务中断', waiting_approval: '等待确认' };

function AIAssistantPanel({ ads }: { ads: Ad[] }) {
    const [open, setOpen] = useState(false);
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
    const summary = useMemo(() => ({ clicks: ads.reduce((n, ad) => n + Number(ad.clicks || 0), 0),
        videos: ads.reduce((n, ad) => n + (ad.videos?.length || 0), 0) }), [ads]);

    useEffect(() => { mounted.current = true; return () => { mounted.current = false; controller.current?.abort(); }; }, []);
    useEffect(() => {
        if (!busy) return;
        const start = Date.now(); setElapsed(0);
        const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 1000);
        return () => window.clearInterval(timer);
    }, [busy]);

    async function loadHistory(id: string, cursor?: string) {
        const page = await hostedHistory(id, cursor);
        const items = page.items.filter(item => item.record_type === 'message').reverse()
            .map(item => ({ role: item.role!, content: item.content_preview || '' }));
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
            const pending = previous ? JSON.parse(previous) : { text, key: crypto.randomUUID() };
            if (pending.text !== text) throw new Error('上一次提交结果待确认，请先重试原消息。');
            sessionStorage.setItem(storageKey, JSON.stringify(pending));
            const created = await hostedCreateRun(target.id, text, pending.key);
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
        <div className={`assistant-shell ${open ? 'open' : ''}`}>
            {open && <section className="assistant-panel" aria-label="广告运营助手">
                <div className="assistant-header">
                    <div><div className="assistant-title">广告运营助手</div>
                        <div className="assistant-meta">{ads.length} 条广告 · {summary.clicks} 次点击 · {summary.videos} 个素材</div></div>
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
                <div className="assistant-prompts">{QUICK_PROMPTS.map(prompt => <button key={prompt} type="button" onClick={() => void sendMessage(prompt)} disabled={busy || !identity || !!approval}>{prompt}</button>)}</div>
                <div className="assistant-messages" aria-live="polite">
                    {historyCursor && session && <button type="button" onClick={() => void loadHistory(session.id, historyCursor)}>加载更早记录</button>}
                    {!messages.length && <div className="assistant-message assistant"><div className="assistant-bubble">我是广告运营助手，可以结合当前广告数据给出投放、素材和出价建议。</div></div>}
                    {messages.map((message, index) => <div key={index} className={`assistant-message ${message.role}`}><div className="assistant-bubble" style={{ whiteSpace: 'pre-wrap' }}>{message.content}</div></div>)}
                    {liveText && <div className="assistant-message assistant"><div className="assistant-bubble" style={{ whiteSpace: 'pre-wrap' }}>{liveText}</div></div>}
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
            </section>}
            <button className="assistant-fab" type="button" onClick={toggle} aria-label="打开广告运营助手">AI</button>
        </div>
    );
}
export default AIAssistantPanel;
