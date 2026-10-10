import config from '../config';
import { agentRequest } from './agentHttp';
import { Ad, AssistantChatInput, AssistantChatOutput } from '../types';

interface AdsAgentChatResponse {
    conv_id: string;
    response: string;
    intent: string;
    agent_type: string;
    decision: 'execute' | 'clarify' | 'reject' | 'confirm';
    decision_reason: string;
    missing_fields?: string[];
    confirmation_id?: string;
    risk_level: 'low' | 'medium' | 'high';
    action_name?: string;
    confirmed: boolean;
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
        const data = await agentRequest('/health');
        return {
            available: data.status === 'ok',
            url: baseUrl,
            message: data.status === 'ok' ? 'Agent 服务已连接 · 模型可用性以实际回复为准' : 'MiniAdsWall Agent 状态异常',
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

function isPotentiallyMutating(message: string): boolean {
    if (/^(确认|确认执行|我确认|继续执行|取消|取消操作|confirm|proceed|cancel)$/i.test(message.trim())) {
        return true;
    }
    const advisory = /(哪些|建议|怎么|如何|策略|模拟|应该|分析)/i;
    if (advisory.test(message)) {
        return false;
    }
    return /(删除|移除|下线|停用).{0,8}(广告|计划|素材|视频|图片)|(修改|调整|设置|提高|降低|增加|减少).{0,10}(预算|出价)|(预算|出价).{0,10}(修改|调整|设置|提高|降低|增加|减少)|delete\s+(ad|campaign|asset|creative|video|image)|(change|raise|lower|increase|decrease).{0,10}(budget|bid)/i.test(message);
}

function localFallback(input: AssistantChatInput, reason?: string): AssistantChatOutput {
    if (isPotentiallyMutating(input.message) || input.confirmationId) {
        return {
            convId: input.convId || null,
            response: '安全决策服务当前不可用，无法校验权限或恢复确认状态。本次请求已拒绝，没有执行任何变更。',
            intent: 'other',
            agentType: 'local',
            decision: 'reject',
            decisionReason: 'safety_service_unavailable',
            missingFields: [],
            riskLevel: 'high',
            actionName: undefined,
            confirmed: false,
            escalated: false,
            knowledgeUsed: false,
            source: 'local'
        };
    }

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
        '这些判断仅针对当前返回的广告样本；全量统计尚未提供。'
    ];

    return {
        convId: input.convId || null,
        response: `${suggestions.join('\n')}\n\n注：AdsAgent 当前不可用，已使用 MiniAddwall 本地诊断。${reason ? ` (${reason})` : ''}`,
        intent: 'local_analysis',
        agentType: 'local',
        decision: 'execute',
        decisionReason: 'safe_local_analysis_fallback',
        missingFields: [],
        riskLevel: 'low',
        actionName: undefined,
        confirmed: false,
        escalated: false,
        knowledgeUsed: false,
        source: 'local'
    };
}

async function chat(input: AssistantChatInput, requestId?: string): Promise<AssistantChatOutput> {
    try {
        const data: AdsAgentChatResponse = await agentRequest('/chat', {
            message: input.message,
            user_id: input.userId || 'anonymous',
            conv_id: input.convId || undefined,
            ads: input.ads || [],
            ad_context: ['true','1'].includes(process.env.AD_CONTEXT_V1_ENABLED||'') ? input.ad_context : undefined,
            confirmation_id: input.confirmationId
        }, requestId);
        return {
            convId: data.conv_id,
            response: data.response,
            intent: data.intent,
            agentType: data.agent_type,
            decision: data.decision,
            decisionReason: data.decision_reason,
            missingFields: data.missing_fields || [],
            confirmationId: data.confirmation_id,
            riskLevel: data.risk_level,
            actionName: data.action_name,
            confirmed: data.confirmed,
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
