import React, { useState, useEffect } from 'react';
import { getFormConfig, generateAdStrategy, getMaterials, Material } from '../api';
import {
    Ad,
    AdInput,
    FormFieldConfig,
    AdStrategyOutput
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

interface FormData { [key:string]: string | number | string[]; videos:string[]; }

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
    const [materials, setMaterials] = useState<Material[]>([]);
    const [materialError, setMaterialError] = useState('');
    const [materialCursor, setMaterialCursor] = useState<string | null>(null);
    const [materialLoading, setMaterialLoading] = useState(false);
    useEffect(() => {
        if (!isOpen) return;
        const controller = new AbortController();
        setMaterialLoading(true); setMaterialError('');
        getMaterials(undefined,controller.signal).then(p=>{setMaterials(p.items);setMaterialCursor(p.next_cursor);}).catch(()=>{if(!controller.signal.aborted)setMaterialError('素材读取失败，请关闭后重试');}).finally(()=>{if(!controller.signal.aborted)setMaterialLoading(false);});
        return () => controller.abort();
    },[isOpen]);
    const [formConfig, setFormConfig] = useState<FormFieldConfig[]>([]);
    const [formData, setFormData] = useState<FormData>({ videos: [] });
    const [errors, setErrors] = useState<Record<string, string>>({});
    const [loading, setLoading] = useState(false);

    const [aiPanelOpen, setAiPanelOpen] = useState(false);
    const [aiLoading, setAiLoading] = useState(false);
    const [aiIndustry, setAiIndustry] = useState('食品饮料');
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
        setFormData(prev => ({ ...prev, [field]: value, ...(field === 'content' ? {material_id:''} : {}) }));
        if (errors[field]) {
            setErrors(prev => {
                const newErrors = { ...prev };
                delete newErrors[field];
                return newErrors;
            });
        }
    };

    const handleAiGenerate = async () => {
        const description = formData.content as string || formData.title as string;
        if (!description.trim()) {
            setAiError('请先填写广告标题或内容描述');
            return;
        }

        setAiLoading(true);
        setAiError(null);
        setAiStrategyResult(null);

        try {
            const strategyRes = await generateAdStrategy({ adDescription: description, industry: aiIndustry });
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
            videos: formData.videos,
            ...(formData.material_id ? {material_id:String(formData.material_id)} : {})
        };

        onSubmit(submitData);
    };

    if (!isOpen) return null;

    const titleMap: Record<string, string> = {
        create: '新建广告',
        edit: '编辑广告',
        copy: initialData?.id ? '复制广告' : '使用素材新建广告'
    };


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

                            <div className="ad-material-reference">
                                <strong>广告素材</strong>
                                <p>选择已有素材可供多个广告复用。改写正文会保存为新素材。</p>
                                <label htmlFor="ad-material">引用素材</label>
                                <select id="ad-material" className="form-input" value={String(formData.material_id || '')} disabled={materialLoading} onChange={e=>{
                                    const m=materials.find(x=>x.id===e.target.value);
                                    setFormData(prev=>({...prev,material_id:m?.id || '',...(m?{content:m.content,videos:m.videos}:{})}));
                                }}>
                                    <option value="">使用当前正文和视频（自动去重）</option>
                                    {formData.material_id && !materials.some(m=>m.id===formData.material_id) && <option value={String(formData.material_id)}>当前素材 · {String(formData.material_id).slice(0,8)}</option>}
                                    {materials.map(m=><option key={m.id} value={m.id}>{m.name} · {m.usage_count} 条广告使用</option>)}
                                </select>
                                {materialError && <p role="alert">{materialError}</p>}
                                {materialCursor && <button type="button" disabled={materialLoading} onClick={async()=>{setMaterialLoading(true);try{const p=await getMaterials(materialCursor);setMaterials(old=>[...old,...p.items]);setMaterialCursor(p.next_cursor);}catch{setMaterialError('素材读取失败，请重试');}finally{setMaterialLoading(false);}}}>加载更多素材</button>}
                                {(formData.videos || []).map((url, i) => <video key={url + i} controls preload="metadata" src={url} style={{width:'100%',maxHeight:180,borderRadius:8}} />)}
                                <button type="button" className="btn btn-default" onClick={() => { onClose(); window.location.hash = 'creatives'; }}>编辑素材✨</button>
                            </div>

                            <div className="ai-assist-section">
                                <div className="ai-assist-header" onClick={() => setAiPanelOpen(!aiPanelOpen)}>
                                    <div className="ai-assist-title">
                                        <span className="ai-icon">✨</span>
                                        AI 投放助手
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
                                                        获取投放建议
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
                                            提示：请先填写广告内容描述，AI 将根据描述提供推广目标、人群及投放建议；不会修改创意素材
                                        </div>

                                        {aiError && (
                                            <div className="ai-error">{aiError}</div>
                                        )}

                                        {aiStrategyResult && (
                                            <div className="ai-result-section">
                                                <div className="ai-result-title">投放策略建议（尚未执行）</div>
                                                <div className="ai-result-group"><div className="ai-result-label">推广目标</div><p>{aiStrategyResult.recommendedObjective || '需进一步明确推广目标'}</p></div>

                                                <div className="ai-result-group">
                                                    <div className={`ai-result-label ${appliedItem === 'price' ? 'applied' : ''}`}>
                                                        建议出价:
                                                        <span className="ai-price">{aiStrategyResult.bidSuggestion === null ? '依据不足，暂不建议金额' : `¥${aiStrategyResult.bidSuggestion}`}</span>
                                                        {aiStrategyResult.bidSuggestion !== null && <button
                                                            className={`ai-apply-btn small ${appliedItem === 'price' ? 'applied' : ''}`}
                                                            onClick={() => applyStrategyPrice(aiStrategyResult.bidSuggestion!)}
                                                            disabled={appliedItem === 'price'}
                                                        >
                                                            {appliedItem === 'price' ? '✓ 已应用' : '应用到表单'}
                                                        </button>}
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
