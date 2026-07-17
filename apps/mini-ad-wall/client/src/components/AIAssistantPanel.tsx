import React, { useEffect, useMemo, useState } from 'react';
import { chatWithAssistant, getAssistantStatus } from '../api';
import { Ad, AssistantMessage, AssistantStatus } from '../types';

interface AIAssistantPanelProps {
    ads: Ad[];
}

const QUICK_PROMPTS = [
    '分析当前广告表现，给出三个优化动作',
    '哪些广告应该提高出价，哪些应该先改素材？',
    '帮我生成下一轮 A/B 测试计划'
];

function AIAssistantPanel({ ads }: AIAssistantPanelProps) {
    const [open, setOpen] = useState(false);
    const [input, setInput] = useState('');
    const [loading, setLoading] = useState(false);
    const [convId, setConvId] = useState<string | undefined>();
    const [status, setStatus] = useState<AssistantStatus | null>(null);
    const [messages, setMessages] = useState<AssistantMessage[]>([
        {
            role: 'assistant',
            content: '我是广告运营助手，可以结合当前广告数据给出投放、素材和出价建议。'
        }
    ]);

    const summary = useMemo(() => {
        const clicks = ads.reduce((sum, ad) => sum + Number(ad.clicks || 0), 0);
        const videos = ads.reduce((sum, ad) => sum + (ad.videos?.length || 0), 0);
        return { clicks, videos };
    }, [ads]);

    useEffect(() => {
        if (!open) {
            return;
        }

        getAssistantStatus()
            .then(setStatus)
            .catch(() => {
                setStatus({
                    available: false,
                    url: '',
                    message: '无法读取助手状态'
                });
            });
    }, [open]);

    const sendMessage = async (message: string) => {
        const text = message.trim();
        if (!text || loading) {
            return;
        }

        setInput('');
        setLoading(true);
        setMessages(prev => [...prev, { role: 'user', content: text }]);

        try {
            const result = await chatWithAssistant({
                message: text,
                userId: 'mini-ad-manager',
                convId,
                ads
            });
            if (result.convId) {
                setConvId(result.convId);
            }
            setMessages(prev => [...prev, {
                role: 'assistant',
                content: result.response,
                source: result.source
            }]);
        } catch (error) {
            setMessages(prev => [...prev, {
                role: 'assistant',
                content: '助手暂时不可用，请确认 MiniAddwall 后端正在运行。',
                source: 'local'
            }]);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className={`assistant-shell ${open ? 'open' : ''}`}>
            {open && (
                <section className="assistant-panel" aria-label="广告运营助手">
                    <div className="assistant-header">
                        <div>
                            <div className="assistant-title">广告运营助手</div>
                            <div className="assistant-meta">{ads.length} 条广告 · {summary.clicks} 次点击 · {summary.videos} 个素材</div>
                        </div>
                        <button className="assistant-icon-btn" type="button" onClick={() => setOpen(false)} aria-label="关闭助手">×</button>
                    </div>

                    {status && (
                        <div className={`assistant-status ${status.available ? 'online' : 'offline'}`}>
                            <span className="assistant-status-dot" />
                            <span>{status.available ? 'MiniAdsWall Agent 已连接' : '本地兜底模式'}</span>
                            {!status.available && <span className="assistant-status-reason">{status.message}</span>}
                        </div>
                    )}

                    <div className="assistant-prompts">
                        {QUICK_PROMPTS.map(prompt => (
                            <button key={prompt} type="button" onClick={() => sendMessage(prompt)} disabled={loading}>
                                {prompt}
                            </button>
                        ))}
                    </div>

                    <div className="assistant-messages">
                        {messages.map((message, index) => {
                            const lines = message.content.split('\n');
                            return (
                                <div key={index} className={`assistant-message ${message.role}`}>
                                    <div className="assistant-bubble">
                                        {lines.map((line, lineIndex) => (
                                            <React.Fragment key={lineIndex}>
                                                {line}
                                                {lineIndex < lines.length - 1 && <br />}
                                            </React.Fragment>
                                        ))}
                                    </div>
                                    {message.source && (
                                        <div className="assistant-source">{message.source === 'adsAgent' ? 'AdsAgent' : '本地诊断'}</div>
                                    )}
                                </div>
                            );
                        })}
                        {loading && (
                            <div className="assistant-message assistant">
                                <div className="assistant-bubble">正在分析当前广告数据...</div>
                            </div>
                        )}
                    </div>

                    <form className="assistant-input-row" onSubmit={(event) => { event.preventDefault(); sendMessage(input); }}>
                        <input
                            value={input}
                            onChange={(event) => setInput(event.target.value)}
                            placeholder="询问投放、素材或出价建议"
                            disabled={loading}
                        />
                        <button type="submit" disabled={loading || !input.trim()}>发送</button>
                    </form>
                </section>
            )}

            <button className="assistant-fab" type="button" onClick={() => setOpen(prev => !prev)} aria-label="打开广告运营助手">
                AI
            </button>
        </div>
    );
}

export default AIAssistantPanel;
