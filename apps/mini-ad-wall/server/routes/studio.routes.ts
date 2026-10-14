import Router from "koa-router";
import multer from "@koa/multer";
import fs from "fs/promises";
import path from "path";
import { RowDataPacket } from "mysql2/promise";
import config from "../config";
import { mutate } from "../middlewares/businessBoundary";
import {
  currentAdsConnection,
  recordOperationChange,
} from "../models/ads.model";
import { withAdsConnection } from "../services/ads.database";
import * as store from "../services/merchant/studio/store";
import { studioSchema } from "../services/merchant/studio/migration";
import { requireInput } from "../services/merchant/studio/contracts";
import {
  storeUpload,
  assetPath,
  verifyAsset,
  studioDir,
} from "../services/merchant/studio/assets";
import { resourceError as fail } from "../services/marketing/resource.service";
import { studioRequest } from '../services/merchant/studio/worker';
const router = new Router({ prefix: "/api/merchant/v1/studio" });
const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 100 * 1024 * 1024, files: 1, fields: 2 },
});
const env = () => config.MARKETING.environment;
let knowledgeRoot = __dirname;
while (!require('fs').existsSync(path.join(knowledgeRoot, 'mcp/external_marketing.py'))) {
  const parent = path.dirname(knowledgeRoot);
  if (parent === knowledgeRoot) throw Error('knowledge_root_missing');
  knowledgeRoot = parent;
}
router.use(async (ctx, next) => {
  try {
    const publicAsset = ctx.path.includes("/public-assets/");
    if (!publicAsset && !ctx.state.principal)
      throw fail("authentication_required", 401, "请提供运营凭据");
    const read = ["GET", "HEAD"].includes(ctx.method);
    if (!read && process.env.MERCHANT_WORKSPACE_ENABLED !== "true")
      throw fail("studio_disabled", 503, "工作台未启用");
    if (ctx.path.endsWith("/capabilities")) {
      await next();
      return;
    }
    await studioSchema();
    ctx.set("Cache-Control", "no-store");
    await next();
  } catch (e) {
    const r = e as any;
    ctx.status = r.status || 503;
    ctx.body = {
      error: {
        code: r.code || "studio_unavailable",
        message: r.status ? r.message : "工作台依赖暂不可用",
        request_id: ctx.state.requestId,
      },
    };
  }
});
router.get("/capabilities", (ctx) => {
  ctx.body = {
    enabled: process.env.MERCHANT_WORKSPACE_ENABLED === "true",
    video: process.env.STUDIO_VIDEO_ENABLED === "true",
    image: true,
    visual_understanding: true,
    multimodal_rag: "caption-vector-v1",
      understanding_model: process.env.STUDIO_UNDERSTANDING_MODEL||"doubao-seed-2-0-mini-260428",
    text_model: "deepseek-v4-1-flash-260910",
    video_model: "doubao-seedance-1-0-pro-fast-251015",
    policy: "seed-understanding-v2",
  };
});
router.get("/brands", async (ctx) => {
  ctx.body = await withAdsConnection(async (c) => {
    const [r] = await c.execute<RowDataPacket[]>(
      "SELECT DISTINCT b.id,b.name FROM marketing_brands b JOIN marketing_memberships m ON m.brand_id=b.id WHERE b.environment=? AND m.principal=? AND m.role='operator' ORDER BY b.id LIMIT 100",
      [env(), ctx.state.principal],
    );
    return r;
  });
});
router.get('/reference-images/:sourceId/:label', async ctx => {
  const catalog = JSON.parse(await fs.readFile(path.join(knowledgeRoot, 'data/external-marketing/catalog.json'), 'utf8'));
  const source = catalog.sources.find((s: any) => s.id === ctx.params.sourceId);
  const image = source?.images.find((i: any) => i.label === ctx.params.label);
  if (!image) throw fail('not_found', 404, '参考图片不存在');
  const base = path.join(knowledgeRoot, 'data/external-marketing/images');
  const file = path.resolve(knowledgeRoot, image.local_path);
  if (!file.startsWith(base + path.sep) || !/\.(png|jpg|webp)$/.test(file))
    throw fail('invalid_reference_image', 422, '参考图片路径不合法');
  ctx.type = path.extname(file);
  ctx.body = await fs.readFile(file);
});
router.get('/reference-search', async ctx => {
  const query = ctx.query.q;
  if (typeof query !== 'string' || !query.trim() || query.length > 2000)
    throw fail('invalid_query', 422, '请输入不超过2000字的检索问题');
  ctx.body = await studioRequest('/studio/reference-search', {query:query.trim()});
});
router.get("/sessions", async (ctx) => {
  ctx.body = await withAdsConnection(async (c) => {
    const [r] = await c.execute<RowDataPacket[]>(
      `SELECT o.id,o.revision,o.created_at FROM merchant_studio_objects o JOIN marketing_memberships m ON m.brand_id=o.brand_id AND m.principal=? WHERE o.environment=? AND o.kind='session' AND o.revision=(SELECT MAX(r.revision) FROM merchant_studio_objects r WHERE r.id=o.id) GROUP BY o.id,o.revision,o.created_at ORDER BY o.created_at DESC LIMIT 20`,
      [ctx.state.principal, env()],
    );
    return r;
  });
});
router.post(
  "/assets",
  async (ctx, next) => {
    const brand = ctx.query.brand_id;
    if (typeof brand !== "string" || !/^[\w-]{1,100}$/.test(brand))
      throw fail("invalid_brand", 422, "品牌无效");
    await withAdsConnection((c) =>
      store.authorize(c, ctx.state.principal, brand, env(), true),
    );
    await next();
  },
  upload.single("file") as any,
  async (ctx) => {
    const file = (ctx.request as any).file || (ctx as any).file;
    if (!file) throw fail("missing_asset", 422, "请选择附件");
    const data = await storeUpload(file.buffer, file.mimetype);
    ctx.request.body = {
      brand_id: String(ctx.query.brand_id),
      asset_hash: data.hash,
    };
    try {
      await mutate(ctx, async () => {
        const c = currentAdsConnection();
        await store.authorize(
          c,
          ctx.state.principal,
          String(ctx.query.brand_id),
          env(),
          true,
        );
        const result = await store.put(
          c,
          ctx.state.principal,
          String(ctx.query.brand_id),
          env(),
          "asset",
          data,
        );
        return { status: 201, body: result };
      });
    } finally {
      if ((ctx.body as any)?.data?.filename !== data.filename)
        await fs.rm(path.join(studioDir(), data.filename), { force: true });
    }
  },
);
router.post("/assets/:id/analyze", async (ctx) => {
  await mutate(ctx, async () => {
    const c = currentAdsConnection();
    const asset = await store.object(c, ctx.params.id, "asset", env(), true);
    await store.authorize(c, ctx.state.principal, asset.brand_id, env(), true);
    if (asset.data.analysis_job_id) return {status:200, body:asset};
    const job = await store.queue(c, ctx.state.principal, {id:asset.id,brand_id:asset.brand_id,environment:env()}, "text", {kind:"asset_analysis",asset_id:asset.id,model:process.env.STUDIO_UNDERSTANDING_MODEL||"doubao-seed-2-0-mini-260428"});
    const updated = await store.revise(c, ctx.state.principal, asset, {...asset.data,analysis_job_id:job.id});
    return {status:202,body:updated};
  });
});
router.get("/assets/:id/content", async (ctx) => {
  const a = await withAdsConnection(async (c) => {
    const a = await assetPath(c, ctx.params.id, env());
    await store.authorize(c, ctx.state.principal, a.row.brand_id, env());
    return a;
  });
  ctx.type = a.row.data.mime;
  ctx.body = await fs.readFile(a.path);
});
router.get("/public-assets/:token", async (ctx) => {
  const claims = verifyAsset(ctx.params.token);
  const a = await withAdsConnection(async (c) => {
    const a = await assetPath(c, claims.id, claims.environment);
    await store.authorize(
      c,
      a.row.created_by,
      a.row.brand_id,
      claims.environment,
    );
    return a;
  });
  ctx.type = a.row.data.mime;
  ctx.body = await fs.readFile(a.path);
  ctx.set("Referrer-Policy", "no-referrer");
});
function write(
  path: string,
  schema: string,
  status: number,
  handler: (ctx: any) => Promise<any>,
) {
  router.post(path, async (ctx) => {
    requireInput(schema, ctx.request.body);
    await mutate(ctx, async () => ({ status, body: await handler(ctx) }));
  });
}
write("/briefs", "Brief", 201, (ctx) => {
  const b = ctx.request.body;
  if (!b.subject_description.trim() && !b.idea.trim() && !b.asset_refs.length)
    throw fail("empty_brief", 422, "请填写营销主体、idea或上传附件");
  return store.createBrief(ctx.state.principal, env(), b);
});
write("/briefs/:id/revisions", "Revision", 201, async (ctx) => {
  const c = currentAdsConnection(),
    row = await store.object(c, ctx.params.id, "brief", env(), true);
  await store.authorize(c, ctx.state.principal, row.brand_id, env(), true);
  store.checkRevision(row, ctx.request.body.expected_revision);
  if (ctx.request.body.brief.brand_id !== row.brand_id)
    throw fail("scope_mismatch", 403, "不能更换品牌");
  for (const ref of ctx.request.body.brief.asset_refs) {
    const a = await store.object(c, ref.asset_id, "asset", env());
    if (a.brand_id !== row.brand_id)
      throw fail("scope_mismatch", 403, "附件越权");
  }
  return store.revise(c, ctx.state.principal, row, ctx.request.body.brief);
});
router.post('/recipes/:id/image', async ctx => {
  await mutate(ctx, async () => {
    const c = currentAdsConnection();
    const r = await store.object(c, ctx.params.id, 'recipe', env(), true);
    await store.authorize(c, ctx.state.principal, r.brand_id, env(), true);
    if (!['accept','modify'].includes(r.data.decision)) throw fail('recipe_not_approved',422,'请先确认创意方案');
    const [pending]:any = await c.execute("SELECT id FROM merchant_studio_jobs WHERE session_id=? AND JSON_UNQUOTE(JSON_EXTRACT(input,'$.kind'))='image_generation' AND status IN ('queued','running','submission_unknown') LIMIT 1",[r.data.session_id]);
    if(pending.length) throw fail('image_pending',409,'图片任务正在处理或待核查，请勿重复生成');
    const session = await store.object(c,r.data.session_id,'session',env(),true);
    const job = await store.queue(c,ctx.state.principal,session,'text',{kind:'image_generation',recipe_id:r.id,recipe_revision:r.revision,prompt:'生成广告图片素材，采用静态画面，不要视频分镜。场景：'+r.data.concept.scenario+'。广告文案：'+r.data.concept.copy+'。创意：'+r.data.concept.video_prompt});
    return {status:202,body:job};
  });
});
write("/sessions", "Session", 201, (ctx) =>
  store.createSession(ctx.state.principal, env(), ctx.request.body),
);
write("/sessions/:id/turns", "Turn", 202, (ctx) =>
  store.turn(ctx.state.principal, env(), ctx.params.id, ctx.request.body),
);
router.get("/sessions/:id", async (ctx) => {
  ctx.body = await store.readSession(ctx.state.principal, env(), ctx.params.id);
});
write("/recipes/:id/decisions", "Decision", 200, (ctx) => {
  if (ctx.request.body.decision === "modify" && !ctx.request.body.concept)
    throw fail("concept_required", 422, "修改需要完整方案");
  return store.decide(
    ctx.state.principal,
    env(),
    ctx.params.id,
    ctx.request.body,
  );
});
write("/recipes/:id/jobs", "Video", 202, (ctx) =>
  store.videoJob(ctx.state.principal, env(), ctx.params.id, ctx.request.body),
);
write("/recipes/:id/save-draft", "Draft", 201, async (ctx) => {
  const c = currentAdsConnection(),
    r = await store.object(c, ctx.params.id, "recipe", env(), true);
  await store.authorize(c, ctx.state.principal, r.brand_id, env(), true);
  store.checkRevision(r, ctx.request.body.expected_revision);
  if (!["accept", "modify"].includes(r.data.decision))
    throw fail("review_required", 422, "请先确认文案与事实");
  const result = await store.put(
    c,
    ctx.state.principal,
    r.brand_id,
    env(),
    "draft",
    {
      title: ctx.request.body.title,
      recipe_id: r.id,
      recipe_revision: r.revision,
      concept: r.data.concept,
      status: "unpublished",
    },
  );
  recordOperationChange(null, result);
  return result;
});
router.get("/recipes/:id", async (ctx) => {
  ctx.body = await withAdsConnection(async (c) => {
    const r = await store.object(c, ctx.params.id, "recipe", env());
    await store.authorize(c, ctx.state.principal, r.brand_id, env());
    return r;
  });
});
router.get("/jobs/:id", async (ctx) => {
  ctx.body = await store.readJob(ctx.state.principal, env(), ctx.params.id);
});
write("/jobs/:id/cancel", "Cancel", 200, async (ctx) => {
  const c = currentAdsConnection();
  const [r] = await c.execute<RowDataPacket[]>(
    "SELECT * FROM merchant_studio_jobs WHERE id=? AND environment=? FOR UPDATE",
    [ctx.params.id, env()],
  );
  if (!r.length) throw fail("not_found", 404, "任务不存在");
  await store.authorize(c, ctx.state.principal, r[0].brand_id, env(), true);
  if (r[0].status !== "queued")
    throw fail(
      "cancel_unavailable",
      409,
      "任务已提交，需查单；不能假称供应商取消",
    );
  await c.execute(
    "UPDATE merchant_studio_jobs SET status='cancelled',updated_at=UTC_TIMESTAMP(3) WHERE id=?",
    [r[0].id],
  );
  recordOperationChange(r[0], { status: "cancelled" });
  return { status: "cancelled" };
});
write("/outputs/:id/review", "Review", 200, async (ctx) => {
  const c = currentAdsConnection(),
    r = await store.object(c, ctx.params.id, "output", env(), true);
  await store.authorize(c, ctx.state.principal, r.brand_id, env(), true);
  store.checkRevision(r, ctx.request.body.expected_revision);
  return store.revise(c, ctx.state.principal, r, {
    ...r.data,
    review: ctx.request.body.decision,
    reason: ctx.request.body.reason,
    reviewed_by: ctx.state.principal,
  });
});
write("/outputs/:id/save-draft", "Draft", 201, async (ctx) => {
  const c = currentAdsConnection(),
    r = await store.object(c, ctx.params.id, "output", env(), true);
  await store.authorize(c, ctx.state.principal, r.brand_id, env(), true);
  store.checkRevision(r, ctx.request.body.expected_revision);
  if (r.data.review !== "approve")
    throw fail("review_required", 422, "请先审核素材");
  const d = await store.put(
    c,
    ctx.state.principal,
    r.brand_id,
    env(),
    "draft",
    {
      title: ctx.request.body.title,
      output_id: r.id,
      output_revision: r.revision,
      asset_id: r.data.asset_id,
      status: "unpublished",
    },
  );
  recordOperationChange(null, d);
  return d;
});
write("/jobs/:id/retry", "Retry", 200, async (ctx) => {
  const c = currentAdsConnection();
  const [rows] = await c.execute<RowDataPacket[]>(
    "SELECT * FROM merchant_studio_jobs WHERE id=? AND environment=? FOR UPDATE",
    [ctx.params.id, env()],
  );
  if (!rows.length) throw fail("not_found", 404, "任务不存在");
  const r = rows[0];
  await store.authorize(c, ctx.state.principal, r.brand_id, env(), true);
  if (r.status !== "failed")
    throw fail(
      "retry_unavailable",
      409,
      "仅已明确失败任务可恢复；未知提交需人工核查",
    );
  if (r.kind === "video" && !r.provider_task_id)
    throw fail(
      "retry_unavailable",
      409,
      "没有已确认的供应商任务，需重新确认方案",
    );
  const next = r.kind === "video" ? "submitted" : "queued";
  const input = store.decode(r.input);
  if (input.kind === "asset_analysis") {
    input.model = process.env.STUDIO_UNDERSTANDING_MODEL || "doubao-seed-2-0-mini-260428";
    await c.execute("UPDATE merchant_studio_jobs SET input=? WHERE id=?", [JSON.stringify(input), r.id]);
  }
  await c.execute(
    "UPDATE merchant_studio_jobs SET status=?,lease_token=NULL,lease_until=NULL,attempts=0,error_code=NULL,updated_at=UTC_TIMESTAMP(3) WHERE id=?",
    [next, r.id],
  );
  recordOperationChange(r, { status: next });
  return { id: r.id, status: next };
});
router.get("/outputs/:id", async (ctx) => {
  ctx.body = await withAdsConnection(async (c) => {
    const r = await store.object(c, ctx.params.id, "output", env());
    await store.authorize(c, ctx.state.principal, r.brand_id, env());
    return r;
  });
});
export default router;
