import config from '../config';
import { Ad, AssistantChatInput, AssistantChatOutput } from '../types';

interface AdsAgentChatResponse {
    conv_id: string;
    response: string;
    intent: string;
    agent_type: string;
    escalated: boolean;
    knowledge_used?: boolean;
    tools_used?: string[];
}

async function getStatus(): Promise<{
    available: boolean;
    url: string;
    message: string;
    agents?: unknown;
}> {
    const baseUrl = config.ADS_AGENT_API_URL.replace(/\/$/, '');

    try {
        const res = await fetch(`${baseUrl}/health`);
        if (!res.ok) {
            return {
                available: false,
                url: baseUrl,
                message: `MiniAdsWall Agent HTTP ${res.status}`
            };
        }

        const data = await res.json() as { status?: string; agents?: unknown };
        return {
            available: data.status === 'ok',
            url: baseUrl,
            message: data.status === 'ok' ? 'MiniAdsWall Agent 已连接' : 'MiniAdsWall Agent 状态异常',
            agents: data.agents
        };
    } catch (error) {
        const message = error instanceof Error ? error.message : 'unknown error';
        return {
            available: false,
            url: baseUrl,
            message
        };
    }
}

function buildAgentMessage(input: AssistantChatInput): string {
    return [
        '你是 Mini Ad Manager 的广告运营助手。请基于 MiniAdsWall Agent 的广告工具分析和知识库结果回答。',
        '如果用户询问优化建议，请优先给出标题、出价、素材和下一步实验建议。',
        '',
        `[用户问题]\n${input.message}`
    ].join('\n');
}

function localFallback(input: AssistantChatInput, reason?: string): AssistantChatOutput {
    const ads = input.ads || [];
    const totalClicks = ads.reduce((sum, ad) => sum + Number(ad.clicks || 0), 0);
    const noVideoCount = ads.filter(ad => !ad.videos || ad.videos.length === 0).length;
    const topAd = [...ads].sort((a, b) => Number(b.clicks || 0) - Number(a.clicks || 0))[0];
    const avgPrice = ads.length
        ? ads.reduce((sum, ad) => sum + Number(ad.price || 0), 0) / ads.length
        : 0;

    const suggestions = [
        `当前共有 ${ads.length} 条广告，总点击 ${totalClicks}，平均出价 ${avgPrice.toFixed(2)}。`,
        topAd ? `点击最高的是「${topAd.title}」，点击 ${topAd.clicks}，可以优先复用它的标题结构和素材方向。` : '当前还没有广告数据，建议先创建 3-5 条不同卖点的广告做初始测试。',
        noVideoCount > 0 ? `有 ${noVideoCount} 条广告没有绑定视频素材，建议补齐素材后再观察点击变化。` : '所有广告都已绑定视频素材，下一步可以比较不同素材长度与点击表现。',
        '出价优化上，优先提高高点击广告预算；低点击高出价广告应先改文案或素材，不建议直接继续加价。'
    ];

    return {
        convId: input.convId || null,
        response: `${suggestions.join('\n')}\n\n注：AdsAgent 当前不可用，已使用 MiniAddwall 本地诊断。${reason ? ` (${reason})` : ''}`,
        intent: 'local_analysis',
        agentType: 'local',
        escalated: false,
        knowledgeUsed: false,
        source: 'local'
    };
}

async function chat(input: AssistantChatInput): Promise<AssistantChatOutput> {
    const url = `${config.ADS_AGENT_API_URL.replace(/\/$/, '')}/chat`;

    try {
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: buildAgentMessage(input),
                user_id: input.userId || 'mini-ad-manager',
                conv_id: input.convId || undefined,
                ads: input.ads || []
            })
        });

        if (!res.ok) {
            const detail = await res.text();
            return localFallback(input, `AdsAgent HTTP ${res.status}: ${detail.slice(0, 120)}`);
        }

        const data = await res.json() as AdsAgentChatResponse;
        return {
            convId: data.conv_id,
            response: data.response,
            intent: data.intent,
            agentType: data.agent_type,
            escalated: data.escalated,
            knowledgeUsed: Boolean(data.knowledge_used),
            toolsUsed: data.tools_used || [],
            source: 'adsAgent'
        };
    } catch (error) {
        const message = error instanceof Error ? error.message : 'unknown error';
        return localFallback(input, message);
    }
}

export default {
    getStatus,
    chat
};
