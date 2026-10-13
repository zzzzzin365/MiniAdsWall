"""Independent real Chroma requests at each K on the frozen questions."""
import json, hashlib, tempfile
from datetime import datetime,timezone
from pathlib import Path
import chromadb
from chromadb.config import Settings
from mcp.knowledge_base import KnowledgeBase

def main():
    out=Path('docs/evidence/vector-topk-independent-v1.json')
    if out.exists(): raise FileExistsError(out)
    cases=json.loads(Path('docs/evidence/vector-retrieval-cases-v1.json').read_text())
    old=json.loads(Path('docs/evidence/vector-retrieval-results-v1.json').read_text())
    with tempfile.TemporaryDirectory(prefix='miniad-independent-topk-') as tmp:
        kb=KnowledgeBase.__new__(KnowledgeBase)
        kb._client=chromadb.PersistentClient(path=tmp,settings=Settings(anonymized_telemetry=False))
        kb._open_cosine_collection()
        kb._ensure_default_docs()
        corpus_hash=hashlib.sha256(json.dumps(kb._default_docs(),ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        assert corpus_hash==old['corpus_sha256']
        rows=[]; summary={}
        for k in (3,5,10):
            subset=[]
            for case in cases:
                hits=kb.search(case['question'],top_k=k)
                gold=set(case['gold_ids']); found=len(gold & {x['id'] for x in hits})
                subset.append({**case,'top_k':k,'retrieved':hits,'recall':found/len(gold),'any_hit':found>0,'all_gold_hit':found==len(gold)})
            summary[str(k)]={name:sum(r[name] for r in subset)/len(subset) for name in ('recall','any_hit','all_gold_hit')}
            summary[str(k)]['hit_count']=sum(r['any_hit'] for r in subset)
            summary[str(k)]['complete_count']=sum(r['all_gold_hit'] for r in subset)
            summary[str(k)]['multi_complete_count']=sum(r['all_gold_hit'] for r in subset if r['group']=='multi_document')
            rows+=subset
        report={'run_at_utc':datetime.now(timezone.utc).isoformat(),'scope':'Pure vector retrieval; independent query n_results=3,5,10; no rewrite/rerank model. Enhanced-model sweep blocked by daily free-model quota.',
                'questions_per_k':len(cases),'documents':len(kb._default_docs()),'chunks':kb.doc_count,'corpus_sha256':corpus_hash,
                'summary':summary,'details':rows,'limitations':old['limitations']}
        out.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
