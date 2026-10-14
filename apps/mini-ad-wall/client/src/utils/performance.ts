import type { Ad } from '../types';
import fixture from '../data/merchant-demo.json';

// A frozen, explicitly synthetic ledger. Filtering/pagination never regenerates facts.
export const demo = fixture;
export const days = fixture.days;
export const periodLabel = (range: PerformanceRange) => range === '7' ? '10 月 8–14 日' : '10 月 12–14 日';
export type Measures = { impressions: number; clicks: number; orders: number; spend: number; revenue: number; grossRevenue: number; refunds: number; refund: number };
const keys = ['impressions', 'clicks', 'orders', 'spend', 'revenue', 'grossRevenue', 'refunds', 'refund'] as const;
const empty = (): Measures => ({ impressions: 0, clicks: 0, orders: 0, spend: 0, revenue: 0, grossRevenue: 0, refunds: 0, refund: 0 });
const byAd = new Map(fixture.ads.map(ad => [ad.id, ad]));
const orders = new Map(fixture.orders.map(order => [order.id, order]));
export const demoAdFor = (id: string) => byAd.get(id);
export const publisherLabel = (publisher: string) => publisher.replace(/\s*·\s*模拟投放\s*$/, '');
export const deliveryStatusFor = (id: string) => {
    const ad = byAd.get(id);
    if (!ad?.daily.some(row => row.impressions > 0)) return '未投放';
    return ad.status.startsWith('暂停') ? '已投放 · 已暂停' : '已投放';
};

export function buildSample(ads: Ad[]) {
    const creatives = Array.from(new Map(ads.map(ad => [ad.id, ad])).values()).map(ad => {
        const snapshot = byAd.get(ad.id);
        return { id: `${ad.id}:material`, adId: ad.id, adTitle: ad.title,
            name: snapshot?.material_name || '暂无投放记录', version: ad.version,
            ...performanceForAd(ad) };
    });
    const rows = creatives.flatMap(creative => days.map(day => {
        const record = byAd.get(creative.adId)?.daily.find(row => row.day === day);
        const paid = fixture.orders.filter(order => order.ad_id === creative.adId && order.paid_at === day);
        const refunded = fixture.refunds.filter(refund => refund.refunded_at === day && orders.get(refund.order_id)?.ad_id === creative.adId);
        const grossRevenue = paid.reduce((total, order) => total + order.amount_minor, 0);
        const refund = refunded.reduce((total, item) => total + item.amount_minor, 0);
        return { day, creative: creative.id, impressions: record?.impressions || 0,
            clicks: record?.click_costs_minor.length || 0, spend: record?.click_costs_minor.reduce((total, value) => total + value, 0) || 0,
            orders: paid.length, refunds: refunded.length, grossRevenue, refund, revenue: grossRevenue - refund };
    }));
    return { creatives, rows };
}
export const sum = (items: Measures[]): Measures => items.reduce((total, item) => {
    for (const key of keys) total[key] += item[key];
    return total;
}, empty());
export type PerformanceRange = '7' | '3';
export function performanceForAd(ad: Ad, range: PerformanceRange = '7'): Measures {
    const snapshot = byAd.get(ad.id);
    if (!snapshot) return empty();
    const selectedDays = range === '7' ? days : days.slice(-3);
    const paid = fixture.orders.filter(order => order.ad_id === ad.id && selectedDays.includes(order.paid_at));
    const refunded = fixture.refunds.filter(refund => selectedDays.includes(refund.refunded_at) && orders.get(refund.order_id)?.ad_id === ad.id);
    const result = empty();
    for (const record of snapshot.daily.filter(row => selectedDays.includes(row.day))) {
        result.impressions += record.impressions;
        result.clicks += record.click_costs_minor.length;
        result.spend += record.click_costs_minor.reduce((total, cost) => total + cost, 0);
    }
    result.orders = paid.length;
    result.refunds = refunded.length;
    result.grossRevenue = paid.reduce((total, order) => total + order.amount_minor, 0);
    result.refund = refunded.reduce((total, refund) => total + refund.amount_minor, 0);
    result.revenue = result.grossRevenue - result.refund;
    return result;
}
export function materialPerformance(usage: {id: string; video_count: number}[], range: PerformanceRange): Measures {
    return sum(Array.from(new Set(usage.map(ad => ad.id))).map(id => performanceForAd({id} as Ad, range)));
}
