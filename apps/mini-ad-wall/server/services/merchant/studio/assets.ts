import fs from "fs/promises";
import path from "path";
import os from "os";
import { createHmac, createHash, timingSafeEqual, randomUUID } from "crypto";
import { execFile } from "child_process";
import { promisify } from "util";
import { PoolConnection } from "mysql2/promise";
import config from "../../../config";
import { object, hash } from "./store";
import { resourceError as fail } from "../../marketing/resource.service";
const exec = promisify(execFile);
export const studioDir = () => {
  const dir = path.resolve(
    process.env.STUDIO_STORAGE_DIR ||
      path.resolve(config.UPLOAD_DIR, "../studio-private"),
  );
  const rel = path.relative(path.resolve(config.UPLOAD_DIR), dir);
  if (!rel || (!rel.startsWith("..") && !path.isAbsolute(rel)))
    throw fail(
      "unsafe_storage_directory",
      503,
      "工作台存储不得位于公开上传目录中",
    );
  return dir;
};
export async function probe(file: string) {
  try {
    const r = await exec(
      process.env.STUDIO_FFPROBE || "ffprobe",
      [
        "-v",
        "error",
        "-show_entries",
        "format=duration,format_name:stream=codec_type,width,height",
        "-of",
        "json",
        file,
      ],
      { timeout: 10000, maxBuffer: 65536 },
    );
    return JSON.parse(r.stdout);
  } catch {
    throw fail("invalid_asset", 422, "素材无法解码，需有效图片或60秒以内MP4");
  }
}
export async function storeUpload(buffer: Buffer, mime: string) {
  const image = buffer.subarray(0, 3).equals(Buffer.from([255, 216, 255]))
    ? "image/jpeg"
    : buffer
          .subarray(0, 8)
          .equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))
      ? "image/png"
      : buffer.toString("ascii", 0, 4) === "RIFF" &&
          buffer.toString("ascii", 8, 12) === "WEBP"
        ? "image/webp"
        : null;
  const video = buffer.toString("ascii", 4, 8) === "ftyp";
  if (!image && !video)
    throw fail("invalid_asset", 422, "只支持JPEG/PNG/WebP/MP4");
  if (image && buffer.length > 10 * 1024 * 1024)
    throw fail("asset_limit", 413, "图片超过10MiB");
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), "studio-upload-"));
  try {
    const f = path.join(temp, "asset");
    await fs.writeFile(f, buffer);
    const p = await probe(f),
      streams = p.streams || [];
    const v = streams.find((s: any) => s.codec_type === "video");
    if (!v || !v.width || !v.height || v.width * v.height > 20000000)
      throw fail("asset_limit", 422, "图片/视频尺寸不合法或超过2000万像素");
    if (
      video &&
      (!(Number(p.format?.duration) > 0) ||
        Number(p.format.duration) > 60 ||
        !String(p.format.format_name).includes("mp4"))
    )
      throw fail("asset_limit", 422, "视频必须是60秒以内MP4");
    const filename =
      randomUUID() +
      (image
        ? image === "image/jpeg"
          ? ".jpg"
          : image === "image/png"
            ? ".png"
            : ".webp"
        : ".mp4");
    await fs.mkdir(studioDir(), { recursive: true });
    await fs.copyFile(f, path.join(studioDir(), filename));
    return {
      filename,
      mime: image || "video/mp4",
      size: buffer.length,
      width: v.width,
      height: v.height,
      duration: video ? Number(p.format.duration) : null,
      hash: createHash("sha256").update(buffer).digest("hex"),
    };
  } finally {
    await fs.rm(temp, { recursive: true, force: true });
  }
}
export async function assetPath(c: PoolConnection, id: string, env: string) {
  const a = await object(c, id, "asset", env);
  if (path.basename(a.data.filename) !== a.data.filename)
    throw fail("invalid_asset", 422, "素材路径不合法");
  return { row: a, path: path.join(studioDir(), a.data.filename) };
}
export function signedAsset(id: string, environment: string) {
  const base = process.env.STUDIO_PUBLIC_BASE_URL || "",
    key = process.env.STUDIO_ASSET_SIGNING_KEY || "";
  if (!base.startsWith("https://") || key.length < 32)
    throw fail(
      "first_frame_unavailable",
      503,
      "图片首帧需要配置HTTPS公网地址及签名密钥",
    );
  const data = Buffer.from(
    JSON.stringify({ id, environment, exp: Date.now() + 30 * 60000 }),
  ).toString("base64url");
  const sig = createHmac("sha256", key).update(data).digest("base64url");
  return (
    base.replace(/\/$/, "") +
    "/api/merchant/v1/studio/public-assets/" +
    data +
    "." +
    sig
  );
}
export function verifyAsset(token: string) {
  const [data, sig, ...rest] = token.split("."),
    key = process.env.STUDIO_ASSET_SIGNING_KEY || "";
  if (!data || !sig || rest.length || key.length < 32 || token.length > 1000)
    throw fail("invalid_asset_token", 403, "素材凭证无效");
  const expected = createHmac("sha256", key).update(data).digest(),
    actual = Buffer.from(sig, "base64url");
  if (actual.length !== expected.length || !timingSafeEqual(actual, expected))
    throw fail("invalid_asset_token", 403, "素材凭证无效");
  let value: any;
  try {
    value = JSON.parse(Buffer.from(data, "base64url").toString());
  } catch {
    throw fail("invalid_asset_token", 403, "素材凭证无效");
  }
  if (
    !Number.isSafeInteger(value.exp) ||
    value.exp < Date.now() ||
    !/^[a-zA-Z0-9_-]{1,100}$/.test(value.id) ||
    !["synthetic", "sandbox", "production"].includes(value.environment)
  )
    throw fail("invalid_asset_token", 403, "素材凭证过期");
  return value;
}
