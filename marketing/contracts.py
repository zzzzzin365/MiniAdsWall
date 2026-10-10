"""The same draft-07 contract used by Koa; reject unknown fields and coercion."""
import json
from pathlib import Path
from jsonschema import Draft7Validator, FormatChecker

_schema = json.loads((Path(__file__).resolve().parents[1] / 'contracts/marketing/v1/schema.json').read_text())
Draft7Validator.check_schema(_schema)

def valid_contract(name, value):
    if name not in _schema['definitions']:
        raise ValueError('unknown_marketing_contract')
    local = {**_schema, '$ref': '#/definitions/' + name}
    return Draft7Validator(local, format_checker=FormatChecker()).is_valid(value)

def require_contract(name, value):
    if not valid_contract(name, value):
        raise ValueError('invalid_marketing_request')
