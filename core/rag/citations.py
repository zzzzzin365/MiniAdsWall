"""Validate structure, source identities and exact quotations; not truthfulness."""
import json
from urllib.parse import urlparse
from core.rag.service import flag


def enabled(chunks):
    return bool(chunks) and any(c.get('generation_id') for c in chunks) and flag('RAG_V2_CITATIONS_ENABLED')


def instruction(chunks, business_records=()):
    return ('\n最终输出必须是一个 JSON 对象，不要 Markdown 代码围栏。格式：'
        '{"status":"answered或insufficient","answer_blocks":[{"text":"中文回答",'
        '"evidence_ids":["本轮证据ID"],"quote":null}],"missing_information":[]}。'
        '每个回答块必须引用本轮证据；直接摘录时 quote 写连续原文，否则 null。'
        '业务事实引用 business，并在该块 tool_record_ids 中列本轮工具记录名称。'
        '资料不足时 answer_blocks 为空，missing_information 列具体缺项。'
        '只输出以上 JSON，优先于商家阅读排版要求。可用证据ID：'
        + json.dumps([c['id'] for c in chunks], ensure_ascii=False)
        + '；可用业务记录：' + json.dumps(sorted(business_records), ensure_ascii=False))


def validate_and_render(raw, chunks, *, business_records=(), revalidate=None):
    if revalidate:
        chunks = revalidate(chunks)
    sources = {c['id']: c for c in chunks}
    records = set(business_records)
    obj = json.loads(raw)
    if not isinstance(obj, dict) or set(obj) != {'status', 'answer_blocks', 'missing_information'}:
        raise ValueError('invalid_answer_object')
    blocks, missing = obj['answer_blocks'], obj['missing_information']
    if (not isinstance(blocks, list) or len(blocks) > 20 or not isinstance(missing, list)
            or len(missing) > 10 or any(not isinstance(x,str) or not x.strip() or len(x)>300 for x in missing)):
        raise ValueError('invalid_answer_fields')
    if obj['status'] == 'insufficient' and missing and not blocks:
        return '资料不足：' + '；'.join(missing)
    if obj['status'] != 'answered' or missing or not blocks:
        raise ValueError('inconsistent_answer')
    used, output = set(), []
    for block in blocks:
        if not isinstance(block, dict) or set(block) - {'text','evidence_ids','quote','tool_record_ids'}:
            raise ValueError('invalid_answer_block')
        text, ids = block.get('text'), block.get('evidence_ids')
        if (not isinstance(text,str) or not text.strip() or len(text)>3000 or not isinstance(ids,list)
                or not ids or any(not isinstance(cid,str) for cid in ids) or len(set(ids)) != len(ids)):
            raise ValueError('invalid_answer_block')
        for cid in ids:
            if cid == 'business':
                tool_ids = block.get('tool_record_ids')
                if (not isinstance(tool_ids,list) or not tool_ids
                        or any(not isinstance(t,str) or t not in records for t in tool_ids)):
                    raise ValueError('unknown_business_record')
            elif cid not in sources:
                raise ValueError('unknown_or_revoked_citation')
            else:
                used.add(cid)
        quote = block.get('quote')
        if quote is not None and (not isinstance(quote,str) or not quote or
                not any(quote in sources[cid]['content'] for cid in ids if cid in sources)):
            raise ValueError('invalid_exact_quote')
        output.append(text)
    labels = []
    for cid in sorted(used):
        source = sources[cid]
        url = source.get('source_url', '')
        title = source.get('title') or '知识片段'
        # Plain text titles avoid Markdown injection through source metadata.
        title = title.replace('\n', ' ').replace('[','').replace(']','')
        valid_url = urlparse(url)
        labels.append(title + (' ' + url if valid_url.scheme in {'https','http'} and valid_url.netloc else ''))
    return '\n\n'.join(output) + ('\n\n参考依据：\n' + '\n'.join(dict.fromkeys(labels)) if labels else '')


def failure_response():
    return '本轮回答未通过来源校验，请重试或查看知识库原文。'
