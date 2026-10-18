"""Score external observation episodes and report paired, scoped measurements."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import numpy as np

def read(p):return json.loads(p.read_text())
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2))
def norm(s):return re.sub(r'\s+',' ',str(s).lower()).strip()
def field_matches(value,expected):
    if isinstance(expected,bool):return value is expected
    if value is None or isinstance(value,bool):return False
    return isinstance(value,(str,int,float)) and any(norm(alias) in norm(value) for alias in expected)
def main(out):
    gold={x['case_id']:x for x in read(out/'cases-gold.json')}
    rows=[json.loads(x) for x in (out/'trace.jsonl').read_text().splitlines()]
    scores=[]
    for r in rows:
        g=gold[r['case_id']]['gold'];a=r.get('answer',{});expected=g['expected'];v=a.get('value')
        if expected is None:correct=a.get('status')=='insufficient' and v is None
        else:correct=a.get('status')=='answered' and field_matches(v,expected)
        citations=bool(r['asset_id'] in a.get('source_ids',[])) if g['answerable'] else True
        scores.append({**r,'field_pass':bool(correct and r['status']=='succeeded'),'field_and_source_pass':bool(correct and citations and r['status']=='succeeded'),
            'gold_answerable':g['answerable'],'expected':expected,'support_region':g['support_region'],
            'human_review_status':'pending','score_limit':'word/boolean checks and required source, not full semantic citation review'})
    write(out/'scores.json',scores)
    def summarize(sub):
        result={}
        for arm in ['A','B','C0','C1']:
            rs=[r for r in sub if r['arm']==arm];ans=[r for r in rs if r['gold_answerable']];neg=[r for r in rs if not r['gold_answerable']]
            if not rs:continue
            result[arm]={'n':len(rs),'answerable_n':len(ans),'field_and_source_pass':float(np.mean([r['field_and_source_pass'] for r in ans])) if ans else None,
                'all_tasks_pass':float(np.mean([r['field_and_source_pass'] for r in rs])),
                'not_provable_correct':float(np.mean([r['field_and_source_pass'] for r in neg])) if neg else None,
                'vision_calls':sum(r['vision_called'] for r in rs),'failures':sum(r['status']!='succeeded' for r in rs),
                'p50_seconds':float(np.percentile([r['total_ms']/1000 for r in rs],50)),
                'p95_seconds':float(np.percentile([r['total_ms']/1000 for r in rs],95)),
                'evidence_p95_seconds':float(np.percentile([r.get('evidence_ready_ms',r['total_ms'])/1000 for r in rs],95)),
                'router_total_seconds':sum(r.get('route_ms',0) for r in rs)/1000}
        return result
    all_test=[r for r in scores if r['episode']=='main' and r['split']=='test']
    case_arms={k:{r['arm'] for r in all_test if r['case_id']==k} for k in {r['case_id'] for r in all_test}}
    matched={k for k,arms in case_arms.items() if arms=={'A','B','C0','C1'}}
    test=[r for r in all_test if r['case_id'] in matched];repeated=[r for r in scores if r['episode']!='main']
    summary={'status':'completed' if read(out/'manifest.json')['status']=='completed' else 'partial',
             'test':summarize(test),'development':summarize([r for r in scores if r['split']=='development']),
             'repeated':summarize(repeated),'grading':'automated field + source diagnostics; independent semantic review not completed',
             'scope':'external public images only','money':None}
    summary['test_coverage']={'all_observed_rows':len(all_test),'matched_cases':len(matched),'unmatched_rows':len(all_test)-len(test),
       'all_observed_failures':sum(r['status']!='succeeded' for r in all_test),
       'comparison_rule':'compare only cases whose four arms were attempted, including failed arms; all rows retained in scores/trace'}
    protocol=read(out/'protocol.json')
    summary['models']={'vision':protocol['vision_model'],'answer':protocol['text_model'],'router':protocol.get('router_model',protocol['text_model'])}
    summary['initial_observations_import']=read(out/'manifest.json').get('initial_observations_import')
    assets={a['asset_id']:a for a in read(out/'assets.json')};rng=np.random.default_rng(20261015)
    if test:
        families=sorted({assets[r['asset_id']]['family_id'] for r in test})
        groups=[[r for r in test if assets[r['asset_id']]['family_id']==f] for f in families]
        quality=[];speed=[]
        for _ in range(10000):
            sampled=[r for i in rng.integers(0,len(groups),len(groups)) for r in groups[i]]
            b=[r for r in sampled if r['arm']=='B'];c=[r for r in sampled if r['arm']=='C1']
            quality.append(np.mean([r['field_and_source_pass'] for r in c if r['gold_answerable']])-np.mean([r['field_and_source_pass'] for r in b if r['gold_answerable']]))
            speed.append(np.percentile([r['total_ms'] for r in c],95)/np.percentile([r['total_ms'] for r in b],95))
        summary['paired']={'families':len(groups),'C1_minus_B_quality_ci95':np.percentile(quality,[2.5,97.5]).tolist(),
            'C1_over_B_p95_ci95':np.percentile(speed,[2.5,97.5]).tolist(),'limits':f'{len(groups)} small families; exploratory intervals'}
    receipts=[read(p) for p in (out/'receipts').glob('*.json') if not p.name.endswith('.response.json')]
    usage={}
    for r in receipts:
        u=usage.setdefault(r['model'],{'requests':0,'prompt_tokens':0,'completion_tokens':0,'failed':0})
        u['requests']+=1;u['failed']+=r['status']!='succeeded'
        for k in ['prompt_tokens','completion_tokens']:u[k]+=r.get('usage',{}).get(k,0)
    summary['model_usage']=usage;write(out/'summary.json',summary)
    # Image-level cumulative cost includes each setup cost independently for C0/C1.
    init=read(out/'initial-observations.json');cumulative=[]
    receipt_by_id={r['request_id']:r for r in receipts}
    for aid,asset in assets.items():
        for arm in ['A','B','C0','C1']:
            rr=sorted([r for r in test if r['asset_id']==aid and r['arm']==arm],key=lambda r:r['question_number'])
            prep=init[aid]['receipt'] if arm in ['C0','C1'] else None
            wall=prep['elapsed_ms'] if prep else 0;tokens=sum((prep.get('usage') or {}).get(k,0) for k in ['prompt_tokens','completion_tokens']) if prep else 0
            for n,r in enumerate(rr,1):
                wall+=r['total_ms'];tokens+=sum(sum(receipt_by_id[i].get('usage',{}).get(k,0) for k in ['prompt_tokens','completion_tokens']) for i in r['receipt_ids'])
                cumulative.append({'asset_id':aid,'arm':arm,'questions':n,'setup_plus_online_seconds':wall/1000,'total_tokens':tokens,'money':None})
    write(out/'cumulative-cost.json',cumulative)
    lines=['# 外部案例：视觉观察复用实验报告','','日期：2026-10-15。仅外部案例图片；未读取商家素材、未改生产索引。',
        '',f'状态：{summary["status"]}，已保存{len(rows)}/240题次，未执行题不计正确或错误；实际完成数见表。',
        '部分运行的主比较只用四组均已尝试的相同题（失败照计），不按成功与否筛题；尾部未配齐题次单列保留：'+json.dumps(summary['test_coverage'],ensure_ascii=False),
        '', '**以下是自动字段＋必要来源诊断，尚未完成人工盲审，不能直接称每句话都有依据或正式质量验收通过。**',
        '', '## 设置','', '计划6张归档外部图片、5个来源家族；Semrush单图6题开发，其余5张图、4家族30题测试。每图6个不同问题：2基础、2细节、1明确存在/不存在、1原图不能证明。计划主测试25题可回答、5题缺事实，实际覆盖见主表与逐题日志。金标准为本助手看原图后、运行前固定，非独立人工标注。',
        '', 'A=归档描述；B=每题读原图；C0=只读冻结通用观察；C1=根据已有观察判缺项，必要时补读并追加观察。路由模型、视觉模型及回答模型：'+json.dumps(summary['models'],ensure_ascii=False)+'，全部在线调用计时计费。',
        '通用观察不接收问题或gold；模型请求只接收去gold的请求。无整句问题视觉缓存、无答案缓存、无查询向量缓存。C0/C1共用同一初始观察，但状态各自隔离。',
        '', '## 主测试（同图不同新问题）','', '| 组 | 可回答字段＋来源通过 | 缺事实题正确克制 | Seed在线读图 | 完整P50秒 | 完整P95秒 |', '|---|---:|---:|---:|---:|---:|']
    for arm,s in summary['test'].items():lines.append(f'| {arm} | {s["field_and_source_pass"]:.2%} | {s["not_provable_correct"]:.2%} | {s["vision_calls"]} | {s["p50_seconds"]:.2f} | {s["p95_seconds"]:.2f} |')
    if 'B' in summary['test'] and 'C1' in summary['test']:
        b=summary['test']['B'];c=summary['test']['C1'];lines+=['',f'C1相对B：诊断通过率差值 {(c["field_and_source_pass"]-b["field_and_source_pass"])*100:.2f} 个百分点；P95变化 {(c["p95_seconds"]/b["p95_seconds"]-1)*100:.2f}%；在线读图减少 {(1-c["vision_calls"]/b["vision_calls"])*100:.2f}%。',
        '这不是相对A的双提升。C0−A还包括新增初始理解的信息量，不能独归因于结构化格式。',
        '', '探索性配对区间：'+json.dumps(summary.get('paired'),ensure_ascii=False)]
    lines+=['', '## 成本与重复诊断','', '初始观察的每图Seed收据单列；cumulative-cost.json按每图N问把初始费用完整加回C0/C1，没有把它除给两组。若从旧批次导入相同Seed初始观察，初始usage是继承成本，新批次model_usage仅统计新提交，二者不重复收费。金额未查账单，money=null；tokens包括提供方报告的图片输入。重复诊断计划对汽车组件图与课程图各再运行两次、反向问题顺序，实际覆盖以repeated统计为准；每次重置C1状态，不复用最终答案或问题读图结果。',
            '', '重复诊断：'+json.dumps(summary['repeated'],ensure_ascii=False), '', '模型请求与usage：'+json.dumps(usage,ensure_ascii=False),
            '', '## 逐题差异与人工复核入口','', '| 题号 | A | B | C0 | C1 | C1路由 |','|---|---|---|---|---|---|']
    for case in [c for c in gold.values() if c['split']=='test']:
        rs={r['arm']:r for r in test if r['case_id']==case['case_id']}
        lines.append('| '+case['case_id']+' | '+' | '.join('未执行' if arm not in rs else '通过' if rs[arm]['field_and_source_pass'] else '未通过' for arm in ['A','B','C0','C1'])+' | '+str((rs.get('C1',{}).get('route') or {}).get('decision',''))+' |')
    lines+=['', '## 证据边界','', '测试只有4个来源家族，25题里1题占4个百分点；不能可靠证明5个百分点非劣。每题每组1次主运行，P95受API抖动影响；重复episode不增加独立图片数量。所有错误、超时留在分母。',
            '字段别名匹配不能自动识别否定、无关附加事实或不完整关系。scores.json保留每题原图支撑区域、答案和expected供人工复核，人工审查完成前不宣布正式质量达标。',
            '本轮固定正确图片，不证明向量召回提升；没有视频、音频、视觉embedding或私有素材。观察更新无独立no-update组，不能把组合收益单独归功于更新。',
            '', '复查：manifest.json、requests.json（无gold）、cases-gold.json、initial-observations.json、trace.jsonl、scores.json、receipts/、summary.json、cumulative-cost.json。']
    (out/'report.md').write_text('\n'.join(lines)+'\n');print(json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args();main(Path(a.output))
