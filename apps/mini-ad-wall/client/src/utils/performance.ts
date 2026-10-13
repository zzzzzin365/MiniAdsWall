import type { Ad } from "../types";
export const days = ['2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04', '2026-10-05', '2026-10-06', '2026-10-07'];
export type Measures = { impressions: number; clicks: number; orders: number; spend: number; revenue: number };
const keys = ['impressions', 'clicks', 'orders', 'spend', 'revenue'] as const;
const weights = [10, 12, 13, 14, 15, 17, 19];
// Ad names and material configurations come from the loaded list. Performance is synthetic.
export function buildSample(ads: Ad[]) {
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
export const sum = (items: Measures[]): Measures => items.reduce((total, item) => {
    for (const key of keys) total[key] += item[key];
    return total;
}, { impressions: 0, clicks: 0, orders: 0, spend: 0, revenue: 0 });

export type PerformanceRange = '7' | '3';
export function performanceForAd(ad: Ad, range: PerformanceRange = '7'): Measures {
    const {rows} = buildSample([ad]);
    return sum(rows.filter(row => range === '7' || row.day >= days[4]));
}
export function materialPerformance(usage: {id: string; video_count: number}[], range: PerformanceRange): Measures {
    return sum(usage.map(ad => performanceForAd({id:ad.id,video_count:ad.video_count,videos:[],title:'',publisher:'',content:'',url:'',price:0,clicks:0,version:1},range)));
}
