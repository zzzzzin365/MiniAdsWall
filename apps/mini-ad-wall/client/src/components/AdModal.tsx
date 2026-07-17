import React, { useState, useEffect, ChangeEvent } from 'react';
import { getFormConfig, generateAdCreative, generateAdStrategy } from '../api';
import { chunkedUploadFile } from '../utils/chunkedUpload';
import { 
    Ad, 
    AdInput, 
    FormFieldConfig, 
    AdCreativeOutput, 
    AdStrategyOutput,
    UploadProgress
} from '../types';

interface FormFieldProps {
    config: FormFieldConfig;
    value: string | number | string[];
    onChange: (field: string, value: string) => void;
    error?: string;
}

const FormField: React.FC<FormFieldProps> = ({ config, value, onChange, error }) => {
    const { field, name, component, placeholder, required } = config;

    const renderComponent = () => {
        const strValue = value as string || '';
        switch (component) {
            case 'Input':
                return (
                    <input
                        type="text"
                        className={`form-input ${error ? 'form-input-error' : ''}`}
                        id={field}
                        value={strValue}
                        onChange={(e) => onChange(field, e.target.value)}
                        placeholder={placeholder}
                    />
                );
            case 'Number':
                return (
                    <input
                        type="number"
                        className={`form-input ${error ? 'form-input-error' : ''}`}
                        id={field}
                        value={strValue}
                        onChange={(e) => onChange(field, e.target.value)}
                        placeholder={placeholder}
                        step="0.01"
                    />
                );
            case 'Textarea':
                return (
                    <textarea
                        className={`form-textarea ${error ? 'form-input-error' : ''}`}
                        id={field}
                        value={strValue}
                        onChange={(e) => onChange(field, e.target.value)}
                        placeholder={placeholder}
                    />
                );
            default:
                return (
                    <input
                        type="text"
                        className="form-input"
                        id={field}
                        value={strValue}
                        onChange={(e) => onChange(field, e.target.value)}
                        placeholder={placeholder}
                    />
                );
        }
    };

    if (component === 'VideoUpload') {
        return null;
    }

    return (
        <div className="form-group">
            <label className="form-label">
                {name}
                {required && <span>*</span>}
            </label>
            {renderComponent()}
            {error && <div className="form-error">{error}</div>}
        </div>
    );
};

interface VideoUploadFieldProps {
    config: FormFieldConfig;
    videos: string[];
    onAdd: (e: ChangeEvent<HTMLInputElement>) => void;
    onRemove: (index: number) => void;
    uploading: boolean;
    uploadProgress: UploadProgress | null;
}

const PHASE_LABELS: Record<string, string> = {
    hashing: '计算指纹',
    checking: '检查状态',
    uploading: '分片上传',
    merging: '合并文件',
    done: '完成',
    error: '失败'
};

const VideoUploadField: React.FC<VideoUploadFieldProps> = ({ config, videos, onAdd, onRemove, uploading, uploadProgress }) => {
    const { name, validator } = config;
    const maxCount = validator?.maxCount || 5;

    return (
        <div className="form-group">
            <label className="form-label">{name}</label>
            <div className="video-upload-area">
                {videos.map((v, i) => (
                    <div key={i} className="video-preview-item">
                        <video src={v} className="video-thumbnail" />
                        <button
                            onClick={() => onRemove(i)}
                            className="video-remove-btn"
                        >×</button>
                    </div>
                ))}
                {videos.length < maxCount && (
                    <label className="video-upload-btn">
                        {uploading ? (
                            <div className="chunk-upload-status">
                                <span className="upload-loading">
                                    {uploadProgress ? PHASE_LABELS[uploadProgress.phase] : '准备中...'}
                                </span>
                                {uploadProgress && uploadProgress.phase !== 'error' && (
                                    <div className="chunk-progress-bar">
                                        <div
                                            className="chunk-progress-fill"
                                            style={{ width: `${uploadProgress.percent}%` }}
                                        />
                                    </div>
                                )}
                                {uploadProgress && (
                                    <span className="chunk-progress-text">{uploadProgress.message}</span>
                                )}
                            </div>
                        ) : (
                            <>
                                <span className="upload-icon">+</span>
                                <span className="upload-text">上传视频</span>
                            </>
                        )}
                        <input
                            type="file"
                            accept="video/*"
                            onChange={onAdd}
                            style={{ display: 'none' }}
                            disabled={uploading}
                        />
                    </label>
                )}
            </div>
            <div className="form-hint">最多上传 {maxCount} 个视频，支持秒传与断点续传</div>
            <div className="form-hint">单个视频最大不超过50MB，支持 .mp4、.mov 格式</div>
        </div>
    );
};

