import { assistantPerformance } from './assistant-data';
import { existsSync, readFileSync } from 'fs';
import { resolve } from 'path';
import { withAdsConnection } from './ads.database';
import { adProjection } from './ad-projection';
const file = [resolve(__dirname, '../../../client/src/data/merchant-demo.json'), resolve(__dirname, '../../client/src/data/merchant-demo.json')].find(existsSync);
const ledger = file ? JSON.parse(readFileSync(file, 'utf8')) : null;
const decode = (value: any) => typeof value === 'string' ? JSON.parse(value) : value;
export type BidSettings = { goal: 'clicks'|'orders'|'leads'; dailyBudget: number; days: number; maxCpc: number; unitProfit?: number };
const invalid = (message: string) => Object.assign(new Error(message), {status: 400});
export function validateSettings(value: any): BidSettings {
    if (!value || !['clicks','orders','leads'].includes(value.goal)) throw invalid('请选择推广目标');
    for (const [key,label,max] of [['dailyBudget','日预算',1000000],['days','投放天数',365],['maxCpc','最高点击出价',100]] as const) {
        if (typeof value[key] !== 'number' || !Number.isFinite(value[key]) || value[key] <= 0 || value[key] > max) throw invalid(`请填写有效的${label}`);
    }
    if (!Number.isInteger(value.days)) throw invalid('投放天数必须为整数');
    if (value.maxCpc < .01 || value.dailyBudget < .01) throw invalid('金额不能低于0.01元');
    if (value.unitProfit !== undefined && (typeof value.unitProfit !== 'number' || !Number.isFinite(value.unitProfit) || value.unitProfit <= 0)) throw invalid('单笔利润必须大于0');
    return {goal:value.goal,dailyBudget:value.dailyBudget,days:value.days,maxCpc:value.maxCpc,...(value.unitProfit !== undefined ? {unitProfit:value.unitProfit} : {})};
}
export function buildDeliveryContext(ad: any, range: '7'|'3' = '7') {
    const sample = ad && ledger?.ads.find((row: any) => row.id === ad.id);
    const performance = sample ? assistantPerformance({items:[ad], captured_at:new Date().toISOString(),selection:{limit:1,truncated:false}},range).items[0] : null;
    if (performance) performance.daily = sample.daily.filter((row:any) => (range==='7'?ledger.days:ledger.days.slice(-3)).includes(row.day)).map((row:any)=>({day:row.day,impressions:row.impressions,clicks:row.click_costs_minor.length,spend_minor:row.click_costs_minor.reduce((sum:number,cost:number)=>sum+cost,0)}));
    const videos = decode(ad?.videos || []), images = decode(ad?.images || []);
    return {status:sample ? 'launched' : 'new', billing:'CPC', currency:'CNY',
        ad_id:ad?.id || null, currentBid:ad && Number.isFinite(Number(ad.price)) ? Number(ad.price) : null,
        window:sample ? (range === '7' ? ledger.days : ledger.days.slice(-3)) : [],
        performance, material:{id:ad?.material_id || null,video_count:videos.length,image_count:images.length},
        defaults:{goal:'clicks',dailyBudget:sample ? sample.daily_budget_minor/100 : null,days:7,
            maxCpc:sample ? sample.max_cpc_minor/100 : null,
            unitProfit:sample ? (sample.unit_price_minor-sample.unit_cost_minor)/100 : null}};
}
export async function loadDeliveryContext(adId: unknown, range: unknown = '7', materialId?: unknown) {
    if (!['7','3'].includes(String(range))) throw invalid('统计周期无效');
    if (materialId !== undefined && (typeof materialId !== 'string' || materialId.length > 100)) throw invalid('素材编号无效');
    if ((adId === undefined || adId === null || adId === '') && !materialId) return buildDeliveryContext(null);
    if (adId !== undefined && adId !== null && adId !== '' && (typeof adId !== 'string' || adId.length > 100)) throw invalid('广告编号无效');
    return withAdsConnection(async c => {
        let current:any = null;
        if (adId) {
            const [rows]:any = await c.execute(adProjection+' WHERE a.id=?',[String(adId)]);
            if (!rows.length) throw Object.assign(new Error('广告不存在'),{status:404});
            current = rows[0];
        }
        const context = buildDeliveryContext(current,range as '7'|'3');
        if (materialId && materialId !== current?.material_id) {
            const [materials]:any = await c.execute('SELECT m.id,m.videos,COALESCE(i.images,JSON_ARRAY()) AS images FROM ads_business_materials m LEFT JOIN ads_business_material_images i ON i.material_id=m.id WHERE m.id=?',[String(materialId)]);
            if (!materials.length) throw Object.assign(new Error('素材不存在'),{status:404});
            context.material = {id:materialId,video_count:decode(materials[0].videos).length,image_count:decode(materials[0].images).length};
        }
        if (context.performance && materialId !== undefined && materialId !== current?.material_id) context.performance.material_changed = true;
        return context;
    });
}
