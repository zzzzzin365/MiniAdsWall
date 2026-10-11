import Bitmap = require("roaring/RoaringBitmap32");
import { performance } from "perf_hooks";
import { Condition, Document, failure } from "./contracts";
import { Node, put, remove, find, iterate, iterateFrom, top } from "./tree";
const scores = new WeakMap<Document, bigint>();
export function compare(a: Document, b: Document) {
  const score = (d: Document) => {
    let v = scores.get(d);
    if (v === undefined) {
      v = BigInt(d.score.replace(".", ""));
      scores.set(d, v);
    }
    return v;
  };
  const x = score(a),
    y = score(b);
  return x === y ? a.doc_id - b.doc_id : x > y ? -1 : 1;
}
const byId = (a: Document, b: Document) => a.doc_id - b.doc_id;
const local = (id: number) => Math.floor(id / 32);
const scoreValue = (d: Document) => BigInt(d.score.replace(".", ""));
const decimal = (v: bigint) => {
  const s = v.toString().padStart(9, "0");
  return s.slice(0, -8) + "." + s.slice(-8);
};
// Monotone score bands: keep the leading eight bits of the exact fixed-point integer.
// Bands only skip impossible ranges; ordering inside a band still uses the exact score.
function band(d: Document) {
  const v = scoreValue(d),
    length = v.toString(2).length,
    shift = Math.max(0, length - 8),
    prefix = Number(v >> BigInt(shift));
  return {
    key: length * 256 + prefix,
    lower: BigInt(prefix) << BigInt(shift),
    upper: BigInt(prefix + 1) << BigInt(shift),
  };
}

