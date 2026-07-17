import llmService from './llm.service';
import {
    AdCreativeInput,
    AdCreativeOutput,
    AdStrategyInput,
    AdStrategyOutput,
    AIErrorResponse
} from '../types';

async function generateAdCreative(
    input: AdCreativeInput
): Promise<AdCreativeOutput | AIErrorResponse> {
    const { adDescription, industry, tone } = input;

    const prompt = `生成广告创意。产品：${adDescription}，行业：${industry}，风格：${tone === 'strong' ? '强势' : '温和'}

输出JSON：{"titles":["标题x3"],"texts":["文案x3,50字内"],"scripts":["脚本x3,30字内"],"keywords":["关键词x5"]}`;

    try {
        const result = await llmService.generateJson<AdCreativeOutput>(prompt);

        if (!result.titles || !result.texts || !result.scripts || !result.keywords) {
            throw new Error('AI 返回结果格式不完整');
        }

        return {
            titles: result.titles.slice(0, 3),
            texts: result.texts.slice(0, 3),
            scripts: result.scripts.slice(0, 3),
            keywords: result.keywords.slice(0, 5)
        };
    } catch (error) {
        console.error('AI 广告创意生成失败:', error);
        return {
            error: true,
            message: 'AI 调用失败'
        };
    }
}

async function generateAdStrategy(
    input: AdStrategyInput
): Promise<AdStrategyOutput | AIErrorResponse> {
    const { adDescription, industry } = input;

    const prompt = `生成投放策略。产品：${adDescription}，行业：${industry}

输出JSON：{"targetUsers":["用户特征x3"],"bidSuggestion":出价1-100,"interests":["兴趣x5"],"reason":"理由50字内"}`;

    try {
        const result = await llmService.generateJson<AdStrategyOutput>(prompt);

        if (
            !result.targetUsers ||
            result.bidSuggestion === undefined ||
            !result.interests ||
            !result.reason
        ) {
            throw new Error('AI 返回结果格式不完整');
        }

        let bidSuggestion = Number(result.bidSuggestion);
        if (isNaN(bidSuggestion) || bidSuggestion < 1) {
            bidSuggestion = 10;
        } else if (bidSuggestion > 100) {
            bidSuggestion = 100;
        }

        return {
            targetUsers: result.targetUsers.slice(0, 5),
            bidSuggestion: Math.round(bidSuggestion * 100) / 100,
            interests: result.interests.slice(0, 5),
            reason: result.reason
        };
    } catch (error) {
        console.error('AI 投放策略生成失败:', error);
        return {
            error: true,
            message: 'AI 调用失败'
        };
    }
}

export default {
    generateAdCreative,
    generateAdStrategy
};
