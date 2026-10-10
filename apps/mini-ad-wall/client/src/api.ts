import axios from 'axios';
import { 
    Ad, 
    AdInput, 
    FormFieldConfig,
    AdCreativeInput,
    AdCreativeOutput,
    AdStrategyInput,
    AdStrategyOutput,
    AIErrorResponse,
    AssistantChatInput,
    AssistantChatOutput,
    AssistantStatus,
    ChunkCheckResponse,
    ChunkUploadResponse,
    ChunkMergeResponse
} from './types';

const api = axios.create({
    baseURL: '/api'
});

// Minimal single-operator authentication; credential stays in memory, never in the bundle.
let operatorToken = '';
api.interceptors.request.use(config => {
    const url = config.url || '';
    const method = (config.method || 'get').toLowerCase();
    const publicRequest = (method === 'get' && ['/ads', '/form-config'].includes(url)) || /^\/ads\/[^/]+\/click$/.test(url);
    if (!publicRequest) {
        if (!operatorToken) operatorToken = window.prompt('请输入运营凭据')?.trim() || '';
        if (!operatorToken) throw new Error('未提供运营凭据');
        config.headers.Authorization = `Bearer ${operatorToken}`;
    }
    config.headers['X-Request-ID'] = crypto.randomUUID();
    config.timeout = url === '/ai/assistant/chat' ? 35000 : url.startsWith('/ai/') ? 60000 : 15000;
    return config;
});
api.interceptors.response.use(response => response, error => {
    if (error.response?.status === 401) operatorToken = '';
    if (typeof error.response?.data?.error === 'string') error.message = error.response.data.error;
    return Promise.reject(error);
});

async function mutateAd<T>(method: 'post' | 'put' | 'delete', url: string, data?: AdInput): Promise<T> {
    // Keep the same operation ID across retries/reloads; unresolved operations are never assigned a new ID.
    const bytes = new TextEncoder().encode(JSON.stringify({ method, url, data }));
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    const key = 'ad-operation:' + Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
    let id = sessionStorage.getItem(key);
    const send = () => api.request<T>({ method, url, data, headers: { 'Idempotency-Key': id! } });
    const lookup = () => api.get(`/operations/${id}`);
    if (id) {
        try {
            const result = (await lookup()).data;
            sessionStorage.removeItem(key);
            if (result.status >= 400) throw new Error(result.body?.error || '操作失败');
            return result.body as T;
        } catch (error: any) {
            if (error.response?.status !== 404) throw error;
        }
    } else {
        id = crypto.randomUUID();
        sessionStorage.setItem(key, id);
    }
    try {
        const response = await send();
        sessionStorage.removeItem(key);
        return response.data;
    } catch (error: any) {
        if (error.response && error.response.status < 500) {
            sessionStorage.removeItem(key);
            throw error;
        }
        try {
            const result = (await lookup()).data;
            sessionStorage.removeItem(key);
            if (result.status >= 400) throw new Error(result.body?.error || '操作失败');
            return result.body as T;
        } catch (lookupError: any) {
            // Next user retry queries first and, only on 404, resends this same ID.
            if (!sessionStorage.getItem(key)) throw lookupError;
            throw new Error(`操作结果待确认，请恢复连接后重试。操作编号：${id}`);
        }
    }
}

export const getAdsPage = (cursor?: string): Promise<{items: Ad[]; next_cursor: string | null}> => api.get('/ads/page', {params: {cursor}}).then(res => res.data);
export const getAds = (): Promise<Ad[]> => getAdsPage().then(res => res.items);
export const getAd = (id: string): Promise<Ad> => api.get(`/ads/${encodeURIComponent(id)}`).then(res => res.data);
export const searchAds = (conditions: unknown[]) => api.post('/ads/search', {conditions,limit:50}).then(res=>res.data);

export const createAd = (data: AdInput): Promise<Ad> => mutateAd<Ad>('post', '/ads', data);

export const updateAd = (id: string, data: AdInput): Promise<Ad> => mutateAd<Ad>('put', `/ads/${id}`, data);

export const deleteAd = (id: string): Promise<void> => mutateAd<void>('delete', `/ads/${id}`);

export const clickAd = (id: string): Promise<{ clicks: number }> => api.post(`/ads/${id}/click`).then(res => res.data);

export const uploadVideo = (file: File): Promise<{ filename: string; url: string }> => {
    const formData = new FormData();
    formData.append('video', file);
    return api.post('/upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
    }).then(res => res.data);
};

export const getFormConfig = (): Promise<FormFieldConfig[]> => api.get('/form-config').then(res => res.data);

export const generateAdCreative = (
    data: AdCreativeInput
): Promise<AdCreativeOutput | AIErrorResponse> => 
    api.post('/ai/creative', data).then(res => res.data);

