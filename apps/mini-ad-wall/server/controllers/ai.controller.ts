import { RouterContext } from 'koa-router';
import aiService from '../services/ai.service';
import adsAgentService from '../services/adsAgent.service';
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

    const result = await aiService.generateAdCreative(input);

    if ('error' in result && result.error) {
        ctx.status = 500;
        ctx.body = result;
        return;
    }

    ctx.status = 200;
    ctx.body = result;
}

async function generateStrategy(ctx: RouterContext): Promise<void> {
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

    const input: AdStrategyInput = {
        adDescription: body.adDescription.trim(),
        industry: body.industry.trim()
    };

    const result = await aiService.generateAdStrategy(input);

    if ('error' in result && result.error) {
        ctx.status = 500;
        ctx.body = result;
        return;
    }

    ctx.status = 200;
    ctx.body = result;
}


async function chatAssistant(ctx: RouterContext): Promise<void> {
    const body = ctx.request.body as AssistantChatInput;

    if (!body.message || typeof body.message !== 'string') {
        ctx.status = 400;
        ctx.body = { error: true, message: '缺少必填参数: message' };
        return;
    }

    const result = await adsAgentService.chat({
        message: body.message.trim(),
        userId: body.userId,
        convId: body.convId,
        ads: Array.isArray(body.ads) ? body.ads : []
    });

    ctx.status = 200;
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
    chatAssistant,
    assistantStatus
};
