import { agentRequest } from './agentHttp';
import { AdCreativeInput, AdStrategyInput, AdCreativeOutput, AdStrategyOutput, AIErrorResponse } from '../types';

async function generate<T>(path: string, input: unknown, requestId?: string): Promise<T | AIErrorResponse> {
    try {
        return await agentRequest(path, input, requestId) as T;
    } catch {
        return { error: true, message: 'AI 服务不可用或生成超时，请稍后重试' };
    }
}

export default {
    generateAdCreative: (input: AdCreativeInput, requestId?: string) => generate<AdCreativeOutput>('/generate/creative', input, requestId),
    generateAdStrategy: (input: AdStrategyInput, requestId?: string) => generate<AdStrategyOutput>('/generate/strategy', input, requestId)
};
