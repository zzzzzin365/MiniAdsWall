import { randomUUID, createHash } from "crypto";
import { PoolConnection, RowDataPacket } from "mysql2/promise";
import {
  currentAdsConnection,
  recordOperationChange,
} from "../../../models/ads.model";
import { resourceError as fail } from "../../marketing/resource.service";
import { withAdsConnection } from "../../ads.database";
export const decode = (x: any) => (typeof x === "string" ? JSON.parse(x) : x);
export const hash = (x: any) =>
  createHash("sha256").update(JSON.stringify(x)).digest("hex");
export async function authorize(
  c: PoolConnection,
  principal: string,
  brand: string,
  environment: string,
  write = false,
) {
  const [rows] = await c.execute<RowDataPacket[]>(
    "SELECT m.role FROM marketing_memberships m JOIN marketing_brands b ON b.id=m.brand_id WHERE b.id=? AND b.environment=? AND m.principal=?",
    [brand, environment, principal],
  );
  if (!rows.length || (write && !rows.some((r) => r.role === "operator")))
    throw fail("resource_forbidden", 403, "没有该品牌操作权限");
}
export async function object(
  c: PoolConnection,
  id: string,
  kind: string,
  environment: string,
  lock = false,
) {
  const [rows] = await c.execute<RowDataPacket[]>(
    `SELECT * FROM merchant_studio_objects WHERE id=? AND kind=? AND environment=? ORDER BY revision DESC LIMIT 1${lock ? " FOR UPDATE" : ""}`,
    [id, kind, environment],
  );
  if (!rows.length) throw fail("not_found", 404, "工作台资源不存在");
  return { ...rows[0], data: decode(rows[0].data) } as any;
}
export async function put(
  c: PoolConnection,
  principal: string,
  brand: string,
  environment: string,
  kind: string,
  data: any,
  id = randomUUID(),
  revision = 1,
) {
  await c.execute(
    "INSERT INTO merchant_studio_objects(id,revision,kind,brand_id,environment,created_by,data,created_at) VALUES (?,?,?,?,?,?,?,UTC_TIMESTAMP(3))",
    [id, revision, kind, brand, environment, principal, JSON.stringify(data)],
  );
  return { id, revision, kind, brand_id: brand, environment, data };
}
export function checkRevision(row: any, expected: number) {
  if (row.revision !== expected)
    throw fail("revision_conflict", 409, "版本已变化，请刷新");
}
export async function revise(
  c: PoolConnection,
  principal: string,
  row: any,
  data: any,
) {
  const next = await put(
    c,
    principal,
    row.brand_id,
    row.environment,
    row.kind,
    data,
    row.id,
    row.revision + 1,
  );
  recordOperationChange(row, next);
  return next;
}
export async function queue(
  c: PoolConnection,
  principal: string,
  session: any,
  kind: "text" | "video",
  input: any,
) {
  await c.execute("SELECT id FROM marketing_brands WHERE id=? FOR UPDATE", [
    session.brand_id,
  ]);
  const [active] = await c.execute<RowDataPacket[]>(
    "SELECT COUNT(*) AS n FROM merchant_studio_jobs WHERE brand_id=? AND environment=? AND status IN ('queued','running','submitted','submission_unknown')",
    [session.brand_id, session.environment],
  );
  if (Number(active[0].n) >= 20)
    throw fail("quota_exceeded", 429, "品牌待处理任务已满");
  const id = randomUUID();
  await c.execute(
    "INSERT INTO merchant_studio_jobs(id,brand_id,environment,created_by,session_id,kind,status,input,input_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,'queued',?,?,UTC_TIMESTAMP(3),UTC_TIMESTAMP(3))",
    [
      id,
      session.brand_id,
      session.environment,
      principal,
      session.id,
      kind,
      JSON.stringify(input),
      hash(input),
    ],
  );
  return { id, status: "queued", kind };
}
export async function readJob(
  principal: string,
  environment: string,
  id: string,
) {
  return withAdsConnection(async (c) => {
    const [rows] = await c.execute<RowDataPacket[]>(
      "SELECT * FROM merchant_studio_jobs WHERE id=? AND environment=?",
      [id, environment],
    );
    if (!rows.length) throw fail("not_found", 404, "任务不存在");
    const r = rows[0];
    await authorize(c, principal, r.brand_id, environment);
    return {
      id: r.id,
      kind: r.kind,
      status: r.status,
      result: r.result ? decode(r.result) : null,
      error_code: r.error_code,
      provider_task_id: r.provider_task_id,
      updated_at: r.updated_at,
    };
  });
}
export async function createBrief(principal: string, env: string, input: any) {
  const c = currentAdsConnection();
  await authorize(c, principal, input.brand_id, env, true);
  let videoCount = 0;
  for (const ref of input.asset_refs) {
    const a = await object(c, ref.asset_id, "asset", env);
    if (a.brand_id !== input.brand_id)
      throw fail("asset_scope_mismatch", 403, "附件不属于该品牌");
    if (a.data.mime === "video/mp4") videoCount++;
  }
  if (videoCount && (videoCount !== 1 || input.asset_refs.length !== 1))
    throw fail("asset_limit", 422, "一次需求最多一段视频，不能混合图片");
  const result = await put(c, principal, input.brand_id, env, "brief", input);
  recordOperationChange(null, result);
  return result;
}
export async function createSession(
  principal: string,
  env: string,
  input: any,
) {
  const c = currentAdsConnection(),
    brief = await object(c, input.brief_id, "brief", env, true);
  await authorize(c, principal, brief.brand_id, env, true);
  checkRevision(brief, input.brief_revision);
  const result = await put(c, principal, brief.brand_id, env, "session", {
    brief_id: brief.id,
    brief_revision: brief.revision,
    turns: [],
  });
  recordOperationChange(null, result);
  return result;
}
export async function turn(
  principal: string,
  env: string,
  id: string,
  input: any,
) {
  const c = currentAdsConnection(),
    session = await object(c, id, "session", env, true);
  await authorize(c, principal, session.brand_id, env, true);
  checkRevision(session, input.expected_revision);
  if (session.data.turns.length >= 50)
    throw fail("session_limit", 422, "本会话已达50轮，请创建新会话");
  const [running] = await c.execute<RowDataPacket[]>(
    "SELECT id FROM merchant_studio_jobs WHERE session_id=? AND kind='text' AND status IN ('queued','running') LIMIT 1",
    [id],
  );
  if (running.length) throw fail("turn_running", 409, "上一轮仍在处理");
  const brief = await object(c, session.data.brief_id, "brief", env);
  if (brief.revision !== session.data.brief_revision)
    throw fail("brief_changed", 409, "需求已修改，请新建会话");
  const [prior] = await c.execute<RowDataPacket[]>(
    "SELECT result FROM merchant_studio_jobs WHERE session_id=? AND kind='text' AND status='succeeded' ORDER BY created_at DESC LIMIT 1",
    [id],
  );
  const b = brief.data;
  const assetUnderstandings=[];
  for(const ref of b.asset_refs){
    const a=await object(c,ref.asset_id,'asset',env);
    if(a.brand_id!==session.brand_id)throw fail('asset_scope_mismatch',403,'素材作用域不匹配');
    if(a.data.analysis_job_id){const [analysis]=await c.execute<RowDataPacket[]>('SELECT status,result FROM merchant_studio_jobs WHERE id=? AND brand_id=? AND environment=?',[a.data.analysis_job_id,session.brand_id,env]);
      if(analysis.length&&['queued','running'].includes(analysis[0].status))throw fail('analysis_pending',409,'素材正在理解，请等待后再生成创意');
      if(analysis[0]?.status==='succeeded')assetUnderstandings.push({...decode(analysis[0].result),asset_role:ref.role});
    }
  }

  const job = await queue(c, principal, session, "text", {
    brief: {
      subject_type: b.subject_type,
      subject_description: b.subject_description,
      objective: b.objective,
      idea: b.idea,
      confirmed_facts: b.confirmed_facts,
      ...(b.bid_basis ? { bid_basis: b.bid_basis } : {}),
    },
    message: input.message,
    previous_summary: prior.length
      ? JSON.stringify(decode(prior[0].result)).slice(0, 10000)
      : "",
    has_assets: !!b.asset_refs.length,
    asset_understandings: assetUnderstandings,
    output: "text",
    brief_ref: { id: brief.id, revision: brief.revision },
  });
  const next = await revise(c, principal, session, {
    ...session.data,
    turns: [...session.data.turns, { message: input.message, job_id: job.id }],
  });
  return { session: next, job };
}
export async function readSession(principal: string, env: string, id: string) {
  return withAdsConnection(async (c) => {
    const s = await object(c, id, "session", env);
    await authorize(c, principal, s.brand_id, env);
    const b = await object(c, s.data.brief_id, "brief", env);
    const [jobs] = await c.execute<RowDataPacket[]>(
      "SELECT id,kind,status,result,error_code,provider_task_id FROM merchant_studio_jobs WHERE session_id=? ORDER BY created_at,id LIMIT 100",
      [id],
    );
    return {
      session: s,
      brief: b,
      jobs: jobs.map((j) => ({
        ...j,
        result: j.result ? decode(j.result) : null,
      })),
    };
  });
}
export async function decide(
  principal: string,
  env: string,
  id: string,
  input: any,
) {
  const c = currentAdsConnection(),
    r = await object(c, id, "recipe", env, true);
  await authorize(c, principal, r.brand_id, env, true);
  checkRevision(r, input.expected_revision);
  return revise(c, principal, r, {
    ...r.data,
    ...(input.concept ? { concept: input.concept } : {}),
    decision: input.decision,
    reason: input.reason,
    confirmed_by: principal,
    confirmed_at: new Date().toISOString(),
  });
}
export async function videoJob(
  principal: string,
  env: string,
  id: string,
  input: any,
) {
  const c = currentAdsConnection();
  if (input.output_type === "image")
    throw fail(
      "unsupported_output",
      422,
      "Seedance 1.0 Pro Fast 不输出图片，可生成图片创意文本",
    );
  if (input.confirm_generation !== true)
    throw fail("generation_authorization_required", 422, "请明确确认生成视频");
  if (process.env.STUDIO_VIDEO_ENABLED !== "true")
    throw fail("video_disabled", 503, "视频生成尚未启用");
  const recipe = await object(c, id, "recipe", env, true);
  await authorize(c, principal, recipe.brand_id, env, true);
  checkRevision(recipe, input.expected_revision);
  if (!["accept", "modify"].includes(recipe.data.decision))
    throw fail("recipe_not_approved", 422, "请先确认创意方案");
  const session = await object(c, recipe.data.session_id, "session", env, true),
    brief = await object(c, session.data.brief_id, "brief", env);
  if (brief.revision !== session.data.brief_revision)
    throw fail("brief_changed", 409, "原需求已变化");
  const [inflight] = await c.execute<RowDataPacket[]>(
    "SELECT id FROM merchant_studio_jobs WHERE session_id=? AND kind='video' AND status IN ('queued','running','submitted','submission_unknown') LIMIT 1",
    [session.id],
  );
  if (inflight.length)
    throw fail("video_pending", 409, "已有视频任务待完成或核查，不能重复生成");
  const frame = input.asset_id
    ? await object(c, input.asset_id, "asset", env)
    : null;
  if (
    frame &&
    (frame.brand_id !== recipe.brand_id ||
      !frame.data.mime.startsWith("image/"))
  )
    throw fail("invalid_first_frame", 422, "首帧必须是本品牌图片");
  const job = await queue(c, principal, session, "video", {
    recipe_id: recipe.id,
    recipe_revision: recipe.revision,
    model: "doubao-seedance-1-0-pro-fast-251015",
    prompt: recipe.data.concept.video_prompt,
    spec: input.spec,
    asset_id: frame?.id || null,
    authorization: {
      actor: principal,
      recipe_hash: hash(recipe.data),
      source: "explicit_generate_button",
      confirmed: true,
      expires_at: Date.now() + 30 * 60000,
    },
  });
  recordOperationChange(null, { video_intent: job });
  return job;
}
