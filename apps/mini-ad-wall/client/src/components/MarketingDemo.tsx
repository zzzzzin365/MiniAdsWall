import { useMemo, useState, type ReactNode } from 'react';
import { Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Legend, type ChartOptions } from 'chart.js';
import { Line } from 'react-chartjs-2';
import type { Ad } from '../types';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Legend);
const days = ['2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04', '2026-10-05', '2026-10-06', '2026-10-07'];
type Measures = { impressions: number; clicks: number; orders: number; spend: number; revenue: number };
const keys = ['impressions', 'clicks', 'orders', 'spend', 'revenue'] as const;
const weights = [10, 12, 13, 14, 15, 17, 19];
// Ad names and material configurations come from the loaded list. Performance is synthetic.
function buildSample(ads: Ad[]) {
    const creatives = ads.flatMap(ad => {
        const videoCount = ad.video_count ?? ad.videos.length;
        const materials = videoCount ? Array.from({ length: videoCount }, (_, index) => `视频素材 ${index + 1}`) : ['图文 / 链接'];
        return materials.map((material, index) => {
            const id = `${ad.id}:${index}`;
            const seed = Array.from(id).reduce((hash, character) => (hash * 31 + character.charCodeAt(0)) >>> 0, 0);
            const impressions = 20000 + seed % 30000;
            const clicks = Math.floor(impressions * (0.02 + (seed % 20) / 1000));
            const orders = Math.floor(clicks * (0.03 + (seed % 30) / 1000));
            return { id, adId: ad.id, adTitle: ad.title, name: material, version: ad.version, impressions, clicks, orders,
                spend: clicks * (150 + seed % 100), revenue: orders * (17000 + seed % 4000) };
        });
    });
    const rows = creatives.flatMap(creative => {
        const used: Measures = { impressions: 0, clicks: 0, orders: 0, spend: 0, revenue: 0 };
        return days.map((day, index) => {
            const values = { ...used };
            for (const key of keys) {
                values[key] = index === days.length - 1 ? creative[key] - used[key] : Math.floor(creative[key] * weights[index] / 100);
                used[key] += values[key];
            }
            return { day, creative: creative.id, ...values };
        });
    });
    return { creatives, rows };
}
const sum = (items: Measures[]): Measures => items.reduce((total, item) => {
    for (const key of keys) total[key] += item[key];
    return total;
}, { impressions: 0, clicks: 0, orders: 0, spend: 0, revenue: 0 });
const count = (value: number) => value.toLocaleString('zh-CN');
const money = (value: number) => '¥' + (value / 100).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
const percent = (value: number, denominator: number) => denominator ? (value / denominator * 100).toFixed(2) + '%' : '—';
const cost = (value: number, denominator: number) => denominator ? money(value / denominator) : '—';
const roas = (value: number, denominator: number) => denominator ? (value / denominator).toFixed(2) : '—';
const trendOptions: ChartOptions<'line'> = {
    responsive: true, maintainAspectRatio: false, animation: false,
    plugins: { legend: { position: 'top', labels: { usePointStyle: true, boxWidth: 8 } }, tooltip: { callbacks: { label: item => `${item.dataset.label}：¥${Number(item.parsed.y).toLocaleString('zh-CN')}` } } },
    scales: { y: { beginAtZero: true, title: { display: true, text: '金额（元）' }, grid: { color: '#edf2f7' } }, x: { grid: { display: false } } },
};

