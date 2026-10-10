export type MarketingEnvironment = 'synthetic' | 'sandbox' | 'production';
export interface CreateCampaign {
    brand_id: string; product_id: string; name: string; objective: 'traffic' | 'conversion';
    starts_at: string; ends_at: string;
}
export interface CreateCreative {
    brand_id: string; campaign_id: string; ad_id: string; title: string;
    asset_ref: string; asset_hash: string; landing_url: string;
    authorization_ref: string; authorization_expires_at: string;
}
export interface CampaignStatus { expected_version: number; status: 'active' | 'paused' | 'ended' }
export interface CreativeStatus { expected_revision: number; status: 'pending_review' | 'approved' | 'rejected' | 'archived' }
export interface MarketingError { error: { code: string; message: string; request_id: string } }
