"""Held-out public bracelet-image E4 extension; fixed inputs, no production writes."""
import copy,json,random,time,hashlib
from pathlib import Path
from PIL import Image,ImageOps
import numpy as np
from evaluation.image_rag_provider import Provider,VISION_MODEL,write,digest
from evaluation.run_visual_observation import INITIAL,image_content,validate_observations
from evaluation.visual_observation_v3 import READER_V3,ROUTE_V3,ANSWER_V3,answer_contract,checked_route,answer_errors
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/validation/visual-observation-ab/20261016-bracelets-seedmini-v1'
# Pre-inference visual annotation by the primary assistant from seven contact sheets.
# Not human annotations; no test-model output was available when these were set.
LABELS={0:('绿',True),3:('白',False),8:('白',False),13:('白',False),16:('白',False),22:('白',False),24:('黑',True),27:('白',False),32:('灰',True),34:('白',False),35:('灰',False),37:('黑|灰',True),39:('蓝',True),40:('棕|褐|灰',False),41:('白|浅灰',False),42:('白|浅紫',False),44:('白',False),47:('黄|金|棕|褐',True),49:('白|灰',False),51:('绿',True),53:('白|米',False)}
OVERRIDE={24:'手腕后方的衣物主要是什么颜色？',37:'手掌后方的汽车内饰主要是什么颜色？',53:'手串下方承托布主要是什么颜色？'}

def prepare():
 data=json.loads((ROOT/'evaluation/visual_observation_bracelets_v1/assets.json').read_text());assets=data['assets'];chosen={};public=[];gold=[]
 OUT.mkdir(parents=True,exist_ok=True);(OUT/'images').mkdir(exist_ok=True)
 dev={a['family_id'] for a in assets if a['split']=='development'}
 assert not dev & {a['family_id'] for a in assets if a['split']=='test'}
 for i,a in enumerate(assets):
  if digest(Path(a['path']).read_bytes())!=a['sha256']:raise ValueError('source image changed')
  if a['split']!='test' or a['family_id'] in chosen:continue
  if i not in LABELS:raise ValueError('missing pre-inference annotation')
  im=ImageOps.exif_transpose(Image.open(a['path'])).convert('RGBA');base=Image.new('RGBA',im.size,'white');base.alpha_composite(im);target=OUT/'images'/(a['asset_id']+'.jpg');base.convert('RGB').save(target,quality=95)
  selected={'asset_id':a['asset_id'],'family_id':a['family_id'],'source_url':a['source_url'],'source_sha256':a['sha256'],'path':str(target.resolve()),'sha256':digest(target.read_bytes()),'name':'外部手链推广图','manifest_index':i,'input_normalization':'EXIF transpose, alpha on white, JPEG95','near_duplicate_review':'assistant contact-sheet review, one image per test source family; no cross-family duplicate spotted, not human reviewed'}
  chosen[a['family_id']]=selected;bg,hand=LABELS[i]
  qs=[('画面中心主要展示的对象是什么？',['手链','手串','手镯','腕饰','bracelet'],'中心腕饰'),(OVERRIDE.get(i,'画面主要背景是什么颜色？'),bg.split('|'),'主体以外背景' if i not in OVERRIDE else '问题指定区域'),('画面中是否出现真人的手或手腕？',hand,'画面所有区域；展示架不算真人'),('这张推广图对应商品的真实销量是多少？',None,'原图不能核验真实成交销量')]
  for qn,(q,expected,region) in enumerate(qs):
   case={'case_id':a['asset_id']+'-'+str(qn+1),'asset_id':a['asset_id'],'family_id':a['family_id'],'question':q,'answer_contract':answer_contract(q)}
   public.append(case);gold.append({**case,'gold':{'expected':expected,'answerable':expected is not None,'support_region':region,'annotation_author':'primary assistant visual preannotation','human_review':'pending'}})
 write(OUT/'assets.json',list(chosen.values()));write(OUT/'requests.json',public);write(OUT/'cases-gold.json',gold)
 protocol={'model':VISION_MODEL,'arms':['B','C1'],'images':len(chosen),'cases':len(public),'planned_rows':len(public)*2,'selection':'first manifest-listed test image per family; unchanged from prior split','seed':20261016,'repeats':1,'concurrency':1,'max_calls':500,'estimated_budget_cny':5,'price_source':'https://docs.volcengine.com/docs/ark/model-pricing?lang=zh&redirect=1','same_model_initial_observations':True,'no_answer_cache':True,'scope':'public external images only; fixed reference image, no retrieval test','annotation':'assistant visual preannotation before model calls; human review pending','quality_floor':'observed C1-B >= -0.02 on answerable field+source; not a proof of statistical noninferiority','speed_goal':'C1 P95 <= 0.8 * B P95','production_mutated':False,'source_hashes':{str(p.relative_to(ROOT)):digest(p.read_bytes()) for p in [Path(__file__),ROOT/'evaluation/image_rag_provider.py',ROOT/'evaluation/run_visual_observation.py',ROOT/'evaluation/visual_observation_v3.py']},'prompts':{k:digest(v.encode()) for k,v in [('initial',INITIAL),('reader',READER_V3),('router',ROUTE_V3),('answer',ANSWER_V3)]}}
 protocol['input_hashes']={name:digest((OUT/name).read_bytes()) for name in ['assets.json','requests.json','cases-gold.json']};write(OUT/'protocol.json',protocol)
 return protocol

