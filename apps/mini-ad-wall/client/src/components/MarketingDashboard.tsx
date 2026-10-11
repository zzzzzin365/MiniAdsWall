import { FormEvent, useEffect, useRef, useState } from 'react';
import { getMarketingReport, getMarketingSnapshot, getMetricEstimates } from '../api';
import type { MarketingReport, MetricEstimates, MetricValue } from '../types/marketing';
import './MarketingDashboard.css';

const names: Record<string, string> = { ctr: 'CTR 点击率', cvr: 'CVR 点击后转化率', cpa_minor: 'CPA 转化成本', cpc_minor: 'CPC 点击成本', cpm_minor: 'CPM 千次曝光成本', roas: 'ROAS 广告支出回报', refund_rate: '退款订单率', realized_revenue_per_mille_minor: '历史每千次曝光归因净收入', ecpm_minor: 'eCPM 预估千次曝光价值', ltv_minor: 'LTV 客户生命周期价值' };
const reasons: Record<string, string> = { zero_denominator: '没有足够样本计算', spend_coverage_incomplete: '费用来源尚未全部核对', aggregation_lag: '归因处理尚未跟上数据', prediction_model_and_bid_basis_missing: '未接入预测模型和出价口径', stable_customer_identity_and_lifetime_history_missing: '缺少客户身份和完整生命周期记录' };
const warnings: Record<string, string> = { aggregation_lag: '归因处理滞后，相关指标暂不可用', observation_open: '观察期未结束，后续回传可能修订结果', spend_sources_not_declared: '费用来源尚未声明', spend_coverage_incomplete: '费用不完整，成本和回报指标暂不可用', spend_source_policy_changed: '费用来源配置发生变化，需要核对', clicks_exceed_impressions: '点击超过曝光，请检查缺失曝光', multiple_orders_per_click: '一次点击可能归因多笔订单，CVR可以超过100%' };
const totals: Record<string, string> = { impressions: '有效曝光', clicks: '有效点击', paid_orders: '区间支付订单', cohort_orders: '点击归因订单', refunded_orders: '退款订单', revenue_minor: '归因净收入', spend_minor: '已接收花费' };

function format(m: MetricValue) {
    if (m.value === null) return '—';
    if (m.unit === 'minor') return '¥' + (Number(m.value) / 100).toLocaleString('zh-CN', { maximumFractionDigits: 4 });
    return Number(m.value).toLocaleString('zh-CN', { maximumFractionDigits: 4 });
}
function date(offset = 0) { return new Date(Date.now() + 8 * 3600000 + offset * 86400000).toISOString().slice(0, 10); }
function message(error: unknown) {
    const e = error as { response?: { data?: { error?: { message?: string } } }; message?: string };
    return e.response?.data?.error?.message || e.message || '查询失败，请重试';
}
function MetricCards({ metrics }: { metrics: Record<string, MetricValue> }) {
    return <div className="marketing-metrics">{Object.entries(metrics).map(([key, metric]) => <article className="marketing-metric" key={key}>
        <h4>{names[key] || key}</h4>
        <strong>{metric.value !== null && ['ctr', 'cvr', 'refund_rate'].includes(key) ? (Number(metric.value) * 100).toFixed(2) + '%' : format(metric)}</strong>
        <p>{metric.status === 'estimate' ? '按填写假设估算' : metric.status === 'provisional' ? '观察中' : metric.status === 'complete' ? '观察期已结束' : '数据不足'}</p>
        {metric.reasons?.map(reason => <small key={reason}>{reasons[reason] || reason}</small>)}
        {metric.formula ? <small>{metric.formula}</small> : null}
    </article>)}</div>;
}

