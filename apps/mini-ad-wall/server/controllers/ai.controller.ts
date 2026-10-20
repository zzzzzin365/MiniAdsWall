import { RouterContext } from 'koa-router';
import { adContext } from '../services/recall/search';
import config from '../config';
import aiService from '../services/ai.service';
import adsAgentService from '../services/adsAgent.service';
import { loadDeliveryContext, validateSettings } from '../services/delivery-context';
import { AdCreativeInput, AdStrategyInput, AssistantChatInput } from '../types';

async function generateCreative(ctx: RouterContext): Promise<void> {
    const body = ctx.request.body as AdCreativeInput;

    if (!body.adDescription || typeof body.adDescription !== 'string') {
        ctx.status = 400;
        ctx.body = { error: true, message: '缺少必填参数: adDescription' };
        return;
    }

    if (!body.industry || typeof body.industry !== 'string') {
        ctx.status = 400;
        ctx.body = { error: true, message: '缺少必填参数: industry' };
        return;
    }

    const input: AdCreativeInput = {
        adDescription: body.adDescription.trim(),
        industry: body.industry.trim(),
        tone: body.tone || 'neutral'
    };

    const result = await aiService.generateAdCreative(input, ctx.state.requestId);

    if ('error' in result && result.error) {
        ctx.status = 500;
        ctx.body = result;
        return;
    }

    ctx.status = 200;
    ctx.body = result;
}

async function generateStrategy(ctx: RouterContext): Promise<void> {
    if (!ctx.state.principal) ctx.throw(401, '请先设置运营凭证');
    const body = ctx.request.body as AdStrategyInput;

    if (!body.adDescription || typeof body.adDescription !== 'string') {
        ctx.status = 400;
        ctx.body = { error: true, message: '缺少必填参数: adDescription' };
        return;
    }

    if (!body.industry || typeof body.industry !== 'string') {
        ctx.status = 400;
        ctx.body = { error: true, message: '缺少必填参数: industry' };
        return;
    }

    const delivery = await loadDeliveryContext(body.adId,body.range,body.materialId);
    const settings = validateSettings(body.settings || delivery.defaults);
    const input: AdStrategyInput = {
        adDescription: body.adDescription.trim(),
        industry: body.industry.trim(), settings, delivery
    };

    const result = await aiService.generateAdStrategy(input, ctx.state.requestId);

    if ('error' in result && result.error) {
        ctx.status = 500;
        ctx.body = result;
        return;
    }

    ctx.status = 200;
    ctx.body = result;
}

async function strategyContext(ctx: RouterContext): Promise<void> {
    if (!ctx.state.principal) ctx.throw(401, '请先设置运营凭证');
    ctx.body = await loadDeliveryContext(ctx.query.ad_id,ctx.query.range);
}


async function chatAssistant(ctx: RouterContext): Promise<void> {
    const body = ctx.request.body as AssistantChatInput;

    if (!body.message || typeof body.message !== 'string') {
        ctx.status = 400;
        ctx.body = { error: true, message: '缺少必填参数: message' };
        return;
    }

    if (!config.RELIABILITY.features.AD_CONTEXT_V1_ENABLED && body.conditions?.length) ctx.throw(503, 'feature_disabled');
    const context = await adContext(config.RELIABILITY.features.AD_CONTEXT_V1_ENABLED ? body.conditions : [], ctx.state.principal);
    const result = await adsAgentService.chat({
        message: body.message.trim(),
        userId: ctx.state.principal || 'anonymous',
        convId: body.convId,
        ads: context.items,
        ad_context: config.RELIABILITY.features.AD_CONTEXT_V1_ENABLED ? context : undefined,
        confirmationId: body.confirmationId
    }, ctx.get('Idempotency-Key') || ctx.state.requestId);

    ctx.status = result.runStatus && !['succeeded','failed','cancelled','timed_out','interrupted','waiting_approval'].includes(result.runStatus) ? 202 : 200;
    ctx.body = result;
}

async function assistantStatus(ctx: RouterContext): Promise<void> {
    const result = await adsAgentService.getStatus();
    ctx.status = 200;
    ctx.body = result;
}

export default {
    generateCreative,
    generateStrategy,
    strategyContext,
    chatAssistant,
    assistantStatus
};
