import Router from 'koa-router';
import config from '../config';
import { mutate } from '../middlewares/businessBoundary';
import { requireContract } from '../services/marketing/contracts';
import { requireMarketingSchema } from '../services/marketing/migrations';
import * as resources from '../services/marketing/resource.service';
import * as delivery from '../services/marketing/delivery.service';
import { conversion } from '../services/marketing/conversion.service';
import { spendEntry, spendCoverage } from '../services/marketing/spend.service';
import { authenticateSource, visitor } from '../services/marketing/security';
import { createReport, getReport, parseReportQuery } from '../services/marketing/report.service';
import { estimateMetrics } from '../services/marketing/metrics';

const router = new Router({ prefix: '/api/marketing' });
router.use(async (ctx, next) => {
    try {
        if (!config.MARKETING.features.MARKETING_EVENTS_ENABLED) throw resources.resourceError('feature_disabled', 404, '营销资源入口未启用');
        const consumer = ctx.path === '/api/marketing/serve' || ctx.path === '/api/marketing/events/impressions' || ctx.path.startsWith('/api/marketing/click/') || ctx.path.startsWith('/api/marketing/media/');
        const service = ['/api/marketing/conversions', '/api/marketing/spend-entries', '/api/marketing/spend-coverage'].includes(ctx.path);
        if (!consumer && !service && !ctx.state.principal) throw resources.resourceError('authentication_required', 401, '请提供有效的运营凭据');
        if (service) ctx.state.marketingSource = authenticateSource(ctx, ctx.path.split('/').pop()!);
        ctx.set('Cache-Control', 'no-store');
        ctx.set('Referrer-Policy', 'no-referrer');
        await requireMarketingSchema(ctx.path.startsWith('/api/marketing/reports') ? 13 : consumer || service ? 12 : 10);
        await next();
    } catch (e) {
        const error = e as any;
        ctx.status = error.status || 503;
        ctx.body = { error: { code: error.code || (ctx.status === 409 ? 'idempotency_conflict' : 'marketing_unavailable'), message: ctx.status === 503 ? '营销依赖暂不可用，请检查配置、迁移与数据库' : error.message, request_id: ctx.state.requestId } };
    }
});

router.get('/reports', async ctx => {
    const query = parseReportQuery(ctx.query, config.MARKETING.environment);
    ctx.body = await createReport(ctx.state.principal, query);
});
router.get('/reports/:id', async ctx => {
    requireContract('Id', ctx.params.id);
    ctx.body = await getReport(ctx.state.principal, config.MARKETING.environment, ctx.params.id);
});
router.post('/metrics/estimates', async ctx => {
    requireContract('MetricEstimateInput', ctx.request.body);
    ctx.body = estimateMetrics(ctx.request.body);
});

router.post('/serve', async ctx => {
    requireContract('ServeInput', ctx.request.body);
    ctx.body = await delivery.serve(ctx.request.body.campaign_id, config.MARKETING.environment, visitor(ctx, config.MARKETING.environment, true));
});
router.post('/events/impressions', async ctx => {
    requireContract('ImpressionInput', ctx.request.body);
    const result = await delivery.impression(config.MARKETING.environment, visitor(ctx, config.MARKETING.environment), ctx.request.body);
    ctx.status = result.status; ctx.body = result.body;
});
router.get('/click/:token', async ctx => {
    const result = await delivery.click(config.MARKETING.environment, visitor(ctx, config.MARKETING.environment), ctx.params.token);
    ctx.redirect(result.body.redirect_url);
    ctx.status = result.status;
});
router.get('/media/:token', async ctx => {
    const reference = await delivery.mediaReference(config.MARKETING.environment, visitor(ctx, config.MARKETING.environment), ctx.params.token);
    // koa-send confines the relative path to the upload root.
    try { await require('koa-send')(ctx, reference, { root: config.UPLOAD_DIR }); }
    catch (e) {
        if ((e as any).status === 404) throw resources.resourceError('media_not_found', 404, '素材文件不存在');
        throw resources.resourceError('media_unavailable', 503, '素材暂不可用');
    }
});
for (const [endpoint, contract, handler] of [
    ['conversions', 'ConversionInput', conversion],
    ['spend-entries', 'SourceSpendInput', spendEntry],
    ['spend-coverage', 'SourceCoverageInput', spendCoverage]
] as const) router.post('/' + endpoint, async ctx => {
    requireContract(contract, ctx.request.body);
    const result = await handler(ctx.state.marketingSource, ctx.request.body);
    ctx.status = result.status; ctx.body = result.body;
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