def receipt_cost(r):
 u=r.get('usage') or {};n=u.get('prompt_tokens',0);mult=1 if n<=32768 else 2 if n<=131072 else 4
 return mult*(n*.2+u.get('completion_tokens',0)*2)/1e6

def summarize():
 rows=[json.loads(s) for s in (OUT/'trace.jsonl').read_text().splitlines()] if (OUT/'trace.jsonl').exists() else []
 gold={x['case_id']:x['gold'] for x in json.loads((OUT/'cases-gold.json').read_text())};counts={}
 for r in rows:counts.setdefault(r['case_id'],set()).add(r['arm'])
 paired={k for k,v in counts.items() if v=={'B','C1'}};rs=[r for r in rows if r['case_id'] in paired]
 def passed(r):
  a=r.get('answer') or {};v=a.get('value');g=gold[r['case_id']]['expected']
  if g is None:return not r.get('format_errors') and a.get('status')=='insufficient' and v is None
  ok=v is g if isinstance(g,bool) else isinstance(v,str) and any(alias.lower() in v.lower() for alias in g)
  return r['status']=='succeeded' and a.get('status')=='answered' and ok and r['asset_id'] in a.get('source_ids',[])
 groups={}
 for arm in ['B','C1']:
  selected=[r for r in rs if r['arm']==arm];positive=[r for r in selected if gold[r['case_id']]['answerable']];negative=[r for r in selected if not gold[r['case_id']]['answerable']]
  def p95(key):return float(np.percentile([r[key]/1000 for r in selected],95)) if selected else None
  groups[arm]={'n':len(selected),'answerable_n':len(positive),'answerable_pass':sum(passed(r) for r in positive),'field_source_rate':sum(passed(r) for r in positive)/len(positive) if positive else None,'unprovable_n':len(negative),'unprovable_pass':sum(passed(r) for r in negative),'format_pass':sum(r.get('answer') is not None and not r.get('format_errors') for r in selected),'reads':sum(r['vision_called'] for r in selected),'route_calls':sum('route' in r for r in selected),'p95_s':p95('total_ms'),'reader_p95_s':p95('reader_ms'),'answer_p95_s':p95('answer_ms'),'online_cost_estimate_cny':sum(r.get('cost_estimate_cny',0) for r in selected)}
 rec=[json.loads(p.read_text()) for p in (OUT/'receipts').glob('*.json') if not p.name.endswith('.response.json')]
 # Paired family bootstrap: all four question types stay together.
 families=sorted({r['family_id'] for r in rs});delta=[];lat=[];rng=np.random.default_rng(20261016)
 if len(families)>1:
  for _ in range(2000):
   sample=[r for fam in rng.choice(families,len(families)) for r in rs if r['family_id']==fam];scores={}
   for arm in ['B','C1']:
    part=[r for r in sample if r['arm']==arm];pos=[r for r in part if gold[r['case_id']]['answerable']];scores[arm]=(sum(passed(r) for r in pos)/len(pos),np.percentile([r['total_ms']/1000 for r in part],95))
   delta.append(scores['C1'][0]-scores['B'][0]);lat.append(scores['C1'][1]-scores['B'][1])
 result={'paired_cases':len(paired),'attempted_rows':len(rows),'groups':groups,'requests':len(rec),'estimated_total_cost_cny':sum(receipt_cost(r) for r in rec),'actual_bill':None,'quality_delta_ci95':list(map(float,np.percentile(delta,[2.5,97.5]))) if delta else None,'p95_delta_ci95_s':list(map(float,np.percentile(lat,[2.5,97.5]))) if lat else None,'independent_images':len(families),'human_review_completed':False,'gold_is_assistant_preannotation':True}
 write(OUT/'summary.json',result)
 review=[{'review_id':digest((r['case_id']+r['arm']).encode())[:16],'case_id':r['case_id'],'question':r['question'],'answer':r.get('answer'),'gold':gold[r['case_id']],'correct':None,'citation_supported':None,'reviewer':None} for r in rows];review.sort(key=lambda r:r['review_id']);write(OUT/'semantic-review-blinded.json',review)
 def pct(x):return '待测' if x is None else f'{x*100:.1f}%'
 lines=['# 新图扩展实验：Seed 2.0 mini','','模型 doubao-seed-2-0-mini-260428。固定参考图，测试视觉证据复用；不测图像检索召回。','','主测试集：21个来源家族各1张图，84题；原13张开发图及其来源家族排除。其余22张同测试来源图未运行。',f'完成题次 {len(rows)}/168；完整配对 {len(paired)}/84。','','| 组别 | 可回答字段＋来源 | 无法核验题正确拒答 | 格式通过 | 在线读图 | P95秒 | 在线费用估算元 |','|---|---:|---:|---:|---:|---:|---:|']
 for arm,g in groups.items():lines.append(f"| {arm} | {g['answerable_pass']}/{g['answerable_n']} ({pct(g['field_source_rate'])}) | {g['unprovable_pass']}/{g['unprovable_n']} | {g['format_pass']}/{g['n']} | {g['reads']} | {g['p95_s'] or 0:.2f} | {g['online_cost_estimate_cny']:.4f} |")
 lines+=['','## 边界与验收','','- B每题读图；C1先复用独立初始观察，缺项最多读原图一次，补充观察在同图后续新问题中复用。两组同模型、同阅读/回答约束，同图问题和组别随机交错。','- 初始观察不接触测试问题或gold；生成答案、路由、读图均不接触gold。脚本中预标注常量仅用于准备文件，不进入模型请求。','- 透明图统一白底合成并冻结哈希。答案来自运行前主代理看图标注，未用受测模型答案作gold；不是人工独立金标准。近似图按来源家族隔离、每家族1张，目视未发现跨家族复制，但未经独立人工复核。','- 自动字段命中和来源ID合规不等于正文正确或引用支撑；数量、材质、投放效果、风格和受众标签均不当作已核验事实。','- 四类题仅覆盖主体、背景/指定区域颜色、真人手出现、真实销量不可核验；不覆盖细字OCR、复杂版式和检索召回。','- 预设观察质量下限：C1比B下降不超过2个百分点；速度目标：P95下降至少20%。只有观测结果、区间及人工审核都支持，才可作正式结论。','- family聚类配对bootstrap仅供探索。固定参考图且只有21个来源，不能推广到全部广告和线上流量。','- P95包括路由、读图、生成和本地编排，不含线上检索、上传排队。费用按官方常规价格估算，忽略缓存优惠；不是账单。初始观察准备成本单独保留于initial-observations.json，对C1完整计入总成本。',f"- 总调用{len(rec)}次，含初始观察和可用性检查，总费用估算¥{result['estimated_total_cost_cny']:.4f}。",f"- C1−B字段通过率95%区间：{result['quality_delta_ci95']}；P95差值区间（秒）：{result['p95_delta_ci95_s']}。",'','执行状态见 execution.json，收据见receipts/，独立语义复核队列见semantic-review-blinded.json。']
 (OUT/'report.md').write_text('\n'.join(lines)+'\n');print(json.dumps(result,ensure_ascii=False),flush=True)

