import adsModel from '../models/ads.model';
import { storedPrice } from './ad-value';
import { enabled } from '../recall/contracts';
import config from '../config';
import { Ad, AdInput, ServiceResult } from '../types';

function calculateScore(ad: Ad): number {
    const price = parseFloat(String(ad.price)) || 0;
    const clicks = parseInt(String(ad.clicks)) || 0;
    return price + (price * clicks * config.AD_SCORE_FACTOR);
}

async function getSortedAds(): Promise<Ad[]> {
    return adsModel.getAllAds();
}

export function validateAd(data: AdInput): string | undefined {
    if (!enabled() && data && (data.attributes !== undefined || data.eligibility !== undefined)) return '广告属性召回未启用';
    if (!data || ['title', 'publisher', 'content', 'url'].some(key => typeof data[key] !== 'string' || !data[key].trim())) return '广告字段不完整';
    if (Object.keys(data).some(key => !['title', 'publisher', 'content', 'url', 'price', 'videos', 'version', 'attributes', 'eligibility'].includes(key))) return '不支持的广告字段（当前不支持预算变更）';
    const price = Number(data.price);
    const ceiling = Number(process.env.MAX_AD_BID || 100);
    if (!Number.isFinite(ceiling) || ceiling <= 0) return '服务出价上限配置错误';
    if (!['number', 'string'].includes(typeof data.price) || String(data.price).trim() === '' || !Number.isFinite(price) || price <= 0 || price > ceiling) return `出价必须大于 0 且不超过 ${ceiling}`;
    if (data.title.length > 500 || data.publisher.length > 255 || Buffer.byteLength(data.content) > 1024 * 1024 || Buffer.byteLength(data.url) > 65535) return '广告字段过长';
    if (!storedPrice(data.price)) return '出价最多支持 8 位小数';
    try { if (!['http:', 'https:'].includes(new URL(data.url).protocol)) return '广告链接必须使用 HTTP 或 HTTPS'; } catch { return '广告链接无效'; }
    if (data.videos !== undefined && (!Array.isArray(data.videos) || data.videos.some(v => typeof v !== 'string'))) return '视频列表无效';
}

async function createAd(data: AdInput): Promise<ServiceResult<Ad>> {
    const error = validateAd(data);
    if (error) return { success: false, error };
    const { title, publisher, content, url, price } = data;
    if (!title || !publisher || !content || !url || price === undefined) {
        return {
            success: false,
            error: 'Missing required fields'
        };
    }
    const newAd = await adsModel.create(data);
    return {
        success: true,
        data: newAd
    };
}

async function updateAd(id: string, data: AdInput): Promise<ServiceResult<Ad>> {
    const error = validateAd(data);
    if (error) return { success: false, error };
    if (!Number.isInteger(data.version) || data.version < 1 || data.version > 4294967294) return { success: false, error: '修改广告必须提供有效的 version，请刷新列表后重新编辑' };
    const updatedAd = await adsModel.update(id, data);
    if (!updatedAd) {
        return {
            success: false,
            error: 'Ad not found'
        };
    }
    return {
        success: true,
        data: updatedAd
    };
}

async function deleteAd(id: string): Promise<ServiceResult> {
    const deleted = await adsModel.remove(id);
    if (!deleted) {
        return {
            success: false,
            error: 'Ad not found'
        };
    }
    return { success: true };
}

async function clickAd(id: string): Promise<ServiceResult> {
    const clicks = await adsModel.incrementClicks(id,true);
    if (clicks === null) {
        return {
            success: false,
            error: 'Ad not found'
        };
    }
    return {
        success: true,
        ...clicks
    };
}

export default {
    getSortedAds,
    createAd,
    updateAd,
    deleteAd,
    clickAd,
    calculateScore
};
