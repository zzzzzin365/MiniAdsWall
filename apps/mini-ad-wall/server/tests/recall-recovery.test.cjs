const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { randomUUID } = require("node:crypto");
const { database } = require("./helpers/mysql.cjs");
test("real MySQL: durable delta recovery, checksum rejection and conservative outbox pruning", async (t) => {
  const db = await database(t, []);
  if (!db) return;
  let engine, restored;
  try {
    const migration = require("../dist/scripts/migrate-recall");
    const catalog = [
      ...migration.catalogFields(),
      { field_key: "interests", cardinality: "multi", max_values: 10 },
    ];
    await migration.migrateRecall(catalog);
    await migration.migrateRecall(catalog);
    await assert.rejects(migration.migrateRecall(), /field_catalog_mismatch/);
    process.env.ADS_RECALL_ENABLED = "true";
    const { Engine } = require("../dist/recall/engine"),
      {
        writeSnapshot,
        writeDelta,
        restore,
        poll,
      } = require("../dist/recall/storage"),
      { pruneRecall } = require("../dist/scripts/prune-recall");
    const directory = path.join(db.root, "snapshots");
    engine = new Engine();
    const base = await writeSnapshot(engine, directory, true);
    await restore(engine, base, directory);
    const model = require("../dist/models/ads.model").default;
    await model.executeOperation(
      "operator",
      randomUUID(),
      "d".repeat(64),
      "POST /api/ads",
      async () => ({
        status: 201,
        body: await model.create({
          title: "delta",
          publisher: "test",
          content: "content",
          url: "https://example.com",
          price: 2,
          videos: [],
          attributes: { tags: ["d"] },
          eligibility: { enabled: true },
        }),
      }),
    );
    await poll(engine);
    const delta = await writeDelta(engine, directory, base);
    assert.notEqual(delta, base);
    restored = new Engine();
    await restore(restored, delta, directory);
    assert.deepEqual(restored.sequences, engine.sequences);
    const result = await restored.open([], 50, Date.now(), 1000);
    assert.equal(result.items.length, 1);
    await restored.release(result.token);
    assert.deepEqual(await pruneRecall(directory), {
      deleted: 0,
      reason: "no_durable_watermark",
    });
    await db.connection.execute(
      "INSERT INTO ads_business_recall_consumers(consumer_id,generation_id,lease_until,durable_manifest_ref) VALUES ('recovery','g',UTC_TIMESTAMP(3),?)",
      [delta],
    );
    const manifest = JSON.parse(fs.readFileSync(delta, "utf8")),
      file = path.join(directory, manifest.file),
      original = fs.readFileSync(file);
    fs.appendFileSync(file, "corrupt");
    await assert.rejects(pruneRecall(directory), /checksum_mismatch/);
    const [retained] = await db.connection.query(
      "SELECT COUNT(*) AS n FROM ads_business_recall_outbox",
    );
    assert.equal(retained[0].n, 1);
    fs.writeFileSync(file, original);
    // Recent baselines protect rollback even though the current consumer has checkpointed the delta.
    await db.connection.query(
      "UPDATE ads_business_recall_outbox SET created_at=DATE_SUB(UTC_TIMESTAMP(3),INTERVAL 25 HOUR)",
    );
    assert.equal((await pruneRecall(directory)).deleted, 0);
    for (const name of fs
      .readdirSync(directory)
      .filter((v) => v.endsWith(".manifest.json"))) {
      const ref = path.join(directory, name),
        m = JSON.parse(fs.readFileSync(ref));
      m.created_at = new Date(Date.now() - 25 * 3600000).toISOString();
      fs.writeFileSync(ref, JSON.stringify(m));
    }
    assert.equal((await pruneRecall(directory)).deleted, 1);
    const [usage] = await db.connection.query(
      "SELECT SUM(retained_bytes) AS n FROM ads_business_recall_partition_state",
    );
    assert.equal(Number(usage[0].n), 0);
    // Recovery still works after pruning because the delta contains the applied events.
    await restored.close();
    restored = new Engine();
    await restore(restored, delta, directory);
    assert.deepEqual(restored.sequences, engine.sequences);
    const bad = {
      ...manifest,
      sequences: manifest.sequences.map((s) => ({ ...s, shard_id: 0 })),
    };
    const invalid = path.join(directory, "bad.manifest.json");
    fs.writeFileSync(invalid, JSON.stringify(bad));
    await assert.rejects(
      restore(restored, invalid, directory),
      /invalid_snapshot/,
    );
  } finally {
    await engine?.close();
    await restored?.close();
    delete process.env.ADS_RECALL_ENABLED;
    await db.stop();
  }
});