export class Partition {
  seq = 0n;
  private documents: Node<Document> | null = null;
  private ranking: Node<Document> | null = null;
  private terms = new Map<string, Bitmap>();
  private presence = new Map<string, Bitmap>();
  private enabled = new Bitmap();
  private bands = new Map<
    number,
    { lower: bigint; upper: bigint; bitmap: Bitmap }
  >();
  constructor(readonly shard: number) {}
  private bitmap(map: Map<string, Bitmap>, key: string) {
    let b = map.get(key);
    if (!b) map.set(key, (b = new Bitmap()));
    return b;
  }
  load(doc: Document) {
    if (
      !Number.isInteger(doc.doc_id) ||
      doc.doc_id < 1 ||
      doc.doc_id > 4294967295 ||
      doc.doc_id % 32 !== this.shard ||
      !/^\d+\.\d{8}$/.test(doc.score) ||
      typeof doc.index_revision !== "string" ||
      !/^\d+$/.test(doc.index_revision) ||
      !doc.attributes ||
      typeof doc.enabled !== "boolean" ||
      typeof doc.deleted !== "boolean"
    )
      throw failure("invalid_index_document", 503);
    for (const [field, values] of Object.entries(doc.attributes))
      if (
        !/^\d+$/.test(field) ||
        !Array.isArray(values) ||
        values.some((v) => typeof v !== "string" || !/^\d+$/.test(v))
      )
        throw failure("invalid_index_document", 503);
    const old = find(this.documents, doc, byId);
    if (old && BigInt(old.index_revision) >= BigInt(doc.index_revision)) return;
    const id = local(doc.doc_id);
    if (old) {
      for (const [f, values] of Object.entries(old.attributes)) {
        this.presence.get(f)?.delete(id);
        for (const term of values) {
          const b = this.terms.get(term);
          b?.delete(id);
          if (b && !b.size) this.terms.delete(term);
        }
      }
      this.enabled.delete(id);
      if (old.enabled && !old.deleted) {
        this.ranking = remove(this.ranking, old, compare);
        const key = band(old).key,
          b = this.bands.get(key)!;
        b.bitmap.delete(id);
        if (!b.bitmap.size) this.bands.delete(key);
      }
    }
    // Full immutable document versions are shared by persistent roots; bitmap mutations stay in this worker.
    this.documents = put(this.documents, doc, byId);
    if (!doc.deleted)
      for (const [f, values] of Object.entries(doc.attributes)) {
        if (values.length) this.bitmap(this.presence, f).add(id);
        for (const term of values) this.bitmap(this.terms, term).add(id);
      }
    if (doc.enabled && !doc.deleted) {
      this.enabled.add(id);
      this.ranking = put(this.ranking, doc, compare);
      const position = band(doc);
      let b = this.bands.get(position.key);
      if (!b)
        this.bands.set(
          position.key,
          (b = {
            lower: position.lower,
            upper: position.upper,
            bitmap: new Bitmap(),
          }),
        );
      b.bitmap.add(id);
    }
  }
  apply(seq: string, doc: Document) {
    if (BigInt(seq) !== this.seq + 1n) throw failure("index_sequence_gap", 503);
    this.load(doc);
    this.seq = BigInt(seq);
  }
  export() {
    return { seq: String(this.seq), documents: iterate(this.documents) };
  }
  cursor(
    conditions: Condition[],
    time: number,
    pageSize: number,
    cpuBudget = 50,
    memoryBudget = 8 * 1024 * 1024,
  ): Generator<Document> {
    let remaining = cpuBudget,
      last = performance.now();
    const check = () => {
      const now = performance.now();
      remaining -= now - last;
      last = now;
      if (remaining < 0) throw failure("query_budget_exceeded", 422);
    };
    const owned: Bitmap[] = [];
    let disposed = false;
    const own = (b: Bitmap) => {
      owned.push(b);
      return b;
    };
    const dispose = () => {
      if (disposed) return;
      disposed = true;
      for (const b of owned) b.clear();
    };
    try {
      const unions = new Map<Condition, Bitmap>();
      const positives: Bitmap[] = [];
      let temporary = 0;
      const bytes = (b: Bitmap) => {
        const s = b.statistics();
        return (
          s.bytesInArrayContainers +
          s.bytesInRunContainers +
          s.bytesInBitsetContainers +
          s.containers * 32 +
          64
        );
      };
      // Reserve space before native allocations, including a conservative native-operation scratch allowance.
      const reserve = (additional: number) => {
        if (temporary + additional * 2 > memoryBudget)
          throw failure("query_budget_exceeded", 422);
      };
      for (const c of conditions) {
        check();
        const p = this.presence.get(c.field) || new Bitmap();
        if (c.op === "exists") positives.push(p);
        if (c.op === "in" || c.op === "not_in") {
          const u = own(new Bitmap());
          for (const v of c.values || []) {
            const b = this.terms.get(v);
            if (b) {
              reserve(bytes(u) + bytes(b));
              u.orInPlace(b);
            }
            check();
          }
          temporary += bytes(u);
          reserve(0);
          unions.set(c, u);
          if (c.op === "in") positives.push(u);
          else if (c.missing !== "include") positives.push(p);
        }
      }
      positives.push(this.enabled);
      positives.sort((a, b) => a.size - b.size);
      reserve(bytes(positives[0]));
      const candidate = own(positives[0].clone());
      temporary += bytes(candidate);
      for (const p of positives.slice(1)) {
        candidate.andInPlace(p);
        check();
      }
      for (const c of conditions) {
        if (c.op === "not_in") candidate.andNotInPlace(unions.get(c)!);
        if (c.op === "missing")
          candidate.andNotInPlace(this.presence.get(c.field) || new Bitmap());
        check();
      }
      if (temporary > memoryBudget) throw failure("query_budget_exceeded", 422);
      const selectedBands: { lower: bigint; upper: bigint; bitmap: Bitmap }[] =
        [];
      if (candidate.size > 5000)
        for (const [, b] of [...this.bands].sort((a, b) => b[0] - a[0])) {
          reserve(bytes(candidate) + bytes(b.bitmap));
          const intersection = own(Bitmap.and(candidate, b.bitmap));
          check();
          if (intersection.size) {
            temporary += bytes(intersection);
            reserve(0);
            selectedBands.push({
              lower: b.lower,
              upper: b.upper,
              bitmap: intersection,
            });
          }
        }
      const documents = this.documents,
        ranking = this.ranking,
        shard = this.shard;
      const eligible = (d: Document) =>
        !d.starts_at || Date.parse(d.starts_at) <= time;
      const active = (d: Document) =>
        eligible(d) && (!d.ends_at || Date.parse(d.ends_at) > time);
      const small = candidate.size <= 5000;
      for (const u of unions.values()) u.clear();
      if (!small) candidate.clear();
      const iterator = (function* () {
        try {
          if (small) {
            let after: Document | undefined;
            while (true) {
              const source = function* () {
                for (const id of candidate) {
                  check();
                  const d = find(
                    documents,
                    { doc_id: id * 32 + shard } as Document,
                    byId,
                  )!;
                  if (active(d) && (!after || compare(d, after) > 0)) yield d;
                }
              };
              const batch = top(source(), pageSize, compare);
              if (!batch.length) return;
              for (const d of batch) {
                check();
                yield d;
                last = performance.now();
              }
              after = batch[batch.length - 1];
            }
          } else {
            let visited = 0;
            for (const b of selectedBands) {
              const from = {
                doc_id: Number.MAX_SAFE_INTEGER,
                score: decimal(b.upper),
              } as Document;
              for (const d of iterateFrom(ranking, from, compare)) {
                check();
                if (scoreValue(d) < b.lower) break;
                if (++visited > 20000)
                  throw failure("query_budget_exceeded", 422);
                if (b.bitmap.has(local(d.doc_id)) && active(d)) {
                  yield d;
                  last = performance.now();
                }
              }
            }
          }
        } finally {
          dispose();
        }
      })();
      const end = iterator.return.bind(iterator);
      iterator.return = (value: any) => {
        dispose();
        return end(value);
      };
      return iterator;
    } catch (e) {
      dispose();
      throw e;
    }
  }
}
