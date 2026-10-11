import { createHmac, createHash, timingSafeEqual } from "crypto";
import { RowDataPacket } from "mysql2/promise";
import { withAdsConnection } from "../ads.database";
import {
  search,
  enabled,
  failure,
  matches,
  Document,
} from "../../recall/contracts";
import { projections } from "./repository";
const decode = (v: any) => (typeof v === "string" ? JSON.parse(v) : v);
async function call(endpoint: string, body: any, deadline: number) {
  const token = process.env.ADS_RECALL_TOKEN,
    url = process.env.ADS_RECALL_URL;
  if (!token || !url) throw failure("index_unavailable", 503);
  const remaining = deadline - Date.now();
  if (remaining <= 0) throw failure("recall_deadline_exceeded", 504);
  let response: Response;
  try {
    response = await fetch(url.replace(/\/$/, "") + endpoint, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(remaining),
    });
  } catch (e: any) {
    throw failure(
      e.name === "TimeoutError"
        ? "recall_deadline_exceeded"
        : "index_unavailable",
      e.name === "TimeoutError" ? 504 : 503,
    );
  }
  const bytes = await response.text();
  if (Buffer.byteLength(bytes) > 2 * 1024 * 1024)
    throw failure("invalid_recall_response", 503);
  const result = JSON.parse(bytes);
  if (!response.ok)
    throw failure(result.error || "index_unavailable", response.status);
  return result;
}
export async function hydrate(ids: number[]) {
  return withAdsConnection(async (c) => {
    await c.query("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ");
    await c.query("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY");
    try {
      const docs = await projections(c, ids),
        marks = ids.map(() => "?").join(",");
      if (!ids.length) {
        await c.commit();
        return [];
      }
      const [ads] = await c.execute<RowDataPacket[]>(
        `SELECT d.doc_id,a.id,a.title,a.publisher,a.url,a.price,a.clicks,a.version,a.ranking_score,JSON_LENGTH(a.videos) AS video_count FROM ads_business_recall_docs d JOIN ads_business_ads a ON a.id=d.ad_id WHERE d.doc_id IN (${marks})`,
        ids,
      );
      const [values] = await c.execute<RowDataPacket[]>(
        `SELECT x.doc_id,f.field_key,t.value FROM ads_business_ad_attributes x JOIN ads_business_recall_fields f ON f.field_id=x.field_id JOIN ads_business_recall_terms t ON t.term_id=x.term_id WHERE x.doc_id IN (${marks})`,
        ids,
      );
      const names = new Map<number, Record<string, string[]>>();
      for (const v of values) {
        let attrs = names.get(v.doc_id);
        if (!attrs) names.set(v.doc_id, (attrs = Object.create(null)));
        (attrs[Buffer.from(v.field_key).toString()] ||= []).push(
          Buffer.from(v.value).toString(),
        );
      }
      await c.commit();
      return ads.map((a) => ({
        document: {
          ...docs.find((d) => d.doc_id === a.doc_id)!,
          attributes: names.get(a.doc_id) || {},
        },
        ad: {
          id: a.id,
          title: a.title,
          publisher: a.publisher,
          url: a.url,
          price: Number(a.price),
          clicks: Number(a.clicks),
          version: a.version,
          video_count: Number(a.video_count),
          current_score: a.ranking_score,
        },
      }));
    } catch (e) {
      await c.rollback();
      throw e;
    }
  });
}
export async function findAds(raw: any, principal: string) {
  if (!enabled()) throw failure("feature_disabled", 503);
  const input = search(raw),
    deadline = Date.now() + 200;
  let token: string | undefined;
  try {
    let page = await call("/search", input, deadline);
    token = page.token;
    const snapshot = page.snapshot,
      items: any[] = [];
    let checked = 0,
      exhausted = false;
    while (true) {
      if (!Array.isArray(page.items) || page.items.length > 100)
        throw failure("invalid_recall_response", 503);
      checked += page.items.length;
      if (checked > 2000) throw failure("query_budget_exceeded", 422);
      const rows = await hydrate(page.items.map((i) => i.doc_id));
      if (Date.now() > deadline) throw failure("recall_deadline_exceeded", 504);
      for (const candidate of page.items) {
        const row = rows.find((v) => v.document.doc_id === candidate.doc_id);
        if (row && matches(row.document, input.conditions, Date.now()))
          items.push({ ...row.ad, indexed_score: candidate.score });
        if (items.length === input.limit) break;
      }
      exhausted = page.candidate_exhausted;
      if (items.length === input.limit || exhausted) break;
      if (checked === 2000) throw failure("query_budget_exceeded", 422);
      page = await call(
        "/next",
        { token, limit: Math.min(input.limit, 2000 - checked) },
        deadline,
      );
    }
    const result = {
      items,
      selection: {
        strategy: "attribute_bitmap_topk",
        limit: input.limit,
        returned: items.length,
        candidate_exhausted: exhausted,
      },
      snapshot,
      consistency: {
        mode: "eventual_with_db_recheck",
        max_pending_age_ms: page.pending_age_ms ?? 0,
      },
      query_hash: createHash("sha256")
        .update(JSON.stringify({ input, principal }))
        .digest("hex"),
    };
    if (Buffer.byteLength(JSON.stringify(result)) > 262144)
      throw failure("response_too_large", 422);
    return result;
  } finally {
    if (token)
      void call("/release", { token }, Date.now() + 1000).catch(() => {});
  }
}
export async function listPage(raw: any, principal: string) {
  if (raw.q !== undefined && (typeof raw.q !== "string" || raw.q.length > 200))
    throw failure("搜索词必须是最多 200 字的文本");
  const query = (raw.q ?? "").trim();
  const limit = raw.limit === undefined ? 50 : Number(raw.limit);
  if (!Number.isInteger(limit) || limit < 1 || limit > 100)
    throw failure("invalid_filter");
  const credentials =
    process.env.ADS_OPERATOR_TOKEN || process.env.ADS_OPERATOR_TOKENS;
  const key =
    process.env.ADS_CURSOR_SECRET ||
    (credentials
      ? createHash("sha256").update(credentials).digest("hex")
      : undefined);
  if (!key || key.length < 16) throw failure("cursor_secret_required", 503);
  let cursor: any;
  if (raw.cursor) {
    try {
      const [data, signature] = String(raw.cursor).split(".");
      const expected = createHmac("sha256", key)
        .update(data)
        .digest("base64url");
      const a = Buffer.from(signature || ""),
        b = Buffer.from(expected);
      if (a.length !== b.length || !timingSafeEqual(a, b)) throw Error();
      cursor = JSON.parse(Buffer.from(data, "base64url").toString());
      if (
        cursor.principal !== principal ||
        (cursor.q ?? "") !== query ||
        cursor.expires < Date.now() ||
        typeof cursor.id !== "string" ||
        !/^\d+\.\d{8}$/.test(cursor.score)
      )
        throw Error();
    } catch {
      throw failure("invalid_cursor");
    }
  }
  return withAdsConnection(async (c) => {
    const filters: string[] = [];
    const params: (string | number)[] = [];
    if (query) {
      // Treat LIKE wildcards as literal input; search the full content, not its card preview.
      const pattern = `%${query.replace(/[!%_]/g, "!$&")}%`;
      filters.push("(LOWER(title) LIKE LOWER(?) ESCAPE '!' OR LOWER(publisher) LIKE LOWER(?) ESCAPE '!' OR LOWER(content) LIKE LOWER(?) ESCAPE '!')");
      params.push(pattern, pattern, pattern);
    }
    if (cursor) {
      filters.push("(ranking_score < ? OR (ranking_score = ? AND id > ?))");
      params.push(cursor.score, cursor.score, cursor.id);
    }
    const where = filters.length ? `WHERE ${filters.join(" AND ")}` : "";
    const [rows] = await c.query<RowDataPacket[]>(
      `SELECT id,title,publisher,LEFT(content,800) AS content,url,price,clicks,JSON_EXTRACT(videos,'$[0 to 1]') AS videos,JSON_LENGTH(videos) AS video_count,version,ranking_score FROM ads_business_ads ${where} ORDER BY ranking_score DESC,id ASC LIMIT ?`,
      [...params, limit + 1],
    );
    const selected: any[] = [];
    let bytes = 0;
    for (const row of rows.slice(0, limit)) {
      const item = {
        id: row.id,
        title: row.title,
        publisher: row.publisher,
        content: row.content,
        url: row.url,
        price: Number(row.price),
        clicks: Number(row.clicks),
        videos: (decode(row.videos) || []).filter(
          (v: string) => Buffer.byteLength(v) <= 4096,
        ),
        version: row.version,
        video_count: Number(row.video_count),
      };
      const size = Buffer.byteLength(JSON.stringify(item));
      if (bytes + size > 220 * 1024) break;
      selected.push(item);
      bytes += size;
    }
    if (rows.length && !selected.length)
      throw failure("response_too_large", 422);
    const more = rows.length > selected.length,
      items = selected,
      last = rows[items.length - 1];
    let next_cursor: string | null = null;
    if (more) {
      const data = Buffer.from(
        JSON.stringify({
          score: last.ranking_score,
          id: last.id,
          principal,
          q: query,
          expires: Date.now() + 3600000,
        }),
      ).toString("base64url");
      next_cursor =
        data + "." + createHmac("sha256", key).update(data).digest("base64url");
    }
    return {
      items: items.map((r) => ({
        id: r.id,
        title: r.title,
        publisher: r.publisher,
        content: r.content,
        url: r.url,
        price: Number(r.price),
        clicks: Number(r.clicks),
        videos: decode(r.videos) || [],
        version: r.version,
        video_count: Number(r.video_count),
      })),
      next_cursor,
      omitted_fields: ["content_after_800_chars", "videos_after_2"],
    };
  });
}
export async function adContext(conditions: any, principal: string) {
  const input = search({ conditions: conditions ?? [], limit: 50 });
  let items: any[], selection: any, snapshot: any;
  if (input.conditions.length) {
    const result = await findAds(input, principal);
    items = result.items.map((v) => ({ ...v, videos: [] }));
    selection = result.selection;
    snapshot = result.snapshot;
  } else {
    const page = await listPage({ limit: 50 }, principal);
    items = page.items;
    selection = {
      strategy: "ranking_topk",
      limit: 50,
      truncated: !!page.next_cursor,
    };
  }
  const context: any = {
    version: 1,
    captured_at: new Date().toISOString(),
    scope: "authorized_ads",
    summary: null,
    summary_status: "not_materialized",
    items: items.map(({ content, videos, ...rest }) => ({
      ...rest,
      videos: [],
      video_count: rest.video_count ?? videos?.length ?? 0,
    })),
    selection,
    snapshot,
  };
  while (
    Buffer.byteLength(JSON.stringify(context)) > 65536 &&
    context.items.length
  ) {
    context.items.pop();
    context.selection.truncated = true;
  }
  context.selection.omitted_fields = ["content", "videos"];
  return context;
}
