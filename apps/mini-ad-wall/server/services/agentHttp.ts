import { randomUUID } from 'crypto';
import config from '../config';

/** Read/generation calls only. Never automatically retry a mutation. */
export async function agentRequest(path: string, body?: unknown, requestId: string = randomUUID()): Promise<any> {
    const response = await fetch(`${config.ADS_AGENT_API_URL.replace(/\/$/, '')}${path}`, {
        method: body === undefined ? 'GET' : 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-Request-ID': requestId,
            'Authorization': `Bearer ${process.env.AGENT_SERVICE_TOKEN || ''}`
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(path === '/health' ? 3000 : path === '/chat' ? 32000 : 55000)
    });
    if (!response.ok) throw new Error(`Agent HTTP ${response.status}`);
    return response.json();
}