export default function MarketingDashboard() {
    const [report, setReport] = useState<MarketingReport | null>(null);
    const [estimates, setEstimates] = useState<MetricEstimates | null>(null);
    const [busy, setBusy] = useState(false), [error, setError] = useState('');
    const request = useRef<AbortController | null>(null);
    useEffect(() => () => request.current?.abort(), []);
    async function perform(action: (signal: AbortSignal) => Promise<void>) {
        request.current?.abort();
        const controller = new AbortController(); request.current = controller;
        setBusy(true); setError('');
        try { await action(controller.signal); }
        catch (e) { if (!controller.signal.aborted) setError(message(e)); }
        finally { if (!controller.signal.aborted) setBusy(false); }
    }
    function submit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        const data = new FormData(event.currentTarget), value = (key: string) => String(data.get(key) || '').trim();
        const zone = value('timezone'), offset = zone === 'Asia/Shanghai' ? '+08:00' : 'Z';
        const query: MarketingReport['query'] = { brand_id: value('brand'), campaign_id: value('campaign'), environment: value('environment'),
            start: new Date(value('start') + 'T00:00:00' + offset).toISOString(), end: new Date(value('end') + 'T00:00:00' + offset).toISOString(), timezone: zone, rule_version: 'last-click-v1' };
        if (value('creative')) { query.creative_id = value('creative'); query.creative_version = Number(value('version')); }
        setReport(null);
        void perform(async signal => { const result = await getMarketingReport(query, signal); if (!signal.aborted) setReport(result); });
    }
    function snapshot(event: FormEvent<HTMLFormElement>) {
        event.preventDefault(); const id = String(new FormData(event.currentTarget).get('report_id') || '').trim(); setReport(null);
        void perform(async signal => { const result = await getMarketingSnapshot(id, signal); if (!signal.aborted) setReport(result); });
    }
    function estimate(event: FormEvent<HTMLFormElement>) {
        event.preventDefault(); const data = new FormData(event.currentTarget), n = (key: string) => Number(data.get(key));
        setEstimates(null);
        void perform(async signal => {
            const result = await getMetricEstimates({ ecpm: { predicted_ctr: n('pctr'), predicted_cvr: n('pcvr'), bid_minor: n('bid'), bid_basis: String(data.get('basis')) as 'CPA' | 'CPC' },
                ltv: { average_order_net_revenue_minor: n('aov'), orders_per_month: n('frequency'), lifetime_months: n('lifetime') } }, signal);
            if (!signal.aborted) setEstimates(result);
        });
    }
    function download() {
        if (!report) return;
        const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }));
        const link = document.createElement('a'); link.href = url; link.download = 'marketing-report-' + report.report_id + '.json'; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
    return <section className="marketing-dashboard" aria-label="投放效果指标">
        <header><h3>广告效果指标</h3><p>按活动和素材版本查询曝光、订单、成本与回报。旧广告累计点击不混入此报表。</p></header>
        <form className="marketing-form" onSubmit={submit}>
            <label>品牌 ID<input name="brand" required /></label><label>活动 ID<input name="campaign" required /></label>
            <label>环境<select name="environment"><option value="synthetic">模拟数据</option><option value="sandbox">沙箱</option><option value="production">真实数据</option></select></label>
            <label>时区<select name="timezone"><option>Asia/Shanghai</option><option>UTC</option></select></label>
            <label>开始日<input name="start" type="date" required defaultValue={date(-7)} /></label><label>结束日（不含）<input name="end" type="date" required defaultValue={date(1)} /></label>
            <label>素材 ID（可选）<input name="creative" /></label><label>素材版本<input name="version" type="number" min="1" step="1" defaultValue="1" /></label>
            <div className="marketing-form-actions"><button disabled={busy} type="submit">查询指标</button></div>
        </form>
        <form className="marketing-snapshot" onSubmit={snapshot}><label>历史报表 ID<input name="report_id" required /></label><button disabled={busy} type="submit">查看历史快照</button></form>
        {busy ? <p role="status">正在查询…</p> : null}{error ? <p className="marketing-error" role="alert">{error}</p> : null}
        {report ? <section aria-label="报表结果">
            <h4>{report.data_source === 'synthetic' ? '模拟数据' : report.data_source === 'sandbox' ? '沙箱数据' : '真实数据'} · {report.status === 'stale' ? '处理滞后' : report.status === 'complete' ? '观察期已结束' : '观察中'}</h4>
            <p>{report.query.start} 至 {report.query.end}（不含） · {report.query.timezone}</p>
            <p>活动：{report.query.campaign_id}{report.query.creative_id ? ` · 素材：${report.query.creative_id} v${report.query.creative_version}` : ''}</p>
            <dl className="marketing-totals">{Object.entries(report.totals).map(([key, value]) => <div key={key}><dt>{totals[key] || key}</dt><dd>{value === null ? '—' : key.endsWith('_minor') ? '¥' + (value / 100).toFixed(2) : value.toLocaleString()}</dd></div>)}</dl>
            <MetricCards metrics={report.metrics} />
            <ul>{report.warnings.map(w => <li key={w}>{warnings[w] || w}</li>)}</ul>
            <p>费用核对：{report.coverage.status === 'complete' ? '完整' : '不完整'}；缺失来源：{report.coverage.missing_sources.join('、') || '无'}</p>
            <p>归因观察至：{report.observation.attribution_ends_at}；迟到回传观察至：{report.observation.late_arrival_ends_at}</p>
            <p>CVR统计点击所归因的订单；CPA、ROAS统计区间支付和花费，跨期数据不能解释为因果收益。</p>
            <p>报表 ID：{report.report_id} · 数据接收截止：{report.received_cutoff}</p><button type="button" onClick={download}>下载此报表</button>
        </section> : null}
        <details className="marketing-estimates"><summary>eCPM 与 LTV 估算工具</summary>
            <p>填写预测或业务假设进行计算。估算结果不代表真实投放表现，不影响广告排序或扣费。</p>
            <form className="marketing-form" onSubmit={estimate}>
                <label>预估点击率（0–1）<input name="pctr" type="number" min="0" max="1" step="0.000001" required /></label>
                <label>预估转化率（0–1）<input name="pcvr" type="number" min="0" max="1" step="0.000001" required /></label>
                <label>出价口径<select name="basis"><option>CPA</option><option>CPC</option></select></label><label>出价（分）<input name="bid" type="number" min="0" step="1" required /></label>
                <label>平均订单净收入（分）<input name="aov" type="number" min="0" step="1" required /></label><label>月购买次数<input name="frequency" type="number" min="0" max="100000" step="0.000001" required /></label>
                <label>预计客户存续（月）<input name="lifetime" type="number" min="1" max="1200" step="1" required /></label>
                <div className="marketing-form-actions"><button disabled={busy} type="submit">计算估算值</button></div>
            </form>
            {estimates ? <MetricCards metrics={estimates.metrics} /> : null}
        </details>
    </section>;
}
