// All current-state readers resolve media and copy from the same immutable material.
// Ad-owned configuration and counters always come from ads_business_ads.
export const adProjection = `SELECT a.id,a.title,a.publisher,COALESCE(m.content,a.content) AS content,a.url,a.price,a.clicks,COALESCE(m.videos,a.videos) AS videos,a.version,a.ranking_score,l.material_id
FROM ads_business_ads a LEFT JOIN ads_business_material_links l ON l.ad_id=a.id LEFT JOIN ads_business_materials m ON m.id=l.material_id`;
