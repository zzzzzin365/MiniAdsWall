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

function createAd(data: AdInput): ServiceResult<Ad> {
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