interface FormData {
    [key: string]: string | number | string[];
    videos: string[];
}

const validateFormData = (data: FormData, config: FormFieldConfig[]): Record<string, string> => {
    const errors: Record<string, string> = {};

    for (const field of config) {
        const value = data[field.field];
        const validator = field.validator;

        if (field.required) {
            if (value === undefined || value === null || value === '') {
                errors[field.field] = `${field.name}不能为空`;
                continue;
            }
        }

        if (value !== undefined && value !== null && value !== '' && validator) {
            if (validator.maxLength && String(value).length > validator.maxLength) {
                errors[field.field] = validator.message || `${field.name}超过最大长度`;
            }

            if (validator.pattern) {
                const regex = new RegExp(validator.pattern);
                if (!regex.test(String(value))) {
                    errors[field.field] = validator.message || `${field.name}格式不正确`;
                }
            }

            if (field.component === 'Number') {
                const numValue = parseFloat(String(value));
                if (isNaN(numValue)) {
                    errors[field.field] = `${field.name}必须是数字`;
                } else {
                    if (validator.min !== undefined && numValue < validator.min) {
                        errors[field.field] = validator.message || `${field.name}不能小于${validator.min}`;
                    }
                    if (validator.max !== undefined && numValue > validator.max) {
                        errors[field.field] = validator.message || `${field.name}不能大于${validator.max}`;
                    }
                }
            }

            if (Array.isArray(value) && validator.maxCount) {
                if (value.length > validator.maxCount) {
                    errors[field.field] = validator.message || `${field.name}数量超过限制`;
                }
            }
        }
    }

    return errors;
};

interface AdModalProps {
    isOpen: boolean;
    mode: 'create' | 'edit' | 'copy';
    initialData: Ad | null;
    onClose: () => void;
    onSubmit: (data: AdInput) => void;
}

const INDUSTRY_OPTIONS = [
    '食品饮料',
    '美妆护肤',
    '服装配饰',
    '数码3C',
    '家居生活',
    '教育培训',
    '金融理财',
    '游戏娱乐',
    '医疗健康',
    '汽车出行',
    '旅游酒店',
    '本地生活',
    '电商零售',
    '其他'
];

