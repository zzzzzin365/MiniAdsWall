import React, { useState, useEffect, useRef } from 'react';
import { getFormConfig, generateAdStrategy, getAdStrategyContext, getMaterials, Material } from '../api';
import { usePerformance } from '../contexts/PerformanceContext';
import {
    Ad,
    AdInput,
    FormFieldConfig,
    AdStrategyOutput, DeliveryContext
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
        <div className={`form-group ad-field-${field}`}>
            <label className="form-label" htmlFor={field}>
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
    const {range} = usePerformance();
    const [delivery, setDelivery] = useState<DeliveryContext|null>(null);
    const [contextLoading, setContextLoading] = useState(false);
    const [contextError, setContextError] = useState('');
    const [goal, setGoal] = useState<'clicks'|'orders'|'leads'>('clicks');
    const [dailyBudget, setDailyBudget] = useState('');
    const [bidDays, setBidDays] = useState('7');
    const [maxCpc, setMaxCpc] = useState('');
    const [unitProfit, setUnitProfit] = useState('');
    const [settingErrors, setSettingErrors] = useState<Record<string,string>>({});
    const adviceEpoch = useRef(0);
    useEffect(()=>{
        if (!isOpen) return;
        adviceEpoch.current++;
        const controller = new AbortController();
        setDelivery(null);setContextLoading(true);setContextError('');setAiLoading(false);setAiError(null);setAiStrategyResult(null);setSettingErrors({});setAppliedItem(null);
        getAdStrategyContext(mode==='edit'?initialData?.id:undefined,range,controller.signal).then(data=>{
            if(controller.signal.aborted)return;
            setDelivery(data);setGoal(data.defaults.goal);setDailyBudget(data.defaults.dailyBudget===null?'':String(data.defaults.dailyBudget));
            setBidDays(String(data.defaults.days));setMaxCpc(data.defaults.maxCpc===null?'':String(data.defaults.maxCpc));setUnitProfit(data.defaults.unitProfit===null?'':String(data.defaults.unitProfit));
        }).catch(error=>{if(!controller.signal.aborted)setContextError(error.response?.data?.error || '投放数据读取失败，请关闭后重试');}).finally(()=>{if(!controller.signal.aborted)setContextLoading(false);});
        return ()=>{controller.abort();adviceEpoch.current++;};
    },[isOpen,mode,initialData?.id,range]);

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
        const epoch = adviceEpoch.current;
        const description = formData.content as string || formData.title as string;
        if (!description.trim()) {
            setAiError('请先填写广告标题或内容描述');
            return;
        }
        const invalid:Record<string,string> = {};
        if (!Number.isFinite(Number(dailyBudget)) || Number(dailyBudget)<.01 || Number(dailyBudget)>1000000) invalid.budget='请填写大于0的日预算';
        if (!Number.isInteger(Number(bidDays)) || Number(bidDays)<1 || Number(bidDays)>365) invalid.days='填写1–365个整天';
        if (!Number.isFinite(Number(maxCpc)) || Number(maxCpc)<.01 || Number(maxCpc)>100) invalid.cap='填写0.01–100元';
        if (unitProfit && (!Number.isFinite(Number(unitProfit)) || Number(unitProfit)<=0)) invalid.profit='单笔利润需大于0';
        setSettingErrors(invalid);
        if (Object.keys(invalid).length || !delivery) return;

        setAiLoading(true);
        setAiError(null);
        setAiStrategyResult(null);

        try {
            const strategyRes = await generateAdStrategy({ adDescription: description, industry: aiIndustry,
                ...(mode==='edit'&&initialData?.id?{adId:initialData.id}:{}),range,
                materialId:String(formData.material_id || ''),
                settings:{goal,dailyBudget:Number(dailyBudget),days:Number(bidDays),maxCpc:Number(maxCpc),...(unitProfit?{unitProfit:Number(unitProfit)}:{})} });
            if(epoch!==adviceEpoch.current)return;
            if ('error' in strategyRes && strategyRes.error) {
                setAiError(strategyRes.message);
            } else {
                setAiStrategyResult(strategyRes as AdStrategyOutput);
            }
        } catch {
            if(epoch===adviceEpoch.current)setAiError('AI 服务请求失败，请稍后重试');
        } finally {
            if(epoch===adviceEpoch.current)setAiLoading(false);
        }
    };

    const updateSetting = (setter:(value:string)=>void,value:string) => {
        setter(value);setAiStrategyResult(null);setAiError(null);setSettingErrors({});setAppliedItem(null);
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
            <div className="modal-box ad-editor-modal" role="dialog" aria-modal="true" aria-labelledby="ad-editor-title">
                <div className="modal-header"><h2 id="ad-editor-title">{titleMap[mode]}</h2><button type="button" className="ad-editor-close" aria-label="关闭广告编辑框" onClick={onClose}>×</button></div>

                <div className="modal-body">
                    {loading ? (
                        <div className="form-loading">加载表单配置中...</div>
                    ) : (
                        <>
                            <div className="ad-form-fields">{formConfig.map(fieldConfig => (
                                <FormField
                                    key={fieldConfig.field}
                                    config={fieldConfig}
                                    value={formData[fieldConfig.field]}
                                    onChange={handleFieldChange}
                                    error={errors[fieldConfig.field]}
                                />
                            ))}</div>

                            <div className="ad-material-reference">
                                <strong>广告素材</strong>
                                <p>选择已有素材可供多个广告复用。改写正文会保存为新素材。</p>
                                <label htmlFor="ad-material">引用素材</label>
                                <div className="ad-material-select-row">
                                <select id="ad-material" className="form-input" value={String(formData.material_id || '')} disabled={materialLoading} onChange={e=>{
                                    const m=materials.find(x=>x.id===e.target.value);
                                    setFormData(prev=>({...prev,material_id:m?.id || '',...(m?{content:m.content,videos:m.videos}:{})}));
                                }}>
                                    <option value="">使用当前正文和视频（自动去重）</option>
                                    {formData.material_id && !materials.some(m=>m.id===formData.material_id) && <option value={String(formData.material_id)}>当前素材 · {String(formData.material_id).slice(0,8)}</option>}
                                    {materials.map(m=><option key={m.id} value={m.id}>{m.name} · {m.usage_count} 条广告使用</option>)}
                                </select>
                                <button type="button" className="btn btn-default" onClick={() => { onClose(); window.location.hash = 'creatives'; }}>编辑素材✨</button>
                                </div>
                                {materialError && <p role="alert">{materialError}</p>}
                                {materialCursor && <button type="button" disabled={materialLoading} onClick={async()=>{setMaterialLoading(true);try{const p=await getMaterials(materialCursor);setMaterials(old=>[...old,...p.items]);setMaterialCursor(p.next_cursor);}catch{setMaterialError('素材读取失败，请重试');}finally{setMaterialLoading(false);}}}>加载更多素材</button>}
                                {(formData.videos || []).map((url, i) => <video key={url + i} controls preload="metadata" src={url} style={{width:'100%',maxHeight:180,borderRadius:8}} />)}
                            </div>

                            <div className="ai-assist-section">
                                <button type="button" className="ai-assist-header" aria-expanded={aiPanelOpen} aria-controls="ad-ai-advice" onClick={() => setAiPanelOpen(!aiPanelOpen)}>
                                    <div className="ai-assist-title">
                                        <span className="ai-icon">✨</span>
                                        AI 投放助手
                                    </div>
                                    <span className={`ai-toggle ${aiPanelOpen ? 'open' : ''}`}>
                                        {aiPanelOpen ? '收起' : '展开'}
                                    </span>
                                </button>

                                {aiPanelOpen && (
                                    <div className="ai-assist-body" id="ad-ai-advice">
                                        {contextLoading && <p role="status" className="ai-hint">正在读取广告投放数据…</p>}
                                        {contextError && <p role="alert" className="ai-error">{contextError}</p>}
                                        {delivery && <section className="ad-bid-context" aria-label="出价建议数据">
                                            <div className="ad-bid-context-title"><strong>{delivery.status==='launched'?'已投放 · 已自动读取数据':'未投放 · 设置试投计划'}</strong><span>按点击计费（CPC）</span></div>
                                            {delivery.performance ? <><p>统计周期：{delivery.window[0]} 至 {delivery.window[delivery.window.length-1]}</p><dl className="ad-bid-metrics">
                                                <div><dt>当前出价</dt><dd>¥{delivery.currentBid?.toFixed(2)}</dd></div>
                                                <div><dt>有效曝光</dt><dd>{delivery.performance.impressions.toLocaleString()}</dd></div>
                                                <div><dt>有效点击</dt><dd>{delivery.performance.clicks.toLocaleString()}</dd></div>
                                                <div><dt>广告花费</dt><dd>¥{(delivery.performance.spend_minor/100).toFixed(2)}</dd></div>
                                                <div><dt>支付订单</dt><dd>{delivery.performance.orders}</dd></div>
                                                <div><dt>平均点击成本</dt><dd>{delivery.performance.average_cpc_minor===null?'暂无点击':`¥${(delivery.performance.average_cpc_minor/100).toFixed(2)}`}</dd></div>
                                            </dl>{delivery.performance.material_changed && <p>当前素材已变更，历史效果对应原素材，本轮将保守建议。</p>}</> : <p>无需填写曝光、点击或订单。补齐以下计划后，即可获取起始试投价。</p>}
                                        </section>}
                                        <fieldset className="ad-bid-settings" disabled={contextLoading||aiLoading||!delivery}>
                                            <legend>{delivery?.status==='launched'?'本次优化目标与限制':'试投目标与限制'}</legend>
                                            <div className="ad-bid-fields">
                                                <label htmlFor="ad-bid-goal">推广目标<select id="ad-bid-goal" className="ai-select" value={goal} onChange={e=>{setGoal(e.target.value as typeof goal);setAiStrategyResult(null);}}><option value="clicks">获取商品点击</option><option value="orders">获取成交订单</option><option value="leads">获取咨询线索</option></select></label>
                                                <label htmlFor="ad-bid-industry">行业分类<select id="ad-bid-industry" className="ai-select" value={aiIndustry} onChange={e=>updateSetting(setAiIndustry,e.target.value)}>{INDUSTRY_OPTIONS.map(opt=><option key={opt}>{opt}</option>)}</select></label>
                                                <label htmlFor="ad-bid-budget">日预算（元）<input id="ad-bid-budget" className="form-input" type="number" min="0.01" max="1000000" step="0.01" placeholder="例如 30" value={dailyBudget} aria-invalid={!!settingErrors.budget} onChange={e=>updateSetting(setDailyBudget,e.target.value)}/>{settingErrors.budget&&<span className="form-error">{settingErrors.budget}</span>}</label>
                                                <label htmlFor="ad-bid-days">投放天数<input id="ad-bid-days" className="form-input" type="number" min="1" max="365" step="1" value={bidDays} aria-invalid={!!settingErrors.days} onChange={e=>updateSetting(setBidDays,e.target.value)}/>{settingErrors.days&&<span className="form-error">{settingErrors.days}</span>}</label>
                                                <label htmlFor="ad-bid-cap">最高点击出价（元）<input id="ad-bid-cap" className="form-input" type="number" min="0.01" max="100" step="0.01" placeholder="例如 0.80" value={maxCpc} aria-invalid={!!settingErrors.cap} onChange={e=>updateSetting(setMaxCpc,e.target.value)}/>{settingErrors.cap&&<span className="form-error">{settingErrors.cap}</span>}</label>
                                                {goal==='orders'&&<label htmlFor="ad-bid-profit">单笔利润（元，选填）<input id="ad-bid-profit" className="form-input" type="number" min="0.01" step="0.01" value={unitProfit} onChange={e=>updateSetting(setUnitProfit,e.target.value)}/>{settingErrors.profit&&<span className="form-error">{settingErrors.profit}</span>}</label>}
                                            </div>
                                            <p className="ad-bid-plan">{Number(dailyBudget)>0&&Number(bidDays)>0?`计划预算：¥${(Number(dailyBudget)*Number(bidDays)).toFixed(2)}，分 ${bidDays} 天观察。`:'填写预算和投放天数后生成试投计划。'}{delivery?.status==='new'?' 无历史数据时，以最高点击出价的50%起步。':' 已有预算和出价限制已带入，可按本次目标修改。'}</p>
                                        </fieldset>
                                        <div className="ai-input-row">
                                            <button
                                                className="btn btn-ai"
                                                onClick={handleAiGenerate}
                                                disabled={aiLoading||contextLoading||!delivery||!!contextError}
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
                                            AI 将结合投放数据、预算限制和营销知识给出具体出价。建议应用到表单后，仍需点击“确定”保存。
                                        </div>

                                        {aiError && (
                                            <div className="ai-error">{aiError}</div>
                                        )}

                                        {aiStrategyResult && (
                                            <section className="ai-result-section ad-strategy-result" aria-label="投放策略建议">
                                                <header className="ad-strategy-heading"><h3>投放策略建议</h3><span>尚未执行</span></header>
                                                <div className="ad-strategy-overview">
                                                    <section><h4>推广目标</h4><p>{aiStrategyResult.recommendedObjective || '需进一步明确推广目标'}</p></section>
                                                    <section><h4>建议出价</h4><strong className="ad-strategy-price">{aiStrategyResult.bidSuggestion === null ? '需补齐数据后确定金额' : `¥${aiStrategyResult.bidSuggestion.toFixed(2)}`}</strong>
                                                        {aiStrategyResult.bidBasis&&<p className="ad-bid-comparison">{aiStrategyResult.bidBasis.current_bid!==null?`当前 ¥${aiStrategyResult.bidBasis.current_bid.toFixed(2)} → 建议 ¥${aiStrategyResult.bidSuggestion?.toFixed(2)}`:'起始试投价'} · 元/点击</p>}
                                                        {aiStrategyResult.bidSuggestion !== null && <button type="button" className={`ai-apply-btn ${appliedItem === 'price' ? 'applied' : ''}`} onClick={() => applyStrategyPrice(aiStrategyResult.bidSuggestion!)} disabled={appliedItem === 'price'}>{appliedItem === 'price' ? '✓ 已应用' : '应用到出价'}</button>}
                                                    </section>
                                                </div>
                                                {aiStrategyResult.biddingAdvice && <section className="ad-strategy-block"><h4>出价与调整建议</h4><p>{aiStrategyResult.biddingAdvice}</p></section>}
                                                {!!aiStrategyResult.missingInformation?.length && <section className="ad-strategy-block ad-strategy-missing"><h4>需要补充的数据</h4><ul>{aiStrategyResult.missingInformation.map((item,index)=><li key={index}>{item}</li>)}</ul></section>}
                                                <section className="ad-strategy-block"><h4>目标用户</h4><ul className="ad-strategy-users">{aiStrategyResult.targetUsers.map((user,index)=><li key={index}>{user}</li>)}</ul></section>
                                                <section className="ad-strategy-block"><h4>兴趣标签</h4><div className="ai-tags">{aiStrategyResult.interests.map((interest,index)=><span className="ai-tag interest" key={index}>{interest}</span>)}</div></section>
                                                <section className="ad-strategy-block"><h4>策略说明</h4><p>{aiStrategyResult.reason}</p></section>
                                                {!!aiStrategyResult.knowledgeSources?.length && <section className="ad-strategy-block ad-strategy-sources"><h4>广告营销知识库依据</h4>{aiStrategyResult.knowledgeSources.map(source=><details key={source.id}><summary>{source.title||'营销知识片段'}</summary><p>{source.content}</p>{source.source_url&&/^https?:\/\//i.test(source.source_url)&&<a href={source.source_url} target="_blank" rel="noopener noreferrer">查看来源</a>}</details>)}</section>}
                                            </section>
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
