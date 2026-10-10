import Router from 'koa-router';
import config from '../config';
import { mutate } from '../middlewares/businessBoundary';
import { requireContract } from '../services/marketing/contracts';
import { requireMarketingSchema } from '../services/marketing/migrations';
import * as resources from '../services/marketing/resource.service';

const router = new Router({ prefix: '/api/marketing' });
router.use(async (ctx, next) => {
    try {
        if (!config.MARKETING.features.MARKETING_EVENTS_ENABLED) throw resources.resourceError('feature_disabled', 404, '营销资源入口未启用');
        if (!ctx.state.principal) throw resources.resourceError('authentication_required', 401, '请提供有效的运营凭据');
        await requireMarketingSchema();
        await next();
    } catch (e) {
        const error = e as any;
        ctx.status = error.status || 503;
        ctx.body = { error: { code: error.code || (ctx.status === 409 ? 'idempotency_conflict' : 'marketing_unavailable'), message: ctx.status === 503 ? '营销存储暂不可用，请检查迁移与数据库' : error.message, request_id: ctx.state.requestId } };
    }
});

for (const kind of ['campaigns', 'creatives'] as const) {
    router.get(`/${kind}`, async ctx => {
        requireContract('Id', ctx.query.brand_id);
        if (ctx.query.cursor !== undefined) requireContract('Id', ctx.query.cursor);
        const rawLimit = ctx.query.limit || '50';
        if (typeof rawLimit !== 'string' || !/^\d+$/.test(rawLimit) || Number(rawLimit) < 1 || Number(rawLimit) > 100) throw resources.resourceError('invalid_limit', 400, '分页条数必须为 1–100');
        ctx.body = await resources.listResources(ctx.state.principal, config.MARKETING.environment, kind, ctx.query.brand_id as string, ctx.query.cursor as string, Number(rawLimit));
    });
    router.get(`/${kind}/:id`, async ctx => {
        requireContract('Id', ctx.params.id);
        ctx.body = await resources.getResource(ctx.state.principal, config.MARKETING.environment, kind, ctx.params.id);
    });
    router.post(`/${kind}`, async ctx => {
        requireContract(kind === 'campaigns' ? 'CreateCampaign' : 'CreateCreative', ctx.request.body);
        await mutate(ctx, async () => ({ status: 201, body: await (kind === 'campaigns' ? resources.createCampaign : resources.createCreative)(ctx.state.principal, config.MARKETING.environment, ctx.request.body) }));
    });
    router.post(`/${kind}/:id/status`, async ctx => {
        requireContract('Id', ctx.params.id);
        requireContract(kind === 'campaigns' ? 'CampaignStatus' : 'CreativeStatus', ctx.request.body);
        await mutate(ctx, async () => ({ status: 200, body: await (kind === 'campaigns' ? resources.changeCampaignStatus : resources.changeCreativeStatus)(ctx.state.principal, config.MARKETING.environment, ctx.params.id, ctx.request.body) }));
    });
}
export default router;
