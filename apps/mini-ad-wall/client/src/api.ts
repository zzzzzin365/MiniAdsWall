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

export const getAds = (): Promise<Ad[]> => api.get('/ads').then(res => res.data);

export const createAd = (data: AdInput): Promise<Ad> => api.post('/ads', data).then(res => res.data);

export const updateAd = (id: string, data: AdInput): Promise<Ad> => api.put(`/ads/${id}`, data).then(res => res.data);

export const deleteAd = (id: string): Promise<void> => api.delete(`/ads/${id}`).then(() => undefined);

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
