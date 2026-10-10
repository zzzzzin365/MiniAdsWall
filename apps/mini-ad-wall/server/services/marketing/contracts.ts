import fs from 'fs';
import path from 'path';
import Ajv from 'ajv';
import addFormats from 'ajv-formats';

function contractRoot() {
    for (let root = __dirname; ; root = path.dirname(root)) {
        const candidate = path.join(root, 'contracts/marketing/v1/schema.json');
        if (fs.existsSync(candidate)) return candidate;
        if (path.dirname(root) === root) throw new Error('marketing_contract_missing');
    }
}
const schema = JSON.parse(fs.readFileSync(contractRoot(), 'utf8'));
const ajv = new Ajv({ allErrors: true, strict: true, coerceTypes: false });
addFormats(ajv);
ajv.addSchema(schema);
export function validContract(name: string, value: unknown): boolean {
    const validate = ajv.getSchema(`${schema.$id}#/definitions/${name}`);
    if (!validate) throw new Error('unknown_marketing_contract');
    return !!validate(value);
}
export function requireContract(name: string, value: unknown): void {
    if (!validContract(name, value)) throw Object.assign(new Error('请求格式不符合营销 v1 协议'), { status: 400, code: 'invalid_request' });
}
