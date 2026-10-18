"""Same-model paired E4 regression. Public images only; no production mutation."""
import copy,json,random,time
from pathlib import Path
from evaluation.image_rag_provider import Provider,write,digest
from evaluation.run_visual_observation import INITIAL,READER,ROUTER,ANSWER,annotated_cases,image_content,validate_observations
from evaluation.visual_observation_v3 import READER_V3,ROUTE_V3,ANSWER_V3,answer_contract,checked_route,answer_errors
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/validation/visual-observation-ab/20261015-e4-seed21lite-v1'
MODEL='doubao-seed-2-1-lite-260915'

def main():
 OUT.mkdir(exist_ok=True,parents=True)
 if (OUT/'trace.jsonl').exists():raise RuntimeError('already attempted; do not silently retry')
 assets=json.loads((ROOT/'evaluation/visual_observation_v1/assets.json').read_text())['assets']
 by_id={a['asset_id']:a for a in assets}
 cases=[c for c in annotated_cases() if by_id[c['asset_id']]['split']=='test']
 for a in assets:
  if digest(Path(a['path']).read_bytes())!=a['sha256']:raise ValueError('image changed')
 write(OUT/'cases-gold.json',cases)
 write(OUT/'assets.json',assets)
 protocol={'model':MODEL,'arms':['old_C1','fixed_C1','always_read_B'],'cases':len(cases),'scope':'public images, known-image regression','same_model_initial_observations':True,'seed':20261015,'max_calls':260,'cost_ceiling_estimate_cny':15,'conservative_cny_per_million_total_tokens':24,'initial_cost_reported_separately':True,'final_answer_cache':False,'semantic_review':'pending','production_mutated':False,'runner_sha256':digest(Path(__file__).read_bytes())}
 write(OUT/'protocol.json',protocol)
 provider=Provider(OUT,260);rows=[];initial={};status='running';error=None
 def call(key,messages,tokens):
  receipts=[json.loads(p.read_text()) for p in (OUT/'receipts').glob('*.json') if not p.name.endswith('.response.json')]
  usage=sum((r.get('usage') or {}).get('total_tokens',0) for r in receipts)
  if usage*24/1e6+1>15:raise RuntimeError('conservative budget stop')
  value,receipt,replay=provider.call(OUT.name+':'+key,MODEL,messages,tokens)
  if replay:raise RuntimeError('unexpected replay')
  return value,receipt
 try:
  for aid in sorted({c['asset_id'] for c in cases}):
   value,r=call('initial:'+aid,[{'role':'system','content':INITIAL},{'role':'user','content':image_content(by_id[aid],json.dumps({'source_id':aid},ensure_ascii=False))}],2200)
   initial[aid]={'observations':validate_observations(value,aid,r['request_id'],'initial'),'receipt':r}
   write(OUT/'initial-observations.json',initial)
  states={arm:{aid:copy.deepcopy(v['observations']) for aid,v in initial.items()} for arm in ['old_C1','fixed_C1']}
  random.Random(20261015).shuffle(cases)
  for c in cases:
   aid=c['asset_id'];contract=answer_contract(c['question']);arms=list(protocol['arms']);random.Random(c['case_id']).shuffle(arms)
   for arm in arms:
    start=time.perf_counter();row={k:c[k] for k in ['case_id','asset_id','question']};row.update(arm=arm,status='failed',vision_called=False,route_ms=0,vision_ms=0,answer_ms=0)
    try:
     fixed=arm!='old_C1';evidence=copy.deepcopy(states[arm][aid]) if arm in states else [{'source_id':aid,'caption':by_id[aid]['archived_caption']}]
     row['evidence_before']=copy.deepcopy(evidence);read=arm=='always_read_B'
     payload={'question':c['question'],'source_id':aid,'observations':evidence}
     if fixed:payload['answer_contract']=contract
     if arm in states:
      route,r=call(c['case_id']+':'+arm+':route',[{'role':'system','content':ROUTE_V3 if fixed else ROUTER},{'role':'user','content':json.dumps(payload,ensure_ascii=False)}],550)
      row['route_ms']=r['elapsed_ms'];row['route']=checked_route(route,contract,evidence) if fixed else route;read=row['route']['decision']=='read'
     if read:
      row['vision_called']=True
      value,r=call(c['case_id']+':'+arm+':read',[{'role':'system','content':READER_V3 if fixed else READER},{'role':'user','content':image_content(by_id[aid],json.dumps({'question':c['question'],'source_id':aid},ensure_ascii=False))}],1600)
      row['vision_ms']=r['elapsed_ms'];obs=validate_observations(value,aid,r['request_id'],'supplement');evidence.extend(obs)
      if arm in states:states[arm][aid].extend(obs)
     row['evidence_after']=copy.deepcopy(evidence)
     payload={'question':c['question'],'source_name_map':{aid:by_id[aid]['name']},'allowed_source_ids':[aid],'evidence':evidence}
     if fixed:payload['answer_contract']=contract
     value,r=call(c['case_id']+':'+arm+':answer',[{'role':'system','content':ANSWER_V3 if fixed else ANSWER},{'role':'user','content':json.dumps(payload,ensure_ascii=False)}],600)
     row['answer_ms']=r['elapsed_ms'];row['answer']=value;row['format_errors']=answer_errors(value,contract,aid);row['status']='format_failed' if row['format_errors'] else 'succeeded'
    finally:
     row['total_ms']=(time.perf_counter()-start)*1000;rows.append(row)
     with (OUT/'trace.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    print(c['case_id'],arm,row['status'],round(row['total_ms']/1000,2),flush=True)
  status='completed'
 except Exception as exc:
  status='stopped';error=str(exc);print(status,error,flush=True)
 finally:
  provider.close();write(OUT/'execution.json',{'status':status,'error':error,'attempted_rows':len(rows),'model':MODEL})
  summarize(OUT)

def summarize(out):
 import numpy as np
 rows=[json.loads(s) for s in (out/'trace.jsonl').read_text().splitlines()] if (out/'trace.jsonl').exists() else []
 gold={c['case_id']:c['gold'] for c in json.loads((out/'cases-gold.json').read_text())}
 groups={}
 for arm in ['old_C1','fixed_C1','always_read_B']:
  subset=[r for r in rows if r['arm']==arm];positive=[r for r in subset if gold[r['case_id']]['answerable']]
  def field(r):
   a=r.get('answer',{});v=a.get('value');expected=gold[r['case_id']]['expected']
   ok=v is expected if isinstance(expected,bool) else isinstance(v,str) and any(x.lower() in v.lower() for x in expected)
   return r['status']=='succeeded' and a.get('status')=='answered' and ok and r['asset_id'] in a.get('source_ids',[])
  groups[arm]={'attempted':len(subset),'answerable':len(positive),'field_source_pass':sum(field(r) for r in positive),'format_pass':sum(r.get('answer') is not None and not r.get('format_errors') for r in subset),'reads':sum(r['vision_called'] for r in subset),'p95_s':float(np.percentile([r['total_ms']/1000 for r in subset],95)) if subset else None,'body_correctness':'pending_independent_review','citation_support':'pending_independent_review'}
 receipts=[json.loads(p.read_text()) for p in (out/'receipts').glob('*.json') if not p.name.endswith('.response.json')]
 usage=sum((r.get('usage') or {}).get('total_tokens',0) for r in receipts)
 write(out/'summary.json',{'groups':groups,'requests':len(receipts),'total_tokens':usage,'conservative_cost_estimate_cny':usage*24/1e6,'billing_confirmed':False,'initial_costs':[v['receipt'] for v in json.loads((out/'initial-observations.json').read_text()).values()] if (out/'initial-observations.json').exists() else []})
 (out/'report.md').write_text('# E4 Seed 2.1 lite 同期对照\n\n'+json.dumps(groups,ensure_ascii=False,indent=2)+'\n\n正文正确性和引用支撑尚待独立审查。字段通过不是正文正确率。仅完成全部同题组后才能对照；新模型结果不与旧 Seed mini 混算。费用为保守估算，非账单。初始观察费用单列 summary.json。\n')
if __name__=='__main__':main()
