"""Paired Top-K cutoffs on one frozen retrieved ranking per question."""
import argparse, json
from collections import Counter
from pathlib import Path

def summarize(report):
    rows=report['details']; results={}
    for k in (3,5,10):
        groups={}
        for group in ('all','direct','paraphrase','multi_document'):
            cases=[r for r in rows if group=='all' or r['group']==group]
            recall=0.; hits=complete=0
            for r in cases:
                gold=set(r['gold_ids']); found=len(gold & {x['id'] for x in r['retrieved'][:k]})
                recall+=found/len(gold); hits+=found>0; complete+=found==len(gold)
            groups[group]={'questions':len(cases),'recall':recall/len(cases),'any_hit':hits/len(cases),
                           'all_gold_hit':complete/len(cases),'hit_count':hits,'complete_count':complete}
        results[str(k)]=groups
    status=Counter((m['stage'],m['status'],m.get('http_status')) for r in rows for m in r['trace']['model_calls'])
    return {'method':'Same Top10 candidate retrieval and model ranking; truncate final ranking to K=3,5,10. Not three independently requested recall depths.',
            'model':report['model'],'corpus_sha256':report['corpus_sha256'],'summary':results,
            'model_status':[{'stage':s,'status':t,'http_status':h,'count':n} for (s,t,h),n in status.items()],
            'returned_lengths':dict(Counter(len(r['retrieved']) for r in rows)),
            'limitations':report['limitations']+['Fallbacks included; exact runtime independent top_k=3/5 uses recall_k=5, unlike this common recall_k=10 pool.']}

def main():
    p=argparse.ArgumentParser(); p.add_argument('--input',required=True); p.add_argument('--output',required=True); a=p.parse_args()
    out=Path(a.output)
    if out.exists(): raise FileExistsError(out)
    result=summarize(json.loads(Path(a.input).read_text())); out.write_text(json.dumps(result,ensure_ascii=False,indent=2)); print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