export const generateAdStrategy = (
    data: AdStrategyInput
): Promise<AdStrategyOutput | AIErrorResponse> => 
    api.post('/ai/strategy', data).then(res => res.data);

export const chatWithAssistant = (
    data: AssistantChatInput
): Promise<AssistantChatOutput> =>
    api.post('/ai/assistant/chat', data).then(res => res.data);

export const getAssistantStatus = (): Promise<AssistantStatus> =>
    api.get('/ai/assistant/status').then(res => res.data);

export const checkFileUpload = (
    fileMd5: string,
    fileName: string,
    totalChunks: number,
    fileSize: number
): Promise<ChunkCheckResponse> =>
    api.post('/upload/check', { fileMd5, fileName, totalChunks, fileSize }).then(res => res.data);

export const uploadChunk = (
    chunk: Blob,
    fileMd5: string,
    chunkIndex: number,
    chunkMd5: string
): Promise<ChunkUploadResponse> => {
    const formData = new FormData();
    formData.append('chunk', chunk);
    formData.append('fileMd5', fileMd5);
    formData.append('chunkIndex', String(chunkIndex));
    formData.append('chunkMd5', chunkMd5);
    return api.post('/upload/chunk', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
    }).then(res => res.data);
};

export const mergeChunks = (
    fileMd5: string,
    fileName: string,
    totalChunks: number,
    fileSize: number
): Promise<ChunkMergeResponse> =>
    api.post('/upload/merge', { fileMd5, fileName, totalChunks, fileSize }).then(res => res.data);

export interface HostedRun {
    id: string; session_id: string; status: string; error_code?: string; approval_id?: string;
}
export interface HostedSession { id: string; title: string; active_run_id?: string; }
export interface HostedRecord { id: string; seq: number; record_type: string; role?: 'user' | 'assistant'; content_preview?: string; }
export const hostedIdentity = () => api.get<{ user_id: string; workspace_id: string }>('/agent/identity').then(r => r.data);
export const hostedSessions = () => api.get<{ items: HostedSession[] }>('/agent/sessions', { params: { limit: 100 } }).then(r => r.data.items);
export const hostedNewSession = (workspace_id: string, title: string) => api.post<HostedSession>('/agent/sessions', { workspace_id, title }).then(r => r.data);
export const hostedHistory = (id: string, cursor?: string) => api.get<{ items: HostedRecord[]; next_cursor?: string }>(`/agent/sessions/${id}/history`, { params: { limit: 50, cursor } }).then(r => r.data);
export const hostedCreateRun = (id: string, message: string, key: string) => api.post<HostedRun>(`/agent/sessions/${id}/runs`, { message }, { headers: { 'Idempotency-Key': key } }).then(r => r.data);
export const hostedRun = (id: string) => api.get<HostedRun>(`/agent/runs/${id}`).then(r => r.data);
export const hostedCancel = (id: string) => api.post<HostedRun>(`/agent/runs/${id}/cancel`).then(r => r.data);
export const hostedResume = (id: string, key: string) => api.post<HostedRun>(`/agent/runs/${id}/resume`, {}, { headers: { 'Idempotency-Key': key } }).then(r => r.data);
export const hostedApprove = (id: string, allow: boolean) => api.post<HostedRun>(`/agent/approvals/${id}/decision`, { allow }).then(r => r.data);

export async function hostedEvents(id: string, after: number, signal: AbortSignal,
    onEvent: (seq: number, type: string, data: any) => void): Promise<void> {
    if (!operatorToken) throw new Error('未提供运营凭据');
    const response = await fetch(`/api/agent/runs/${id}/events`, {
        headers: { Authorization: `Bearer ${operatorToken}`, 'Last-Event-ID': String(after) }, signal
    });
    if (!response.ok || !response.body) throw new Error(`events:${response.status}`);
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
    try {
        while (true) {
            const { value, done } = await reader.read(); if (done) break;
            buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
            if (buffer.length > 262144) throw new Error('events:buffer_limit');
            let boundary: number;
            while ((boundary = buffer.indexOf('\n\n')) >= 0) {
                const block = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
                const lines = block.split('\n');
                const seq = Number(lines.find(l => l.startsWith('id:'))?.slice(3));
                const type = lines.find(l => l.startsWith('event:'))?.slice(6).trim();
                const data = lines.filter(l => l.startsWith('data:')).map(l => l.slice(5).trimStart()).join('\n');
                if (seq > after && type && data) { onEvent(seq, type, JSON.parse(data)); after = seq; }
            }
        }
    } finally { await reader.cancel().catch(() => undefined); reader.releaseLock(); }
}