export default function MarketingDemo({ ads, loadMore, adStatus }: { ads: Ad[]; loadMore?: ReactNode; adStatus?: ReactNode }) {
    const [range, setRange] = useState('7');
    const [adId, setAdId] = useState('all');
    const [selectedCreative, setCreativeId] = useState('all');
    const selectedAd = ads.find(ad => ad.id === adId);
    const activeAdId = selectedAd?.id ?? 'all';
    const { creatives, rows } = useMemo(() => buildSample(selectedAd ? [selectedAd] : ads), [ads, selectedAd]);
    const creativeId = creatives.some(creative => creative.id === selectedCreative) ? selectedCreative : 'all';
    const selectedDays = range === '7' ? days : days.slice(-3);
    const filtered = useMemo(() => rows.filter(row => (range === '7' || row.day >= days[4]) && (creativeId === 'all' || row.creative === creativeId)), [rows, range, creativeId]);
    const total = sum(filtered);
    const summary = creatives.filter(creative => creativeId === 'all' || creative.id === creativeId).map(creative => ({ ...creative, ...sum(filtered.filter(row => row.creative === creative.id)) }));
    const daily = selectedDays.map(day => sum(filtered.filter(row => row.day === day)));
    const indicators = [
        { key: 'impressions', label: '有效曝光', value: count(total.impressions), description: '广告有效展示次数' },
        { key: 'clicks', label: '有效点击', value: count(total.clicks), description: `CTR ${percent(total.clicks, total.impressions)} · 点击 ÷ 曝光` },
        { key: 'orders', label: '归因订单', value: count(total.orders), description: `CVR ${percent(total.orders, total.clicks)} · 订单 ÷ 点击` },
        { key: 'spend', label: '广告花费', value: money(total.spend), description: `CPA ${cost(total.spend, total.orders)} · 花费 ÷ 订单` },
        { key: 'revenue', label: '归因净收入', value: money(total.revenue), description: '归因订单收入扣除退款' },
        { key: 'roas', label: 'ROAS 广告回报', value: roas(total.revenue, total.spend), description: '归因净收入 ÷ 花费，非利润率' },
    ];
    return <section className="marketing-demo" aria-label="模拟投放效果看板">
        <div className="demo-toolbar">
            <div className="demo-filters">
                <label htmlFor="demo-ad">广告<select aria-label="广告" id="demo-ad" value={activeAdId} disabled={!ads.length} onChange={event => { setAdId(event.target.value); setCreativeId('all'); }}><option value="all">全部已加载广告（{ads.length} 条）</option>{ads.map(ad => <option key={ad.id} value={ad.id}>{ad.title} · {ad.id}</option>)}</select></label>
                <label htmlFor="demo-period">统计日期<select aria-label="统计日期" id="demo-period" value={range} onChange={event => setRange(event.target.value)}><option value="7">10 月 1–7 日</option><option value="3">10 月 5–7 日</option></select></label>
                <label htmlFor="demo-creative">素材<select aria-label="素材" id="demo-creative" value={creativeId} disabled={!creatives.length} onChange={event => setCreativeId(event.target.value)}><option value="all">全部素材</option>{creatives.map(creative => <option key={creative.id} value={creative.id}>{selectedAd ? '' : `${creative.adTitle} · `}{creative.name}</option>)}</select></label>
            </div>
            {loadMore}
        </div>
        {!ads.length ? <div className="management-empty">{adStatus}<p>暂无可选广告，请先新建广告或重新加载广告列表。</p></div> : <>
        <div className="demo-kpis" aria-live="polite">{indicators.map(indicator => <article className="dashboard-stat-item demo-kpi" key={indicator.key} data-metric={indicator.key}><h3>{indicator.label}</h3><div className="stat-value">{indicator.value}</div><p>{indicator.description}</p></article>)}</div>
        <div className="charts-grid demo-charts">
            <section className="chart-container demo-funnel" aria-label="转化漏斗">
                <h3>转化漏斗</h3><p className="demo-chart-subtitle">看流量从展示到下单的转化</p>
                {[
                    { name: '有效曝光', value: total.impressions, hint: '流量入口' },
                    { name: '有效点击', value: total.clicks, hint: `曝光 → 点击：${percent(total.clicks, total.impressions)}` },
                    { name: '归因订单', value: total.orders, hint: `点击 → 订单：${percent(total.orders, total.clicks)}` },
                ].map(step => <div className="demo-funnel-step" key={step.name}><div><span>{step.name}</span><strong>{count(step.value)}</strong></div><div className="demo-funnel-track" aria-hidden="true"><span style={{ width: `${total.impressions ? step.value / total.impressions * 100 : 0}%` }} /></div><small>{step.hint}</small></div>)}
                <p className="demo-chart-footnote">条形长度按曝光占比绘制；示例采用最后点击归因，每单只归属一个素材。</p>
            </section>
            <section className="chart-container" aria-label="每日花费与收入趋势">
                <h3>每日花费与收入</h3><p className="demo-chart-subtitle">对比当日广告花费和归因净收入</p>
                <div className="demo-trend"><Line options={trendOptions} data={{ labels: selectedDays.map(day => day.slice(5)), datasets: [
                    { label: '广告花费', data: daily.map(item => item.spend / 100), borderColor: '#054DFF', backgroundColor: '#054DFF', borderWidth: 2, pointRadius: 4, tension: .25 },
                    { label: '归因净收入', data: daily.map(item => item.revenue / 100), borderColor: '#069ec5', backgroundColor: '#069ec5', borderWidth: 2, pointRadius: 4, tension: .25 },
                ] }} role="img" aria-label="模拟的每日广告花费与归因净收入折线图" /></div>
                <details className="demo-trend-data"><summary>查看趋势明细</summary><div className="demo-table-scroll"><table><caption className="management-sr-only">每日趋势数据，金额单位为元</caption><thead><tr><th scope="col">日期</th><th scope="col">花费</th><th scope="col">归因净收入</th></tr></thead><tbody>{daily.map((item, index) => <tr key={selectedDays[index]}><td>{selectedDays[index]}</td><td>{money(item.spend)}</td><td>{money(item.revenue)}</td></tr>)}</tbody></table></div></details>
            </section>
        </div>
        <section className="chart-container demo-creative-comparison" aria-label="素材表现对比">
            <h3>素材表现对比</h3><p className="demo-chart-subtitle">在同一统计日期内，比较素材的点击、转化成本与回报</p>
            <div className="demo-table-scroll" tabIndex={0} role="region" aria-label="素材对比表，可横向滚动"><table><caption className="management-sr-only">模拟素材投放表现</caption><thead><tr><th scope="col">广告 / 素材</th><th scope="col">曝光</th><th scope="col">点击</th><th scope="col">CTR</th><th scope="col">订单</th><th scope="col">CVR</th><th scope="col">花费</th><th scope="col">CPA</th><th scope="col">净收入</th><th scope="col">ROAS</th></tr></thead><tbody>{summary.map(creative => <tr key={creative.id}><th scope="row">{creative.adTitle}<small>{creative.name} · 广告版本 v{creative.version}</small></th><td>{count(creative.impressions)}</td><td>{count(creative.clicks)}</td><td>{percent(creative.clicks, creative.impressions)}</td><td>{count(creative.orders)}</td><td>{percent(creative.orders, creative.clicks)}</td><td>{money(creative.spend)}</td><td>{cost(creative.spend, creative.orders)}</td><td>{money(creative.revenue)}</td><td className="demo-roas">{roas(creative.revenue, creative.spend)}</td></tr>)}</tbody></table></div>
            <p className="demo-chart-footnote">CPC {cost(total.spend, total.clicks)} · CPM {cost(total.spend * 1000, total.impressions)}。ROAS 不扣商品成本，不能当作利润率。</p>
        </section>
        </>}
    </section>;
}