def main():
 if (OUT/'trace.jsonl').exists():raise RuntimeError('already attempted; no blind retry')
 protocol=prepare();assets={a['asset_id']:a for a in json.loads((OUT/'assets.json').read_text())};cases=json.loads((OUT/'requests.json').read_text())
 provider=Provider(OUT,500);initial={};rows=[];status='running';error=None
 def call(key,messages,limit):
  rec=[json.loads(p.read_text()) for p in (OUT/'receipts').glob('*.json') if not p.name.endswith('.response.json')]
  if sum(receipt_cost(r) for r in rec)+.1>5:raise RuntimeError('estimated budget stop')
  value,r,replay=provider.call(OUT.name+':'+key,VISION_MODEL,messages,limit)
  if replay:raise RuntimeError('unexpected replay')
  return value,r
 try:
  # All initial observations are prepared without any future question.
  for aid,asset in assets.items():
   value,r=call('initial:'+aid,[{'role':'system','content':INITIAL},{'role':'user','content':image_content(asset,json.dumps({'source_id':aid},ensure_ascii=False))}],2400)
   initial[aid]={'observations':validate_observations(value,aid,r['request_id'],'initial'),'receipt':r};write(OUT/'initial-observations.json',initial)
   print('initial',len(initial),len(assets),flush=True)
  state={aid:copy.deepcopy(v['observations']) for aid,v in initial.items()};rng=random.Random(protocol['seed']);rng.shuffle(cases)
  for c in cases:
   aid=c['asset_id'];arms=['B','C1'];rng.shuffle(arms)
   for arm in arms:
    row={**c,'arm':arm,'status':'failed','vision_called':False,'reader_ms':0,'answer_ms':0,'route_ms':0,'receipt_ids':[],'cost_estimate_cny':0};evidence=copy.deepcopy(state[aid]) if arm=='C1' else []
    start=time.perf_counter()
    try:
     read=arm=='B';row['evidence_before']=copy.deepcopy(evidence)
     if arm=='C1':
      route,r=call(c['case_id']+':'+arm+':route',[{'role':'system','content':ROUTE_V3},{'role':'user','content':json.dumps({'question':c['question'],'answer_contract':c['answer_contract'],'source_id':aid,'observations':evidence},ensure_ascii=False)}],550)
      row['route']=checked_route(route,c['answer_contract'],evidence);row['route_ms']=r['elapsed_ms'];row['receipt_ids'].append(r['request_id']);row['cost_estimate_cny']+=receipt_cost(r);read=row['route']['decision']=='read'
     if read:
      row['vision_called']=True
      value,r=call(c['case_id']+':'+arm+':read',[{'role':'system','content':READER_V3},{'role':'user','content':image_content(assets[aid],json.dumps({'question':c['question'],'answer_contract':c['answer_contract'],'source_id':aid},ensure_ascii=False))}],1600)
      row['reader_ms']=r['elapsed_ms'];row['receipt_ids'].append(r['request_id']);row['cost_estimate_cny']+=receipt_cost(r);obs=validate_observations(value,aid,r['request_id'],'supplement');evidence.extend(obs)
      if arm=='C1':state[aid].extend(obs)
     row['evidence_after']=copy.deepcopy(evidence)
     value,r=call(c['case_id']+':'+arm+':answer',[{'role':'system','content':ANSWER_V3},{'role':'user','content':json.dumps({'question':c['question'],'answer_contract':c['answer_contract'],'allowed_source_ids':[aid],'source_name_map':{aid:'外部推广图'},'evidence':evidence},ensure_ascii=False)}],600)
     row['answer_ms']=r['elapsed_ms'];row['receipt_ids'].append(r['request_id']);row['cost_estimate_cny']+=receipt_cost(r);row['answer']=value;row['format_errors']=answer_errors(value,c['answer_contract'],aid);row['status']='format_failed' if row['format_errors'] else 'succeeded'
    finally:
     row['total_ms']=(time.perf_counter()-start)*1000;rows.append(row)
     with (OUT/'trace.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    print(len(rows),c['case_id'],arm,row['status'],round(row['total_ms']/1000,2),flush=True)
  status='completed'
 except Exception as exc:status='stopped';error=str(exc);print(status,error,flush=True)
 finally:
  provider.close();write(OUT/'execution.json',{'status':status,'error':error,'attempted_rows':len(rows)});summarize()
if __name__=='__main__':main()
