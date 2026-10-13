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
