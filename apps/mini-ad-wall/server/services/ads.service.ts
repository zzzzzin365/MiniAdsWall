import adsModel from '../models/ads.model';
import config from '../config';
import { Ad, AdInput, ServiceResult } from '../types';

function calculateScore(ad: Ad): number {
    const price = parseFloat(String(ad.price)) || 0;
    const clicks = parseInt(String(ad.clicks)) || 0;
    return price + (price * clicks * config.AD_SCORE_FACTOR);
}

function getSortedAds(): Ad[] {
    const ads = adsModel.getAllAds();
    return ads.sort((a, b) => calculateScore(b) - calculateScore(a));
}

function validateAd(data: AdInput): string | undefined {
    if (!data || ['title', 'publisher', 'content', 'url'].some(key => typeof data[key] !== 'string' || !data[key].trim())) return '广告字段不完整';
    if (Object.keys(data).some(key => !['title', 'publisher', 'content', 'url', 'price', 'videos'].includes(key))) return '不支持的广告字段（当前不支持预算变更）';
    const price = Number(data.price);
    const ceiling = Number(process.env.MAX_AD_BID || 100);
    if (!Number.isFinite(ceiling) || ceiling <= 0) return '服务出价上限配置错误';
    if (!['number', 'string'].includes(typeof data.price) || String(data.price).trim() === '' || !Number.isFinite(price) || price <= 0 || price > ceiling) return `出价必须大于 0 且不超过 ${ceiling}`;
    try { if (!['http:', 'https:'].includes(new URL(data.url).protocol)) return '广告链接必须使用 HTTP 或 HTTPS'; } catch { return '广告链接无效'; }
    if (data.videos !== undefined && (!Array.isArray(data.videos) || data.videos.some(v => typeof v !== 'string'))) return '视频列表无效';
}

function createAd(data: AdInput): ServiceResult<Ad> {
    const error = validateAd(data);
    if (error) return { success: false, error };
    const { title, publisher, content, url, price } = data;
    if (!title || !publisher || !content || !url || price === undefined) {
        return {
            success: false,
            error: 'Missing required fields'
        };
    }
    const newAd = adsModel.create(data);
    return {
        success: true,
        data: newAd
    };
}

function updateAd(id: string, data: AdInput): ServiceResult<Ad> {
    const error = validateAd(data);
    if (error) return { success: false, error };
    const updatedAd = adsModel.update(id, data);
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

function deleteAd(id: string): ServiceResult {
    const deleted = adsModel.remove(id);
    if (!deleted) {
        return {
            success: false,
            error: 'Ad not found'
        };
    }
    return { success: true };
}

function clickAd(id: string): ServiceResult {
    const clicks = adsModel.incrementClicks(id);
    if (clicks === null) {
        return {
            success: false,
            error: 'Ad not found'
        };
    }
    return {
        success: true,
        clicks
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
