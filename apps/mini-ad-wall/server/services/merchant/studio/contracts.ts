import Ajv from "ajv";
import fs from "fs";
import path from "path";
import { resourceError } from "../../marketing/resource.service";
let root = __dirname;
while (!fs.existsSync(path.join(root, "contracts/merchant/studio/v1"))) {
  const parent = path.dirname(root);
  if (parent === root) throw Error("studio_contracts_missing");
  root = parent;
}
const dir = path.join(root, "contracts/merchant/studio/v1");
const ajv = new Ajv({ strict: false, allErrors: true });
const definitions = JSON.parse(
  fs.readFileSync(path.join(dir, "request.schema.json"), "utf8"),
);
const validators = Object.fromEntries(
  Object.entries(definitions).map(([name, s]) => [name, ajv.compile(s as any)]),
);
export const validateResult = ajv.compile<Record<string, any>>(
  JSON.parse(fs.readFileSync(path.join(dir, "result.schema.json"), "utf8")),
);
export function requireInput(name: string, value: any) {
  if (
    !validators[name]?.(value) ||
    Buffer.byteLength(JSON.stringify(value)) > 65536
  )
    throw resourceError("invalid_input", 422, "请求字段或长度不合法");
}
export const validateUnderstanding=ajv.compile<Record<string,any>>(JSON.parse(fs.readFileSync(path.join(dir,'understanding.schema.json'),'utf8')));
export const validateMediaRag=ajv.compile<Record<string,any>>(JSON.parse(fs.readFileSync(path.join(dir,'media-rag.schema.json'),'utf8')));
const visualSchema = ajv.compile<any[]>(JSON.parse(fs.readFileSync(path.join(dir,'visual-embeddings.schema.json'),'utf8')));
export function validateVisualEmbeddings(value: any): value is any[] {
  return !!visualSchema(value) && new Set(value.map((v:any)=>v.frame_index)).size === value.length &&
    value.every((v:any)=>Math.abs(Math.sqrt(v.vector.reduce((s:number,x:number)=>s+x*x,0))-1)<0.002);
}
export function validateVisualFrames(value: any, frames: {data_url:string,time_ms:number|null}[]): value is any[] {
  const {createHash} = require('crypto');
  return validateVisualEmbeddings(value) && value.length === frames.length && value.every((v:any,i:number)=>
    v.frame_index === i && v.time_ms === frames[i].time_ms &&
    v.image_sha256 === createHash('sha256').update(Buffer.from(frames[i].data_url.split(',')[1], 'base64')).digest('hex'));
}
export function nativeMediaRag(): boolean {
  const mode = process.env.STUDIO_MEDIA_RAG_MODE || 'native';
  if (!['native','caption'].includes(mode)) throw Error('invalid_media_rag_mode');
  return mode === 'native';
}
