export interface Ad {
    images?: string[];
    material_id?: string;
    id: string;
    title: string;
    publisher: string;
    content: string;
    url: string;
    price: number;
    clicks: number;
    version: number;
    videos: string[];
    video_count?: number;
}

export interface AdInput {
    material_id?: string;
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

export interface AdModalState {
    open: boolean;
    mode: 'create' | 'edit' | 'copy';
    data: Ad | null;
}

export interface DeleteModalState {
    open: boolean;
    id: string | null;
}

export interface VideoModalState {
    open: boolean;
    url: string;
    adId: string | null;
    landingUrl: string;
}

export interface ToastState {
    show: boolean;
    msg: string;
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
    adId?: string;
    materialId?: string;
    range?: '7'|'3';
    settings?: {goal:'clicks'|'orders'|'leads';dailyBudget:number;days:number;maxCpc:number;unitProfit?:number};
}

export interface DeliveryContext {
    status:'launched'|'new'; billing:'CPC'; currentBid:number|null; window:string[];
    performance: {impressions:number;clicks:number;orders:number;spend_minor:number;material_changed:boolean;average_cpc_minor:number|null}|null;
    material:{video_count:number;image_count:number};
    defaults:{goal:'clicks'|'orders'|'leads';dailyBudget:number|null;days:number;maxCpc:number|null;unitProfit:number|null};
}

export interface AdStrategyOutput {
    targetUsers: string[];
    bidSuggestion: number | null;
    recommendedObjective?: string;
    interests: string[];
    reason: string;
    biddingAdvice?: string;
    missingInformation?: string[];
    knowledgeUsed?: boolean;
    knowledgeSources?: { id: string; title: string; content: string; source_url?: string }[];
    bidBasis?: {status:'launched'|'new';current_bid:number|null;max_bid:number;total_budget:number};
}

export interface AIErrorResponse {
    error: true;
    message: string;
}

export interface AssistantChatInput {
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

export interface AssistantStatus {
    available: boolean;
    url: string;
    message: string;
    agents?: unknown;
}

export interface AssistantMessage {
    role: 'user' | 'assistant';
    content: string;
    source?: 'adsAgent' | 'local';
    decision?: 'execute' | 'clarify' | 'reject' | 'confirm';
}

export interface ChunkCheckResponse {
    exists: boolean;
    url?: string;
    uploadedChunks?: number[];
}

export interface ChunkUploadResponse {
    success: boolean;
    chunkIndex: number;
}

export interface ChunkMergeResponse {
    success: boolean;
    filename: string;
    url: string;
}

export interface UploadProgress {
    phase: 'hashing' | 'checking' | 'uploading' | 'merging' | 'done' | 'error';
    percent: number;
    message: string;
}
