export interface Ad {
    attributes?: Record<string, string[]>;
    eligibility?: {enabled?: boolean; starts_at?: string | null; ends_at?: string | null};
    recall_receipt?: import('../recall/contracts').Receipt;
    id: string;
    title: string;
    publisher: string;
    content: string;
    url: string;
    price: number;
    clicks: number;
    version: number;
    videos: string[];
}

export interface AdInput {
    attributes?: Record<string, string[]>;
    eligibility?: {enabled?: boolean; starts_at?: string | null; ends_at?: string | null};
    title: string;
    publisher: string;
    content: string;
    url: string;
    price: number | string;
    version?: number;
    videos?: string[];
}

export interface FormFieldValidator {
    maxLength?: number;
    minLength?: number;
    pattern?: string;
    min?: number;
    max?: number;
    maxCount?: number;
    message?: string;
}

export interface FormFieldConfig {
    field: string;
    name: string;
    component: 'Input' | 'Number' | 'Textarea' | 'VideoUpload';
    placeholder?: string;
    required: boolean;
    validator?: FormFieldValidator;
}

export interface ValidationError {
    field: string;
    message: string;
}

export interface ValidationResult {
    valid: boolean;
    errors: ValidationError[];
}

export interface ServiceResult<T = any> {
    recall_receipt?: import('../recall/contracts').Receipt;
    success: boolean;
    data?: T;
    error?: string;
    clicks?: number;
}

export interface UploadResult {
    success: boolean;
    data?: {
        filename: string;
        url: string;
    };
    error?: string;
}

export interface Config {
    MARKETING: ReturnType<typeof import('../config/marketing').loadMarketing>;
    RELIABILITY: ReturnType<typeof import('../config/reliability').loadReliability>;
    PORT: number | string;
    HOST: string;
    DATA_FILE: string;
    UPLOAD_DIR: string;
    AD_SCORE_FACTOR: number;
    DEFAULT_ADS: Ad[];
    ADS_AGENT_API_URL: string;
}

export interface AdCreativeInput {
    adDescription: string;
    industry: string;
    tone: string;
}

export interface AdCreativeOutput {
    titles: string[];
    texts: string[];
    scripts: string[];
    keywords: string[];
}

export interface AdStrategyInput {
    adDescription: string;
    industry: string;
}

export interface AdStrategyOutput {
    targetUsers: string[];
    bidSuggestion: number;
    interests: string[];
    reason: string;
}

export interface AIErrorResponse {
    error: true;
    message: string;
}

export interface AssistantChatInput {
    ad_context?: any;
    conditions?: any[];
    message: string;
    userId?: string;
    convId?: string;
    ads?: Ad[];
    confirmationId?: string;
}

export interface AssistantChatOutput {
    convId: string | null;
    response: string;
    intent: string;
    agentType: string;
    decision: 'execute' | 'clarify' | 'reject' | 'confirm';
    decisionReason: string;
    missingFields: string[];
    confirmationId?: string;
    riskLevel: 'low' | 'medium' | 'high';
    actionName?: string;
    confirmed: boolean;
    escalated: boolean;
    knowledgeUsed: boolean;
    toolsUsed?: string[];
    source: 'adsAgent' | 'local';
}

export interface ChunkCheckRequest {
    fileMd5: string;
    fileName: string;
    totalChunks: number;
    fileSize: number;
}

export interface ChunkUploadMeta {
    fileMd5: string;
    chunkIndex: number;
    chunkMd5: string;
}

export interface ChunkMergeRequest {
    fileMd5: string;
    fileName: string;
    totalChunks: number;
    fileSize: number;
}
