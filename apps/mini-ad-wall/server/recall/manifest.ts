import fs from "fs";
import path from "path";
import { createHash } from "crypto";
import { failure } from "./contracts";

export async function readManifest(ref: string, directory: string) {
  if (path.dirname(path.resolve(ref)) !== path.resolve(directory))
    throw failure("invalid_snapshot_path", 503);
  const m = JSON.parse(await fs.promises.readFile(ref, "utf8"));
  if (
    m.format !== 1 ||
    typeof m.file !== "string" ||
    path.basename(m.file) !== m.file ||
    !/^[a-f0-9]{64}$/.test(m.sha256) ||
    !Number.isFinite(Date.parse(m.created_at))
  )
    throw failure("invalid_snapshot", 503);
  if (
    !Array.isArray(m.sequences) ||
    m.sequences.length !== 32 ||
    m.sequences.some(
      (s, i) =>
        s.shard_id !== i || typeof s.seq !== "string" || !/^\d+$/.test(s.seq),
    )
  )
    throw failure("invalid_snapshot", 503);
  if (
    m.base &&
    (typeof m.base !== "string" || path.basename(m.base) !== m.base)
  )
    throw failure("invalid_snapshot_path", 503);
  return m;
}

// A durable watermark is usable for deletion only when every file in its recovery chain is intact.
export async function verifyManifest(
  ref: string,
  directory: string,
  seen = new Set<string>(),
  depth = 0,
): Promise<any> {
  if (depth > 32 || seen.has(ref))
    throw failure("snapshot_chain_too_long", 503);
  seen.add(ref);
  const m = await readManifest(ref, directory);
  if (m.base)
    await verifyManifest(
      path.join(directory, m.base),
      directory,
      seen,
      depth + 1,
    );
  const hash = createHash("sha256");
  for await (const bytes of fs.createReadStream(path.join(directory, m.file)))
    hash.update(bytes);
  if (hash.digest("hex") !== m.sha256)
    throw failure("snapshot_checksum_mismatch", 503);
  return m;
}

export async function syncDirectory(directory: string) {
  const handle = await fs.promises.open(directory, "r");
  try {
    await handle.sync();
  } finally {
    await handle.close();
  }
}
