import fs from "fs";
import path from "path";
import { createHash } from "crypto";
import { RowDataPacket } from "mysql2/promise";
import { adsPool, closeAdsDatabase } from "../services/ads.database";
interface CatalogField {
  field_key: string;
  cardinality: "scalar" | "multi";
  max_values: number;
}
const defaults = [
  { field_key: "region", cardinality: "scalar", max_values: 50 },
  { field_key: "category", cardinality: "scalar", max_values: 50 },
  { field_key: "tags", cardinality: "multi", max_values: 50 },
];
export function catalogFields(input: unknown = defaults): CatalogField[] {
  if (
    !Array.isArray(input) ||
    !input.length ||
    input.length > 128 ||
    Buffer.byteLength(JSON.stringify(input)) > 65536
  )
    throw Error("invalid_field_catalog");
  const seen = new Set<string>();
  return input.map((f) => {
    if (
      !f ||
      typeof f !== "object" ||
      Array.isArray(f) ||
      Object.keys(f).some(
        (k) => !["field_key", "cardinality", "max_values"].includes(k),
      ) ||
      typeof f.field_key !== "string" ||
      !/^[a-z0-9_]{1,64}$/.test(f.field_key) ||
      seen.has(f.field_key) ||
      !["scalar", "multi"].includes(f.cardinality)
    )
      throw Error("invalid_field_catalog");
    seen.add(f.field_key);
    const max_values = f.max_values ?? 50;
    if (!Number.isInteger(max_values) || max_values < 1 || max_values > 50)
      throw Error("invalid_field_catalog");
    return { field_key: f.field_key, cardinality: f.cardinality, max_values };
  });
}
export async function migrateRecall(catalog?: unknown) {
  const definitions = catalogFields(catalog);
  const c = await adsPool().getConnection();
  const root = path.resolve(
    __dirname,
    fs.existsSync(path.join(__dirname, "../package.json")) ? ".." : "../..",
  );
  const sql = fs.readFileSync(
    path.join(root, "migrations/002_ads_recall.sql"),
    "utf8",
  );
  const checksum = createHash("sha256").update(sql).digest("hex");
  let locked = false;
  try {
    const [lock] = await c.query<RowDataPacket[]>(
      "SELECT GET_LOCK(CONCAT('ads:', LEFT(SHA2(DATABASE(),256),50)),30) AS ok",
    );
    if (Number(lock[0].ok) !== 1) throw new Error("migration_lock_timeout");
    locked = true;
    const [old] = await c.query<RowDataPacket[]>(
      "SELECT checksum FROM ads_business_migrations WHERE name='schema-ads-recall-v1'",
    );
    if (old.length && old[0].checksum !== checksum)
      throw new Error("migration_checksum_mismatch");
    for (const statement of sql
      .split(";")
      .map((s) => s.trim())
      .filter(Boolean))
      await c.query(statement);
    const [columns] = await c.query<RowDataPacket[]>(
      "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ads_business_operations' AND COLUMN_NAME='recall_receipt'",
    );
    if (!columns.length)
      await c.query(
        "ALTER TABLE ads_business_operations ADD COLUMN recall_receipt JSON NULL",
      );
    for (let i = 0; i < 32; i++)
      await c.execute(
        "INSERT IGNORE INTO ads_business_recall_partition_state(shard_id) VALUES (?)",
        [i],
      );
    const [registered] = await c.query<RowDataPacket[]>(
      "SELECT field_key,cardinality,max_values FROM ads_business_recall_fields",
    );
    for (const row of registered) {
      const key = Buffer.isBuffer(row.field_key)
          ? row.field_key.toString()
          : row.field_key,
        expected = definitions.find((f) => f.field_key === key);
      if (
        !expected ||
        expected.cardinality !== row.cardinality ||
        expected.max_values !== row.max_values
      )
        throw Error("field_catalog_mismatch");
    }
    if (old.length && registered.length !== definitions.length)
      throw Error("field_catalog_change_requires_migration");
    for (const f of definitions)
      await c.execute(
        "INSERT IGNORE INTO ads_business_recall_fields(field_key,cardinality,max_values) VALUES (?,?,?)",
        [f.field_key, f.cardinality, f.max_values],
      );
    // The migration must run before enabling the new writer. Never overwrite existing eligibility.
    let after = "";
    while (true) {
      const [rows] = await c.execute<RowDataPacket[]>(
        "SELECT id FROM ads_business_ads WHERE id > ? ORDER BY id LIMIT 500",
        [after],
      );
      if (!rows.length) break;
      for (const row of rows)
        await c.execute(
          "INSERT IGNORE INTO ads_business_recall_docs(ad_id) VALUES (?)",
          [row.id],
        );
      await c.execute(
        "UPDATE ads_business_recall_docs SET shard_id=doc_id % 32 WHERE index_revision=0 AND ad_id IN (" +
          rows.map(() => "?").join(",") +
          ")",
        rows.map((r) => r.id),
      );
      after = rows[rows.length - 1].id;
    }
    await c.execute(
      "INSERT IGNORE INTO ads_business_migrations(name,checksum) VALUES ('schema-ads-recall-v1',?)",
      [checksum],
    );
  } finally {
    if (locked)
      await c
        .query(
          "SELECT RELEASE_LOCK(CONCAT('ads:', LEFT(SHA2(DATABASE(),256),50)))",
        )
        .catch(() => {});
    c.release();
  }
}
if (require.main === module)
  Promise.resolve()
    .then(() => {
      const args = process.argv.slice(2);
      if (args.length && !(args.length === 2 && args[0] === "--fields"))
        throw Error("usage: migrate-recall [--fields CATALOG_JSON]");
      const file = args[1] || process.env.ADS_RECALL_FIELDS_FILE;
      return migrateRecall(
        file ? JSON.parse(fs.readFileSync(file, "utf8")) : undefined,
      );
    })
    .then(() => console.log("recall migration complete"))
    .catch((e) => {
      console.error(e.code ? "recall migration failed" : e.message);
      process.exitCode = 1;
    })
    .finally(closeAdsDatabase);
