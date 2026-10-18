"""Paired repeated public-image E3. Only visual-read policy varies; no answer cache."""
import json,random,time
from pathlib import Path
import numpy as np
from evaluation.image_rag_provider import Provider,write,digest
from evaluation.run_visual_observation import annotated_cases,image_content,validate_observations
from evaluation.visual_observation_v3 import READER_V3,ANSWER_V3,answer_errors
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/validation/visual-observation-ab/20261016-e3-seed21lite-v1'
MODEL='doubao-seed-2-1-lite-260915'

def report():
 rows=[json.loads(s) for s in (OUT/'trace.jsonl').read_text().splitlines()] if (OUT/'trace.jsonl').exists() else []
 gold={c['case_id']:c['gold'] for c in annotated_cases()}
 def quality(r):
  a=r.get('answer') or {};v=a.get('value');g=gold[r['case_id']]['expected']
  if g is None:return a.get('status')=='insufficient' and v is None and not r.get('format_errors')
  ok=v is g if isinstance(g,bool) else isinstance(v,str) and any(x.lower() in v.lower() for x in g)
  return r['status']=='succeeded' and a.get('status')=='answered' and ok and r['asset_id'] in a.get('source_ids',[])
 matched={k for k in {(r['repeat'],r['case_id']) for r in rows} if len([r for r in rows if (r['repeat'],r['case_id'])==k])==2}
 paired=[r for r in rows if (r['repeat'],r['case_id']) in matched]
 def stats(rs):
  def percent(metric,p):return float(np.percentile([r[metric]/1000 for r in rs],p)) if rs else None
  return {'n':len(rs),'read_calls':sum(r['vision_called'] for r in rs),'field_source_pass':sum(quality(r) for r in rs),'format_pass':sum(r.get('answer') is not None and not r.get('format_errors') for r in rs),'tokens':sum(r.get('total_tokens',0) for r in rs),'conservative_cost_cny':sum(r.get('total_tokens',0) for r in rs)*24/1e6,**{metric:{'p50_s':percent(metric,50),'p95_s':percent(metric,95)} for metric in ['total_ms','reader_ms','generation_ms']}}
 groups={group:{arm:stats([r for r in paired if r['arm']==arm and (group=='all' or r['group']==group)]) for arm in ['H','R']} for group in ['all','requires_visual_detail','caption_or_unprovable']}
 rounds={str(rep):{g:{arm:stats([r for r in paired if r['repeat']==rep and r['arm']==arm and (g=='all' or r['group']==g)]) for arm in ['H','R']} for g in groups} for rep in range(3)}
 rng=np.random.default_rng(20261016);ci={}
 for group in groups:
  rs=[r for r in paired if group=='all' or r['group']==group];ids=sorted({r['case_id'] for r in rs});d=[]
  if ids:
   for _ in range(2000):
    sample=[r for i in rng.choice(ids,len(ids)) for r in rs if r['case_id']==i]
    d.append(float(np.percentile([r['total_ms']/1000 for r in sample if r['arm']=='R'],95)-np.percentile([r['total_ms']/1000 for r in sample if r['arm']=='H'],95)))
  ci[group]={'p95_delta_R_minus_H_ci95_s':list(np.percentile(d,[2.5,97.5])) if d else None,'resampling_unit':'case_id with all repeats and paired arms; exploratory small sample'}
 receipts=[json.loads(p.read_text()) for p in (OUT/'receipts').glob('*.json') if not p.name.endswith('.response.json')]
 summary={'attempted_rows':len(rows),'paired_question_repeats':len(matched),'groups':groups,'rounds':rounds,'cluster_bootstrap':ci,'requests':len(receipts),'usage_tokens':sum((r.get('usage') or {}).get('total_tokens',0) for r in receipts),'all_receipts_succeeded':all(r['status']=='succeeded' for r in receipts),'body_review':'pending','billing_confirmed':False}
 write(OUT/'summary.json',summary)
 lines=['# E3 分组重复测量：Seed 2.1 lite','','12道固定公开图问题，4张图；每组各6题，3轮新请求；H每题读图，R仅视觉细节组读图。两组使用相同提示、模型、Token限制和评分。','',f'完成 {len(rows)}/72题次；完整配对 {len(matched)}/36题轮。模型：{MODEL}。','','| 问题组 | 策略 | 题次 | 读图 | 字段＋来源通过 | 格式通过 | P50秒 | P95秒 | Token |','|---|---|---:|---:|---:|---:|---:|---:|---:|']
 for group,arms in groups.items():
  for arm,s in arms.items():lines.append(f"| {group} | {arm} | {s['n']} | {s['read_calls']} | {s['field_source_pass']}/{s['n']} | {s['format_pass']}/{s['n']} | {s['total_ms']['p50_s'] or 0:.2f} | {s['total_ms']['p95_s'] or 0:.2f} | {s['tokens']} |")
 lines+=['','## 每轮完整耗时P95（秒）','','| 轮次 | 问题组 | H | R |','|---|---|---:|---:|']
 for rep,data in rounds.items():
  for g,a in data.items():lines.append(f"| {int(rep)+1} | {g} | {a['H']['total_ms']['p95_s'] or 0:.2f} | {a['R']['total_ms']['p95_s'] or 0:.2f} |")
 lines+=['','## 解释边界','','- 不需读图组包含3道已有描述覆盖题和3道原图无法核验的业务事实题；拒答不是视觉理解成功。两类题原样保留，不动态按结果分组。','- 两组每题均从相同归档描述开始，无观察累积、无答案缓存，无跨轮状态继承。题序与题内组别固定随机，串行执行。','- 这是证据准备＋回答的耗时，不包括线上知识检索、排队与上传，不代表全链路生产P95。reader_ms对跳过读图的请求为0；读图条件分布可从trace单独统计。','- P95按numpy线性分位数计算；每个分组每轮只有6题，重复3轮不增加独立图片数。按题聚类bootstrap区间仅作探索，不证明泛化和稳定性。','- 正文正确性和引用支撑尚未独立审查。自动字段通过率不是最终答案正确率。','- Token按供应商回执统计；费用按全部Token每百万24元的保守估算比较，不是实际账单。每次请求前预留1元，估算累计上限8元；单请求真实收费不由脚本控制。','- 不和历史商家图或Seed mini实验拼接成绩；未修改业务模型配置。',f"- 调用 {len(receipts)} 次，Token {summary['usage_tokens']}，保守费用估算 ¥{summary['usage_tokens']*24/1e6:.3f}。"]
 for g,c in ci.items():lines.append(f"- {g}：P95差值R−H的题目聚类95%区间（秒）：{c['p95_delta_R_minus_H_ci95_s']}。")
 (OUT/'report.md').write_text('\n'.join(lines)+'\n')
 print(json.dumps(groups,ensure_ascii=False),flush=True)

