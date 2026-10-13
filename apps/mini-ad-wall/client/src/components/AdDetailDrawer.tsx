import { usePerformance } from '../contexts/PerformanceContext';
import React, { useEffect, useRef } from 'react';
import { Ad } from '../types';

interface Props {
    ad: Ad;
    loading: boolean;
    error: string;
    onClose: () => void;
    onRetry: () => void;
    onEdit: (ad: Ad) => void;
    onCopy: (ad: Ad) => void;
    onPreview: (ad: Ad) => void;
}

function landingUrl(url: string): string | undefined {
    try {
        const parsed = new URL(/^[a-z][a-z\d+.-]*:/i.test(url) ? url : `https://${url}`);
        return ['http:', 'https:'].includes(parsed.protocol) ? parsed.href : undefined;
    } catch { return undefined; }
}

export default function AdDetailDrawer({ ad, loading, error, onClose, onRetry, onEdit, onCopy, onPreview }: Props) {
    const {measures,period}=usePerformance();
    const metrics=measures(ad);
    const dialog = useRef<HTMLDialogElement>(null);
    useEffect(() => { dialog.current?.showModal(); return () => dialog.current?.close(); }, []);
    const target = landingUrl(ad.url);
    const videos = ad.video_count ?? ad.videos.length;
    return <dialog ref={dialog} className="ad-detail-drawer" aria-labelledby="ad-detail-title" onCancel={onClose} onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
        <div className="ad-detail-content">
            <header className="ad-detail-header"><div><span className="ad-detail-eyebrow">广告详情</span><h2 id="ad-detail-title">{ad.title}</h2></div><button type="button" className="ad-detail-close" aria-label="关闭广告详情" onClick={onClose}>×</button></header>
            <div className="ad-detail-body">
                <div className="ad-detail-metrics"><div><span>配置出价</span><strong>¥{ad.price.toFixed(2)}</strong></div><div><span>有效点击 · {period}</span><strong>{measures(ad).clicks}</strong></div><div><span>视频素材</span><strong>{videos}</strong></div></div>
                <p className="ad-detail-note">{period} · 原投放效果面板数据（模拟）</p><div className="ad-detail-metrics"><div><span>有效曝光</span><strong>{metrics.impressions.toLocaleString('zh-CN')}</strong></div><div><span>归因订单</span><strong>{metrics.orders}</strong></div><div><span>广告花费</span><strong>¥{(metrics.spend/100).toLocaleString('zh-CN')}</strong></div><div><span>归因净收入</span><strong>¥{(metrics.revenue/100).toLocaleString('zh-CN')}</strong></div></div>
                <dl className="ad-detail-fields"><div><dt>发布者</dt><dd>{ad.publisher}</dd></div><div><dt>广告 ID</dt><dd>{ad.id}</dd></div><div><dt>素材 ID</dt><dd>{ad.material_id || "未关联"}</dd></div><div><dt>数据版本</dt><dd>v{ad.version}</dd></div><div><dt>落地页</dt><dd>{target ? <a href={target} target="_blank" rel="noopener noreferrer">{ad.url} ↗</a> : ad.url || '未设置'}</dd></div></dl>
                {loading ? <p role="status">正在读取完整详情…</p> : error ? <div className="ad-detail-error" role="alert">{error}<button type="button" className="btn btn-default" onClick={onRetry}>重试</button></div> : <>
                    <section className="ad-detail-section"><h3>创意文案</h3><p>{ad.content}</p></section>
                    <section className="ad-detail-section"><div className="ad-detail-section-heading"><h3>素材预览</h3>{videos > 0 && <button type="button" className="management-text-button" onClick={() => { onClose(); onPreview(ad); }}>播放首个视频</button>}</div><p className="ad-detail-note">运营预览不计入广告点击。</p>{ad.videos.map((video, index) => <video key={`${video}-${index}`} src={video} controls preload="metadata" aria-label={`视频素材 ${index + 1}`} />)}{!videos && <p className="ad-detail-note">当前广告使用文案与落地链接。</p>}</section>
                </>}
            </div>
            <footer className="ad-detail-footer"><button type="button" className="btn btn-default" disabled={loading || !!error} onClick={() => { onClose(); onCopy(ad); }}>复制广告</button><button type="button" className="btn btn-primary" disabled={loading || !!error} onClick={() => { onClose(); onEdit(ad); }}>编辑广告</button></footer>
        </div>
    </dialog>;
}
