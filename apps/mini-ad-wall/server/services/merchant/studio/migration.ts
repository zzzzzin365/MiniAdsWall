import fs from "fs";
import path from "path";
import { createHash } from "crypto";
import { RowDataPacket } from "mysql2/promise";
import { withAdsConnection } from "../../ads.database";
import { requireMarketingSchema, verify } from "../../marketing/migrations";
import { splitSqlStatements } from "../../sql-statements";
const root = path.resolve(
  __dirname,
  fs.existsSync(path.join(__dirname, "../../../package.json"))
    ? "../../.."
    : "../../../..",
);
const file = path.join(root, "migrations/019_merchant_studio.sql");
export async function studioSchema(up = false) {
  await requireMarketingSchema(13);
  const sql = fs.readFileSync(file, "utf8"),
    checksum = createHash("sha256").update(sql).digest("hex");
  return withAdsConnection(async (c) => {
    if (up) {
      const [lock] = await c.query<RowDataPacket[]>(
        "SELECT GET_LOCK(CONCAT('studio:',LEFT(SHA2(DATABASE(),256),50)),30) AS acquired",
      );
      if (Number(lock[0].acquired) !== 1)
        throw Error("studio_migration_lock_timeout");
    }
    try {
      const [tables] = await c.query<RowDataPacket[]>(
        "SHOW TABLES LIKE 'merchant_studio_schema'",
      );
      if (!tables.length && !up)
        throw Object.assign(Error("工作台需要执行迁移"), {
          status: 503,
          code: "studio_migration_required",
        });
      if (up)
        await c.query(
          "CREATE TABLE IF NOT EXISTS merchant_studio_schema (version INT PRIMARY KEY, checksum CHAR(64) NOT NULL, state VARCHAR(16) NOT NULL) ENGINE=InnoDB",
        );
      const [rows] = await c.query<RowDataPacket[]>(
        "SELECT * FROM merchant_studio_schema WHERE version=19",
      );
      if (rows.length && rows[0].checksum !== checksum)
        throw Error("studio_migration_checksum_mismatch");
      if (up && rows[0]?.state !== "applied") {
        await c.execute(
          "INSERT INTO merchant_studio_schema VALUES (19,?,'applying') ON DUPLICATE KEY UPDATE state='applying'",
          [checksum],
        );
        for (const statement of splitSqlStatements(sql))
          await c.query(statement);
        await verify(c, splitSqlStatements(sql));
        await c.query(
          "UPDATE merchant_studio_schema SET state='applied' WHERE version=19",
        );
      }
      const [state] = await c.query<RowDataPacket[]>(
        "SELECT state FROM merchant_studio_schema WHERE version=19",
      );
      if (state[0]?.state !== "applied")
        throw Object.assign(Error("工作台需要执行迁移"), {
          status: 503,
          code: "studio_migration_required",
        });
      await verify(c, splitSqlStatements(sql));
      // Probe required capabilities independently of unrelated reserved migrations 014–018.
      await c.query(
        "SELECT id,revision,kind,brand_id,environment,data,created_by FROM merchant_studio_objects LIMIT 0",
      );
      await c.query(
        "SELECT id,input_hash,lease_token,lease_until,status,provider_task_id FROM merchant_studio_jobs LIMIT 0",
      );
      return { version: 19, state: "applied", checksum };
    } catch (e) {
      const r = e as any;
      throw Object.assign(new Error("studio_schema_unavailable"), {
        status: 503,
        code: r.code || r.message,
      });
    } finally {
      if (up)
        await c.query(
          "SELECT RELEASE_LOCK(CONCAT('studio:',LEFT(SHA2(DATABASE(),256),50)))",
        );
    }
  });
}