def main():
 OUT.mkdir(parents=True,exist_ok=True)
 if (OUT/'trace.jsonl').exists() or (OUT/'receipts').exists():raise RuntimeError('already attempted; do not blindly retry')
 cases=json.loads((ROOT/'docs/validation/visual-observation-ab/20261015-e3-e4-fix-v3/e3/requests.json').read_text())
 assets=json.loads((ROOT/'evaluation/visual_observation_v1/assets.json').read_text())['assets'];by_id={a['asset_id']:a for a in assets}
 for a in assets:
  if digest(Path(a['path']).read_bytes())!=a['sha256']:raise ValueError('changed image')
 for i,c in enumerate(cases):c['group']='caption_or_unprovable' if i<6 else 'requires_visual_detail'
 write(OUT/'requests.json',cases);write(OUT/'assets.json',assets)
 write(OUT/'protocol.json',{'model':MODEL,'repeats':3,'cases':12,'arms':['H','R'],'seed':20261016,'max_calls':181,'budget_conservative_cny':8,'only_variable':'visual read policy','concurrency':1,'source_hashes':{str(p.relative_to(ROOT)):digest(p.read_bytes()) for p in [Path(__file__),ROOT/'evaluation/image_rag_provider.py',ROOT/'evaluation/run_visual_observation.py',ROOT/'evaluation/visual_observation_v3.py']},'prompts':{'reader':digest(READER_V3.encode()),'answer':digest(ANSWER_V3.encode())},'images':{a['asset_id']:a['sha256'] for a in assets},'no_production_changes':True})
 p=Provider(OUT,181);status='running';error=None;rows=[]
 def call(key,messages,tokens):
  used=sum((json.loads(f.read_text()).get('usage') or {}).get('total_tokens',0) for f in (OUT/'receipts').glob('*.json') if not f.name.endswith('.response.json'))
  if used*24/1e6+1>8:raise RuntimeError('budget stop')
  v,r,replay=p.call(OUT.name+':'+key,MODEL,messages,tokens)
  if replay:raise RuntimeError('unexpected replay')
  return v,r
 try:
  call('preflight',[{'role':'user','content':'只返回JSON：{"available":true}'}],40)
  for rep in range(3):
   order=list(cases);rng=random.Random(20261016+rep);rng.shuffle(order)
   for c in order:
    arms=['H','R'];rng.shuffle(arms)
    for arm in arms:
     aid=c['asset_id'];row={**c,'repeat':rep,'arm':arm,'status':'failed','vision_called':False,'reader_ms':0,'generation_ms':0,'total_tokens':0,'receipt_ids':[]};evidence=[{'source_id':aid,'caption':by_id[aid]['archived_caption']}]
     start=time.perf_counter()
     try:
      if arm=='H' or c['group']=='requires_visual_detail':
       row['vision_called']=True
       v,r=call(f'{rep}:{c["case_id"]}:{arm}:read',[{'role':'system','content':READER_V3},{'role':'user','content':image_content(by_id[aid],json.dumps({'source_id':aid,'question':c['question'],'answer_contract':c['answer_contract']},ensure_ascii=False))}],1600)
       row['reader_ms']=r['elapsed_ms'];row['total_tokens']+=(r.get('usage') or {}).get('total_tokens',0);row['receipt_ids'].append(r['request_id']);evidence.extend(validate_observations(v,aid,r['request_id'],'supplement'))
      v,r=call(f'{rep}:{c["case_id"]}:{arm}:answer',[{'role':'system','content':ANSWER_V3},{'role':'user','content':json.dumps({'question':c['question'],'answer_contract':c['answer_contract'],'source_name_map':{aid:by_id[aid]['name']},'allowed_source_ids':[aid],'evidence':evidence},ensure_ascii=False)}],600)
      row['generation_ms']=r['elapsed_ms'];row['total_tokens']+=(r.get('usage') or {}).get('total_tokens',0);row['receipt_ids'].append(r['request_id']);row['answer']=v;row['evidence']=evidence;row['format_errors']=answer_errors(v,c['answer_contract'],aid);row['status']='format_failed' if row['format_errors'] else 'succeeded'
     finally:
      row['total_ms']=(time.perf_counter()-start)*1000;rows.append(row)
      with (OUT/'trace.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
     print(rep+1,c['case_id'],arm,row['status'],round(row['total_ms']/1000,2),flush=True)
  status='completed'
 except Exception as e:status='stopped';error=str(e);print(status,error,flush=True)
 finally:
  p.close();write(OUT/'execution.json',{'status':status,'error':error,'rows':len(rows)});report()
if __name__=='__main__':main()
