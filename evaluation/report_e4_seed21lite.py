"""Report same-run paired outcomes; no model calls or retrospective score repair."""
import json,collections
from pathlib import Path
import numpy as np
from evaluation.image_rag_provider import write,digest
from evaluation.run_e4_seed21lite import summarize,OUT

def main():
 summarize(OUT)
 rows=[json.loads(s) for s in (OUT/'trace.jsonl').read_text().splitlines()] if (OUT/'trace.jsonl').exists() else []
 gold={c['case_id']:c['gold'] for c in json.loads((OUT/'cases-gold.json').read_text())}
 arms=['old_C1','fixed_C1','always_read_B'];counts=collections.defaultdict(set)
 for r in rows:counts[r['case_id']].add(r['arm'])
 paired={c for c,a in counts.items() if a==set(arms)}
 result={};review=[];mapping=[]
 for arm in arms:
  rs=[r for r in rows if r['case_id'] in paired and r['arm']==arm];pos=[r for r in rs if gold[r['case_id']]['answerable']];neg=[r for r in rs if not gold[r['case_id']]['answerable']]
  def passed(r):
   a=r.get('answer') or {};v=a.get('value');g=gold[r['case_id']]['expected']
   if g is None:return a.get('status')=='insufficient' and v is None
   ok=v is g if isinstance(g,bool) else isinstance(v,str) and any(x.lower() in v.lower() for x in g)
   return r['status']=='succeeded' and a.get('status')=='answered' and ok and r['asset_id'] in a.get('source_ids',[])
  result[arm]={'paired_n':len(rs),'answerable_n':len(pos),'field_source_pass_rate':sum(passed(r) for r in pos)/len(pos) if pos else None,'format_pass_rate':sum(r.get('answer') is not None and not r.get('format_errors') for r in rs)/len(rs) if rs else None,'unprovable_pass_rate':sum(passed(r) for r in neg)/len(neg) if neg else None,'reads':sum(r['vision_called'] for r in rs),'route_calls':sum('route' in r for r in rs),'p50_s':float(np.percentile([r['total_ms']/1000 for r in rs],50)) if rs else None,'p95_s':float(np.percentile([r['total_ms']/1000 for r in rs],95)) if rs else None,'body_correctness':None,'citation_semantic_support':None}
 for r in rows:
  rid=digest((r['case_id']+r['arm']).encode())[:16]
  review.append({'review_id':rid,'case_id':r['case_id'],'question':r['question'],'answer':r.get('answer'),'image_path':next(a['path'] for a in json.loads((OUT/'assets.json').read_text()) if a['asset_id']==r['asset_id']),'gold':gold[r['case_id']],'body_correct':None,'citation_supported':None,'reviewer':None})
  mapping.append({'review_id':rid,'arm':r['arm']})
 review.sort(key=lambda r:r['review_id']);write(OUT/'semantic-review-blinded.json',review);write(OUT/'semantic-review-mapping.json',mapping)
 summary=json.loads((OUT/'summary.json').read_text());summary['paired_groups']=result;summary['paired_cases']=len(paired);write(OUT/'summary.json',summary)
 def pct(x):return '待评' if x is None else f'{x*100:.1f}%'
 lines=['# E4：Seed 2.1 lite 旧版与修复版同期对照','','模型：doubao-seed-2-1-lite-260915。仅公开外部图片；本轮重新建立同模型初始观察。','',f'已记录 {len(rows)}/90 题次；完整三组配对 {len(paired)}/30 题。执行状态见 execution.json。','','| 组别 | 可回答题字段＋来源通过率 | 格式通过率 | 不可证明题正确拒答 | 在线读图 | P50秒 | P95秒 |','|---|---:|---:|---:|---:|---:|---:|']
 for arm,g in result.items():
  lines.append(f"| {arm} | {pct(g['field_source_pass_rate'])} | {pct(g['format_pass_rate'])} | {pct(g['unprovable_pass_rate'])} | {g['reads']} | {g['p50_s'] or 0:.2f} | {g['p95_s'] or 0:.2f} |")
 lines+=['','## 指标边界','','- old_C1：原路由、原读图和原回答提示；fixed_C1：路由保护、观察状态约束、按题型回答约束一起修复，测的是整套修复，不能归因单项。','- always_read_B：每题读原图，使用与修复组相同的阅读和回答约束。','- 初始观察对两组复用策略相同，各组独立维护补充观察；固定随机题序，题内交错执行。','- 全部组使用同一格式与字段评分，错误保留分母；字段＋来源通过率不等于正文正确率，来源ID有效也不等于引用内容真实支撑。','- 正文正确性和引用支撑待独立审查，匿名队列见 semantic-review-blinded.json。','- P50/P95包含路由、读图、生成及本地实验编排；费用收据扫描等测量开销各组一致，但与历史运行代码不同，不跨运行比较。初始理解耗时单列 summary.json，不混入在线P95。','- 本轮为已知图片回归；仅5张测试图、30题，未证明新图泛化或生产P95改善。','',f"调用共 {summary['requests']} 次（含检查与初始理解），总Token {summary['total_tokens']}；按全部Token每百万24元保守估算 ¥{summary['conservative_cost_estimate_cny']:.3f}，非实际账单。实际收费可能因缓存及调用类型不同。",'','历史 Seed mini 结果保持不变。未修改线上模型配置；未加入原生CLIP检索，固定参考图测试视觉证据准备。']
 (OUT/'report.md').write_text('\n'.join(lines)+'\n');print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
