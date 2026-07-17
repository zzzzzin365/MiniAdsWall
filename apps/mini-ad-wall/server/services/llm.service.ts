import config from '../config';

const MODEL = config.OPENROUTER_MODEL;

export async function generateContent(prompt: string): Promise<string> {
    const apiKey = config.OPENROUTER_API_KEY;
    
    if (!apiKey || apiKey === 'sk-or-你的key') {
        throw new Error('OPENROUTER_API_KEY 未配置');
    }

    const url = config.OPENROUTER_API_URL;
    const payload = {
        model: MODEL,
        messages: [
            {
                role: 'user',
                content: prompt,
            },
        ],
        temperature: 0.2,
        max_tokens: 500,
        top_p: 1,
        frequency_penalty: 0,
        presence_penalty: 0,
    };

    const res = await fetch(url, {
        method: 'POST',
        headers: {
            'Authorization': `Bearer ${apiKey}`,
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
    });

    const json = await res.json() as any;
    
    if (!res.ok) {
        throw new Error(
            `OpenRouter API 请求失败: ${res.status} - ${JSON.stringify(json)}`
        );
    }

    const content = json?.choices?.[0]?.message?.content;
    if (!content) {
        throw new Error('LLM 响应里没有内容：' + JSON.stringify(json));
    }

    return content;
}

export async function generateJson<T = any>(prompt: string): Promise<T> {
    const fullPrompt = `
你现在必须返回 JSON，不能输出解释，不能输出多余文字。
直接给我一个合法 JSON 对象。
要求：
${prompt}
    `;
    
    const raw = await generateContent(fullPrompt);
    console.log('[LLM Response]', raw.substring(0, 500));
    
    try {
        return JSON.parse(raw) as T;
    } catch {
    }

    const jsonBlockMatch = raw.match(/```(?:json)?\s*([\s\S]*?)\s*```/);
    if (jsonBlockMatch) {
        try {
            return JSON.parse(jsonBlockMatch[1].trim()) as T;
        } catch {
        }
    }

    const jsonMatch = raw.match(/(\{[\s\S]*\})/);
    if (jsonMatch) {
        try {
            return JSON.parse(jsonMatch[1]) as T;
        } catch {
        }
    }

    throw new Error('无法从 LLM 响应中解析 JSON：' + raw);
}

export default {
    generateContent,
    generateJson
};
