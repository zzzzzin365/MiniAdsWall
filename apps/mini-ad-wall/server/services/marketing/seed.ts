import { RowDataPacket } from 'mysql2/promise';
import { withAdsConnection } from '../ads.database';
import { requireContract } from './contracts';
import { requireMarketingSchema } from './migrations';
import { resourceError } from './resource.service';

export type SeedScope = { brand_id: string; product_id: string; principal: string; name: string; environment: 'synthetic' | 'sandbox'; ad_ids: string[] };
export async function seedMarketingScope(input: SeedScope) {
    for (const value of [input.brand_id, input.product_id, ...input.ad_ids]) requireContract('Id', value);
    if (!['synthetic', 'sandbox'].includes(input.environment)) throw resourceError('unsafe_seed_environment', 400, '演示种子禁止写 production');
    if (!input.principal || input.principal.length > 190 || !input.name?.trim() || input.name.length > 500 || input.ad_ids.length > 100) throw resourceError('invalid_seed', 400, '种子参数无效');
    await requireMarketingSchema();
    return withAdsConnection(async c => {
        await c.beginTransaction();
        try {
            await c.execute('INSERT IGNORE INTO marketing_brands(id,name,environment) VALUES (?,?,?)', [input.brand_id, input.name, input.environment]);
            const [brands] = await c.execute<RowDataPacket[]>('SELECT * FROM marketing_brands WHERE id=? FOR UPDATE', [input.brand_id]);
            if (brands[0].environment !== input.environment || brands[0].name !== input.name) throw resourceError('seed_scope_conflict', 409, '已有品牌与种子不一致');
            await c.execute('INSERT IGNORE INTO marketing_products(id,brand_id,environment,name) VALUES (?,?,?,?)', [input.product_id, input.brand_id, input.environment, input.name + ' 商品']);
            const [products] = await c.execute<RowDataPacket[]>('SELECT * FROM marketing_products WHERE id=?', [input.product_id]);
            if (products[0].brand_id !== input.brand_id || products[0].environment !== input.environment) throw resourceError('seed_scope_conflict', 409, '已有商品与种子不一致');
            await c.execute("INSERT IGNORE INTO marketing_memberships(principal,brand_id,role) VALUES (?,?,'operator')", [input.principal, input.brand_id]);
            for (const adId of [...new Set(input.ad_ids)].sort()) {
                const [ads] = await c.execute<RowDataPacket[]>('SELECT id FROM ads_business_ads WHERE id=? FOR UPDATE', [adId]);
                if (!ads.length) throw resourceError('ad_not_found', 404, '指定旧广告不存在');
                await c.execute('INSERT IGNORE INTO marketing_ad_owners(ad_id,brand_id,environment) VALUES (?,?,?)', [adId, input.brand_id, input.environment]);
                const [owners] = await c.execute<RowDataPacket[]>('SELECT * FROM marketing_ad_owners WHERE ad_id=?', [adId]);
                if (owners[0].brand_id !== input.brand_id || owners[0].environment !== input.environment) throw resourceError('seed_scope_conflict', 409, '旧广告已归属其他品牌');
            }
            await c.commit();
            return { brand_id: input.brand_id, product_id: input.product_id, environment: input.environment, ad_ids: input.ad_ids };
        } catch (e) { await c.rollback(); throw e; }
    });
}
