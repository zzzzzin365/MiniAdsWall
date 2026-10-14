import fs from "fs/promises";
import path from "path";
import dns from "dns/promises";
import net from "net";
import { randomUUID, createHash } from "crypto";
import { PoolConnection, RowDataPacket } from "mysql2/promise";
import config from "../../../config";
import { withAdsConnection } from "../../ads.database";
import { object, put, authorize, decode } from "./store";
import { validateResult } from "./contracts";
import { analysisInput } from "./understanding";
import { validateUnderstanding } from "./contracts";
import { validateMediaRag } from "./contracts";
import { mediaCorpus } from "./knowledge";
import { studioDir, signedAsset, probe } from "./assets";
export type Provider = (url: string, body?: any) => Promise<any>;
export async function studioRequest(url: string, body?: any) {
  const r = await fetch(config.ADS_AGENT_API_URL.replace(/\/$/, "") + url, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: "Bearer " + (process.env.AGENT_SERVICE_TOKEN || ""),
      "X-Request-ID": randomUUID(),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(100000),
  });
  if (!r.ok) {
    const data:any=await r.json().catch(()=>({}));
    const detail = String(data.detail || '');
    const code = detail.startsWith('seed_model_not_open') ? 'seed_model_not_open'
      : detail.startsWith('media_rag_unavailable') ? 'media_rag_unavailable' : 'studio_provider_error';
    throw Object.assign(Error("studio_provider_error"), {status:r.status,code});
  }
  return r.json();
}
export async function materialize(url: string) {
  const u = new URL(url),
    suffixes = (
      process.env.STUDIO_PROVIDER_ASSET_HOSTS ||
      "volces.com,volcengine.com,byteimg.com"
    )
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean);
  if (
    u.protocol !== "https:" ||
    u.username ||
    u.password ||
    (u.port && u.port !== "443") ||
    net.isIP(u.hostname) ||
    !suffixes.some((h) => u.hostname === h || u.hostname.endsWith("." + h))
  )
    throw Error("untrusted_result_host");
  const ips = await dns.lookup(u.hostname, { all: true });
  if (
    !ips.length ||
    ips.some(
      (x) =>
        /^(127\.|10\.|192\.168\.|169\.254\.|0\.|172\.(1[6-9]|2\d|3[01])\.)/.test(
          x.address,
        ) || x.address.includes(":"),
    )
  )
    throw Error("untrusted_result_address");
  const r = await fetch(url, {
    redirect: "error",
    signal: AbortSignal.timeout(60000),
  });
  if (!r.ok || !r.body) throw Error("download_failed");
  if (Number(r.headers.get("content-length")) > 100 * 1024 * 1024)
    throw Error("asset_too_large");
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const raw of r.body as any) {
    const chunk = Buffer.from(raw);
    size += chunk.length;
    if (size > 100 * 1024 * 1024) throw Error("asset_too_large");
    chunks.push(chunk);
  }
  const filename = randomUUID() + ".mp4";
  await fs.mkdir(studioDir(), { recursive: true });
  const file = path.join(studioDir(), filename);
  await fs.writeFile(file, Buffer.concat(chunks));
  try {
    const p = await probe(file);
    if (
      !String(p.format?.format_name).includes("mp4") ||
      !(Number(p.format.duration) > 0) ||
      Number(p.format.duration) > 13
    )
      throw Error("invalid_result_video");
    return {
      filename,
      mime: "video/mp4",
      size,
      duration: Number(p.format.duration),
      hash: createHash("sha256").update(Buffer.concat(chunks)).digest("hex"),
    };
  } catch (e) {
    await fs.rm(file, { force: true });
    throw e;
  }
}
async function transaction<T>(fn: (c: PoolConnection) => Promise<T>) {
  return withAdsConnection(async (c) => {
    await c.beginTransaction();
    try {
      const result = await fn(c);
      await c.commit();
      return result;
    } catch (e) {
      await c.rollback();
      throw e;
    }
  });
}
async function claim(environment: string) {
  return transaction(async (c) => {
    // A crash after a paid submission may have created a task. Never re-submit.
    await c.execute(
      "UPDATE merchant_studio_jobs SET status='submission_unknown',error_code='worker_interrupted',lease_token=NULL WHERE environment=? AND kind='video' AND status='running' AND lease_until<UTC_TIMESTAMP(3)",
      [environment],
    );
    await c.execute(
      "UPDATE merchant_studio_jobs SET status=IF(JSON_UNQUOTE(JSON_EXTRACT(input,'$.kind'))='image_generation','submission_unknown',IF(attempts<2,'queued','failed')),error_code='worker_interrupted',lease_token=NULL WHERE environment=? AND kind='text' AND status='running' AND lease_until<UTC_TIMESTAMP(3)",
      [environment],
    );
    const enabled = process.env.MERCHANT_WORKSPACE_ENABLED === "true";
    const [rows] = await c.execute<RowDataPacket[]>(
      `SELECT * FROM merchant_studio_jobs WHERE environment=? AND ((status='queued' AND ?=TRUE) OR status='submitted') AND (lease_until IS NULL OR lease_until<UTC_TIMESTAMP(3)) ORDER BY created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED`,
      [environment, enabled],
    );
    if (!rows.length) return null;
    const row = rows[0];
    try {
      await authorize(c, row.created_by, row.brand_id, environment, true);
    } catch {
      await c.execute(
        "UPDATE merchant_studio_jobs SET status='blocked',error_code='permission_revoked' WHERE id=?",
        [row.id],
      );
      return null;
    }
    await c.execute("SELECT id FROM marketing_brands WHERE id=? FOR UPDATE", [
      row.brand_id,
    ]);
    const [active] = await c.execute<RowDataPacket[]>(
      "SELECT COUNT(*) AS n FROM merchant_studio_jobs WHERE brand_id=? AND status IN ('running','submitted') AND lease_until>UTC_TIMESTAMP(3)",
      [row.brand_id],
    );
    if (Number(active[0].n) >= 2) return null;
    if (
      row.kind === "video" &&
      row.status === "submitted" &&
      Number(row.attempts) >= 8
    ) {
      await c.execute(
        "UPDATE merchant_studio_jobs SET status='failed',error_code='query_retry_limit',lease_token=NULL WHERE id=?",
        [row.id],
      );
      return null;
    }
    const token = randomUUID();
    await c.execute(
      "UPDATE merchant_studio_jobs SET status=IF(status='queued','running',status),lease_token=?,lease_until=DATE_ADD(UTC_TIMESTAMP(3),INTERVAL 180 SECOND),attempts=attempts+1,updated_at=UTC_TIMESTAMP(3) WHERE id=?",
      [token, row.id],
    );
    return { ...row, input: decode(row.input), lease_token: token };
  });
}
async function update(
  job: any,
  status: string,
  result: any,
  error: string | null = null,
  provider: string | null = null,
) {
  return transaction(async (c) => {
    const [r] = await c.execute<RowDataPacket[]>(
      "SELECT id FROM merchant_studio_jobs WHERE id=? AND lease_token=? AND lease_until>UTC_TIMESTAMP(3) FOR UPDATE",
      [job.id, job.lease_token],
    );
    if (!r.length) return false;
    try {
      await authorize(c, job.created_by, job.brand_id, job.environment, true);
    } catch {
      status = "blocked";
      error = "permission_revoked";
      result = null;
    }
    await c.execute(
      "UPDATE merchant_studio_jobs SET status=?,result=?,error_code=?,provider_task_id=COALESCE(?,provider_task_id),lease_token=NULL,lease_until=IF(?='submitted',DATE_ADD(UTC_TIMESTAMP(3),INTERVAL 10 SECOND),NULL),updated_at=UTC_TIMESTAMP(3) WHERE id=?",
      [
        status,
        result ? JSON.stringify(result) : null,
        error,
        provider,
        status,
        job.id,
      ],
    );
    return true;
  });
}
export async function tick(
  environment = config.MARKETING.environment,
  provider: Provider = studioRequest,
  download = materialize,
) {
  const job: any = await claim(environment);
  if (!job) return false;
  if(job.input.kind==='image_generation'){
    try {
      const result = await provider('/studio/image',{prompt:job.input.prompt});
      const bytes = Buffer.from(result.b64_json || '', 'base64');
      if(bytes.length>30*1024*1024 || bytes.subarray(0,8).toString('hex')!=='89504e470d0a1a0a') throw Error('invalid_image');
      const filename=randomUUID()+'.png';
      await fs.mkdir(studioDir(),{recursive:true});
      await fs.writeFile(path.join(studioDir(),filename),bytes);
      await transaction(async c=>{
        const [lease]:any=await c.execute('SELECT id FROM merchant_studio_jobs WHERE id=? AND lease_token=? AND lease_until>UTC_TIMESTAMP(3) FOR UPDATE',[job.id,job.lease_token]);
        if(!lease.length) throw Error('lease_expired');
        await authorize(c,job.created_by,job.brand_id,environment,true);
        const a=await put(c,job.created_by,job.brand_id,environment,'asset',{filename,mime:'image/png',size:bytes.length});
        const o=await put(c,job.created_by,job.brand_id,environment,'output',{asset_id:a.id,recipe_id:job.input.recipe_id,recipe_revision:job.input.recipe_revision,job_id:job.id,review:'pending',model:result.model});
        await c.execute("UPDATE merchant_studio_jobs SET status='succeeded',result=?,lease_token=NULL,lease_until=NULL WHERE id=?",[JSON.stringify({asset_id:a.id,output_id:o.id,model:result.model}),job.id]);
      });
    } catch(e){await update(job,'submission_unknown',null,'image_generation_needs_review');}
    return true;
  }
  if(job.input.kind==='asset_analysis'){
    try{const input=await analysisInput(job);const result=await provider('/studio/understand',input);if(!validateUnderstanding(result)||result.asset_id!==job.input.asset_id||result.model!==job.input.model)throw Error('invalid_understanding');await update(job,'succeeded',result);}
    catch(e){await update(job,'failed',null,(e as any).code==='seed_model_not_open'?'seed_model_not_open':'seed_understanding_failed');}
    return true;
  }
  if (job.kind === "text") {
    try {
      const { brief_ref, ...input } = job.input;
      const corpus = await withAdsConnection(async c => {
        const session = await object(c, job.session_id, 'session', environment);
        const brief = await object(c, session.data.brief_id, 'brief', environment);
        if (session.brand_id !== job.brand_id || brief.brand_id !== job.brand_id ||
            brief.id !== brief_ref.id || brief.revision !== brief_ref.revision) throw Error('brief_changed');
        return mediaCorpus(c, job.created_by, job.brand_id, environment,
          (brief.data.asset_refs || []).map((r: any) => r.asset_id));
      });
      input.media_knowledge = corpus;
      const result = await provider("/studio/generate", input);
      if (!validateResult(result)) throw Error("invalid_result_schema");
      if (corpus.sources.length && !validateMediaRag(result.media_rag)) throw Error('missing_media_rag');
      if (result.media_rag) {
        const rag = result.media_rag;
        if (rag.corpus_assets !== corpus.sources.length || rag.corpus_truncated !== corpus.truncated ||
            (corpus.sources.length && !['retrieved', 'no_match'].includes(rag.status))) throw Error('invalid_media_scope');
        for (const source of rag.retrieved) {
          if (!corpus.sources.some(s => s.asset_id === source.asset_id &&
            s.asset_revision === source.asset_revision && s.analysis_job_id === source.analysis_job_id))
            throw Error('invalid_media_source');
        }
        if (JSON.stringify(rag.citations.map((s: any) => s.id)) !== JSON.stringify(result.source_ids || []) ||
            rag.citations.some((s: any) => !rag.retrieved.some((r: any) => JSON.stringify(r) === JSON.stringify(s))))
          throw Error('invalid_media_citation');
      }
      await transaction(async (c) => {
        const [r] = await c.execute<RowDataPacket[]>(
          "SELECT id FROM merchant_studio_jobs WHERE id=? AND lease_token=? AND lease_until>UTC_TIMESTAMP(3) FOR UPDATE",
          [job.id, job.lease_token],
        );
        if (!r.length) return;
        await authorize(c, job.created_by, job.brand_id, environment, true);
        for (const source of result.media_rag?.citations || []) {
          const asset = await object(c, source.asset_id, 'asset', environment);
          if (asset.brand_id !== job.brand_id || asset.revision !== source.asset_revision ||
              asset.data.analysis_job_id !== source.analysis_job_id) throw Error('media_source_changed');
        }
        const ids = [];
        for (const concept of result.concepts) {
          const recipe = await put(
            c,
            job.created_by,
            job.brand_id,
            environment,
            "recipe",
            {
              session_id: job.session_id,
              job_id: job.id,
              brief_ref,
              concept,
              decision: "pending",
              model: result.model,
              media_sources: result.media_rag?.citations || [],
              external_references: result.reference_knowledge?.citations || [],
            },
          );
          ids.push(recipe.id);
        }
        result.recipe_ids = ids;
        await c.execute(
          "UPDATE merchant_studio_jobs SET status='succeeded',result=?,lease_token=NULL,lease_until=NULL,updated_at=UTC_TIMESTAMP(3) WHERE id=?",
          [JSON.stringify(result), job.id],
        );
      });
    } catch (e) {
      await update(
        job,
        "failed",
        null,
        (e as any).status === 403
          ? "permission_revoked"
          : (e as any).code === 'media_rag_unavailable' ? 'media_rag_unavailable' : "text_generation_failed",
      );
    }
    return true;
  }
  let submitted = false;
  try {
    if (job.status === "queued") {
      if (process.env.STUDIO_VIDEO_ENABLED !== "true")
        throw Error("video_disabled");
      if (job.input.authorization.expires_at < Date.now())
        throw Error("authorization_expired");
      const context = await withAdsConnection(async (c) => {
        await authorize(c, job.created_by, job.brand_id, environment, true);
        const recipe = await object(
          c,
          job.input.recipe_id,
          "recipe",
          environment,
        );
        if (
          recipe.revision !== job.input.recipe_revision ||
          !["accept", "modify"].includes(recipe.data.decision)
        )
          throw Error("recipe_changed");
        const session = await object(c, job.session_id, "session", environment),
          brief = await object(c, session.data.brief_id, "brief", environment);
        if (brief.revision !== session.data.brief_revision)
          throw Error("brief_changed");
        return true;
      });
      if (!context) throw Error("invalid_authorization");
      const payload = {
        prompt: job.input.prompt,
        ...job.input.spec,
        ...(job.input.asset_id
          ? { image_url: signedAsset(job.input.asset_id, environment) }
          : {}),
      };
      // Set before network I/O; even HTTP failures cannot prove a paid task wasn't created.
      submitted = true;
      const created = await provider("/studio/video", payload);
      if (
        typeof created.id !== "string" ||
        !/^[a-zA-Z0-9_-]{1,200}$/.test(created.id)
      )
        throw Error("invalid_provider_task");
      await update(
        job,
        "submitted",
        { model: "doubao-seedance-1-0-pro-fast-251015" },
        null,
        created.id,
      );
      return true;
    }
    const result = await provider(
      "/studio/video/" + encodeURIComponent(job.provider_task_id),
    );
    if (["queued", "running"].includes(result.status)) {
      await update(job, "submitted", null);
      return true;
    }
    if (result.status !== "succeeded") {
      await update(job, "failed", null, "provider_" + String(result.status));
      return true;
    }
    const asset = await download(result.content.video_url);
    await transaction(async (c) => {
      const [r] = await c.execute<RowDataPacket[]>(
        "SELECT id FROM merchant_studio_jobs WHERE id=? AND lease_token=? AND lease_until>UTC_TIMESTAMP(3) FOR UPDATE",
        [job.id, job.lease_token],
      );
      if (!r.length) return;
      await authorize(c, job.created_by, job.brand_id, environment, true);
      const a = await put(
        c,
        job.created_by,
        job.brand_id,
        environment,
        "asset",
        asset,
      );
      const o = await put(
        c,
        job.created_by,
        job.brand_id,
        environment,
        "output",
        {
          asset_id: a.id,
          recipe_id: job.input.recipe_id,
          recipe_revision: job.input.recipe_revision,
          job_id: job.id,
          review: "pending",
          model: "doubao-seedance-1-0-pro-fast-251015",
        },
      );
      await c.execute(
        "UPDATE merchant_studio_jobs SET status='succeeded',result=?,lease_token=NULL,lease_until=NULL,error_code=NULL,updated_at=UTC_TIMESTAMP(3) WHERE id=?",
        [
          JSON.stringify({
            asset_id: a.id,
            output_id: o.id,
            model: "doubao-seedance-1-0-pro-fast-251015",
          }),
          job.id,
        ],
      );
    });
  } catch (e) {
    if (submitted)
      await update(job, "submission_unknown", null, "provider_submit_unknown");
    else if (job.status === "submitted")
      await update(job, "submitted", null, "query_or_materialization_failed");
    else
      await update(
        job,
        "failed",
        null,
        String((e as Error).message).slice(0, 100),
      );
  }
  return true;
}