const AdModal: React.FC<AdModalProps> = ({ isOpen, mode, initialData, onClose, onSubmit }) => {
    const [formConfig, setFormConfig] = useState<FormFieldConfig[]>([]);
    const [formData, setFormData] = useState<FormData>({ videos: [] });
    const [errors, setErrors] = useState<Record<string, string>>({});
    const [uploading, setUploading] = useState(false);
    const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
    const [loading, setLoading] = useState(false);
    
    const [aiPanelOpen, setAiPanelOpen] = useState(false);
    const [aiLoading, setAiLoading] = useState(false);
    const [aiIndustry, setAiIndustry] = useState('食品饮料');
    const [aiCreativeResult, setAiCreativeResult] = useState<AdCreativeOutput | null>(null);
    const [aiStrategyResult, setAiStrategyResult] = useState<AdStrategyOutput | null>(null);
    const [aiError, setAiError] = useState<string | null>(null);
    
    const [appliedItem, setAppliedItem] = useState<string | null>(null);

    useEffect(() => {
        if (isOpen) {
            setLoading(true);
            getFormConfig()
                .then(config => {
                    setFormConfig(config);
                    setLoading(false);
                })
                .catch(err => {
                    console.error('Failed to load form config:', err);
                    setLoading(false);
                });
        }
    }, [isOpen]);

    useEffect(() => {
        if (isOpen && formConfig.length > 0) {
            if (mode === 'create') {
                const emptyData: FormData = { videos: [] };
                formConfig.forEach(field => {
                    emptyData[field.field] = field.component === 'VideoUpload' ? [] : '';
                });
                setFormData(emptyData);
            } else if (initialData) {
                setFormData({
                    ...initialData,
                    videos: initialData.videos || []
                } as FormData);
            }
            setErrors({});
        }
    }, [isOpen, mode, initialData, formConfig]);

    const handleFieldChange = (field: string, value: string) => {
        setFormData(prev => ({ ...prev, [field]: value }));
        if (errors[field]) {
            setErrors(prev => {
                const newErrors = { ...prev };
                delete newErrors[field];
                return newErrors;
            });
        }
    };

    const VIDEO_UPLOAD_CONFIG = {
        maxSize: 50 * 1024 * 1024, 
        maxSizeMB: 50,
        allowedTypes: ['video/mp4', 'video/quicktime', 'video/webm', 'video/x-msvideo'],
        allowedExtensions: ['.mp4', '.mov'],
    };

    const validateVideoFile = (file: File): { valid: boolean; error?: string } => {
        const fileExtension = '.' + file.name.split('.').pop()?.toLowerCase();
        const isValidType = VIDEO_UPLOAD_CONFIG.allowedTypes.includes(file.type) ||
                           VIDEO_UPLOAD_CONFIG.allowedExtensions.includes(fileExtension);
        
        if (!isValidType) {
            return {
                valid: false,
                error: `不支持的视频格式，仅支持 ${VIDEO_UPLOAD_CONFIG.allowedExtensions.join('、')} 格式`
            };
        }

        if (file.size > VIDEO_UPLOAD_CONFIG.maxSize) {
            return {
                valid: false,
                error: `视频文件过大，最大支持 ${VIDEO_UPLOAD_CONFIG.maxSizeMB}MB`
            };
        }

        return { valid: true };
    };

    const handleVideoUpload = async (e: ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file) return;

        const validation = validateVideoFile(file);
        if (!validation.valid) {
            alert(validation.error);
            e.target.value = '';
            return;
        }

        setUploading(true);
        setUploadProgress(null);
        try {
            const url = await chunkedUploadFile(file, (progress) => {
                setUploadProgress(progress);
            });
            setFormData(prev => ({
                ...prev,
                videos: [...(prev.videos || []), url]
            }));
        } catch (error) {
            console.error("Upload failed", error);
            setUploadProgress({ phase: 'error', percent: 0, message: '上传失败，请重试' });
            alert("视频上传失败");
        } finally {
            setUploading(false);
            e.target.value = '';
        }
    };

    const handleRemoveVideo = (index: number) => {
        setFormData(prev => ({
            ...prev,
            videos: prev.videos.filter((_, i) => i !== index)
        }));
    };

    const handleAiGenerate = async () => {
        const description = formData.content as string || formData.title as string;
        if (!description.trim()) {
            setAiError('请先填写广告标题或内容描述');
            return;
        }

        setAiLoading(true);
        setAiError(null);
        setAiCreativeResult(null);
        setAiStrategyResult(null);

        try {
            const [creativeRes, strategyRes] = await Promise.all([
                generateAdCreative({
                    adDescription: description,
                    industry: aiIndustry,
                    tone: 'strong'
                }),
                generateAdStrategy({
                    adDescription: description,
                    industry: aiIndustry
                })
            ]);

            if ('error' in creativeRes && creativeRes.error) {
                setAiError(creativeRes.message);
            } else {
                setAiCreativeResult(creativeRes as AdCreativeOutput);
            }

            if ('error' in strategyRes && strategyRes.error) {
                if (!aiError) setAiError(strategyRes.message);
            } else {
                setAiStrategyResult(strategyRes as AdStrategyOutput);
            }
        } catch {
            setAiError('AI 服务请求失败，请稍后重试');
        } finally {
            setAiLoading(false);
        }
    };

    const showApplyFeedback = (itemKey: string) => {
        setAppliedItem(itemKey);
        setTimeout(() => setAppliedItem(null), 1500);
    };

    const applyCreativeTitle = (title: string, index: number) => {
        setFormData(prev => ({ ...prev, title }));
        showApplyFeedback(`title-${index}`);
    };

    const applyCreativeText = (text: string, index: number) => {
        setFormData(prev => ({ ...prev, content: text }));
        showApplyFeedback(`text-${index}`);
    };

    const applyStrategyPrice = (price: number) => {
        setFormData(prev => ({ ...prev, price: String(price) }));
        showApplyFeedback('price');
    };

    const handleSubmit = () => {
        const validationErrors = validateFormData(formData, formConfig);

        if (Object.keys(validationErrors).length > 0) {
            setErrors(validationErrors);
            return;
        }

        const submitData: AdInput = {
            title: formData.title as string,
            publisher: formData.publisher as string,
            content: formData.content as string,
            url: formData.url as string,
            price: parseFloat(String(formData.price)),
            videos: formData.videos
        };

        onSubmit(submitData);
    };

    if (!isOpen) return null;

    const titleMap: Record<string, string> = {
        create: '新建广告',
        edit: '编辑广告',
        copy: '复制广告'
    };

    const videoConfig = formConfig.find(f => f.component === 'VideoUpload');

    return (
        <div className="modal-overlay active">
            <div className="modal-box">
                <div className="modal-header">{titleMap[mode]}</div>

                <div className="modal-body">
                    {loading ? (
                        <div className="form-loading">加载表单配置中...</div>
                    ) : (
                        <>
                            {formConfig.map(fieldConfig => (
                                <FormField
                                    key={fieldConfig.field}
                                    config={fieldConfig}
                                    value={formData[fieldConfig.field]}
                                    onChange={handleFieldChange}
                                    error={errors[fieldConfig.field]}
                                />
                            ))}

                            {videoConfig && (
                                <VideoUploadField
                                    config={videoConfig}
                                    videos={formData.videos || []}
                                    onAdd={handleVideoUpload}
                                    onRemove={handleRemoveVideo}
                                    uploading={uploading}
                                    uploadProgress={uploadProgress}
                                />
                            )}

                            <div className="ai-assist-section">
                                <div className="ai-assist-header" onClick={() => setAiPanelOpen(!aiPanelOpen)}>
                                    <div className="ai-assist-title">
                                        <span className="ai-icon">✨</span>
                                        AI 智能助手
                                    </div>
                                    <span className={`ai-toggle ${aiPanelOpen ? 'open' : ''}`}>
                                        {aiPanelOpen ? '收起' : '展开'}
                                    </span>
                                </div>
                                
                                {aiPanelOpen && (
                                    <div className="ai-assist-body">
                                        <div className="ai-input-row">
                                            <div className="ai-input-group">
                                                <label className="ai-label">行业分类</label>
                                                <select 
                                                    className="ai-select"
                                                    value={aiIndustry}
                                                    onChange={(e) => setAiIndustry(e.target.value)}
                                                >
                                                    {INDUSTRY_OPTIONS.map(opt => (
                                                        <option key={opt} value={opt}>{opt}</option>
                                                    ))}
                                                </select>
                                            </div>
                                            <button 
                                                className="btn btn-ai"
                                                onClick={handleAiGenerate}
                                                disabled={aiLoading}
                                            >
                                                {aiLoading ? (
                                                    <>
                                                        <span className="ai-loading-spinner"></span>
                                                        生成中...
                                                    </>
                                                ) : (
                                                    <>
                                                        AI 生成创意
                                                        <div className="icon">
                                                            <svg
                                                                height="24"
                                                                width="24"
                                                                viewBox="0 0 24 24"
                                                                xmlns="http://www.w3.org/2000/svg"
                                                            >
                                                                <path d="M0 0h24v24H0z" fill="none"></path>
                                                                <path
                                                                    d="M16.172 11l-5.364-5.364 1.414-1.414L20 12l-7.778 7.778-1.414-1.414L16.172 13H4v-2z"
                                                                    fill="currentColor"
                                                                ></path>
                                                            </svg>
                                                        </div>
                                                    </>
                                                )}
                                            </button>
                                        </div>

                                        <div className="ai-hint">
                                            提示：请先填写广告内容描述，AI 将根据描述生成创意建议
                                        </div>

                                        {aiError && (
                                            <div className="ai-error">{aiError}</div>
                                        )}

                                        {aiCreativeResult && (
                                            <div className="ai-result-section">
                                                <div className="ai-result-title">广告创意建议</div>
                                                
                                                <div className="ai-result-group">
                                                    <div className="ai-result-label">推荐标题</div>
                                                    {aiCreativeResult.titles.map((title, i) => (
                                                        <div key={i} className={`ai-result-item ${appliedItem === `title-${i}` ? 'applied' : ''}`}>
                                                            <span className="ai-result-text">{title}</span>
                                                            <button 
                                                                className={`ai-apply-btn ${appliedItem === `title-${i}` ? 'applied' : ''}`}
                                                                onClick={() => applyCreativeTitle(title, i)}
                                                                disabled={appliedItem === `title-${i}`}
                                                            >
                                                                {appliedItem === `title-${i}` ? '✓ 已应用' : '应用'}
                                                            </button>
                                                        </div>
                                                    ))}
                                                </div>

                                                <div className="ai-result-group">
                                                    <div className="ai-result-label">推荐文案</div>
                                                    {aiCreativeResult.texts.map((text, i) => (
                                                        <div key={i} className={`ai-result-item ${appliedItem === `text-${i}` ? 'applied' : ''}`}>
                                                            <span className="ai-result-text">{text}</span>
                                                            <button 
                                                                className={`ai-apply-btn ${appliedItem === `text-${i}` ? 'applied' : ''}`}
                                                                onClick={() => applyCreativeText(text, i)}
                                                                disabled={appliedItem === `text-${i}`}
                                                            >
                                                                {appliedItem === `text-${i}` ? '✓ 已应用' : '应用'}
                                                            </button>
                                                        </div>
                                                    ))}
                                                </div>

                                                <div className="ai-result-group">
                                                    <div className="ai-result-label">推荐关键词</div>
                                                    <div className="ai-keywords">
                                                        {aiCreativeResult.keywords.map((kw, i) => (
                                                            <span key={i} className="ai-keyword-tag">{kw}</span>
                                                        ))}
                                                    </div>
                                                </div>
                                            </div>
                                        )}

                                        {aiStrategyResult && (
                                            <div className="ai-result-section">
                                                <div className="ai-result-title">投放策略建议</div>
                                                
                                                <div className="ai-result-group">
                                                    <div className={`ai-result-label ${appliedItem === 'price' ? 'applied' : ''}`}>
                                                        建议出价: 
                                                        <span className="ai-price">¥{aiStrategyResult.bidSuggestion}</span>
                                                        <button 
                                                            className={`ai-apply-btn small ${appliedItem === 'price' ? 'applied' : ''}`}
                                                            onClick={() => applyStrategyPrice(aiStrategyResult.bidSuggestion)}
                                                            disabled={appliedItem === 'price'}
                                                        >
                                                            {appliedItem === 'price' ? '✓ 已应用' : '应用到表单'}
                                                        </button>
                                                    </div>
                                                </div>

                                                <div className="ai-result-group">
                                                    <div className="ai-result-label">目标用户</div>
                                                    <div className="ai-tags">
                                                        {aiStrategyResult.targetUsers.map((user, i) => (
                                                            <span key={i} className="ai-tag">{user}</span>
                                                        ))}
                                                    </div>
                                                </div>

                                                <div className="ai-result-group">
                                                    <div className="ai-result-label">兴趣标签</div>
                                                    <div className="ai-tags">
                                                        {aiStrategyResult.interests.map((interest, i) => (
                                                            <span key={i} className="ai-tag interest">{interest}</span>
                                                        ))}
                                                    </div>
                                                </div>

                                                <div className="ai-result-group">
                                                    <div className="ai-result-label">策略说明</div>
                                                    <div className="ai-reason">{aiStrategyResult.reason}</div>
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>
                        </>
                    )}
                </div>

                <div className="modal-footer">
                    <button className="btn btn-default" onClick={onClose}>取消</button>
                    <button
                        className="btn btn-primary"
                        onClick={handleSubmit}
                        disabled={loading}
                    >
                        确定
                    </button>
                </div>
            </div>
        </div>
    );
};

export default AdModal;
