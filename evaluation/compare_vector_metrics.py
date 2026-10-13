"""Paired real Chroma distance ablation with frozen previous DeepSeek outputs."""
import hashlib,json,tempfile
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
import chromadb
from chromadb.config import Settings
from mcp.knowledge_base import KnowledgeBase
from evaluation.summarize_retrieval_topk import summarize

class FrozenQueryCollection:
    def __init__(self,collection,vectors):self.collection=collection;self.vectors=vectors
    def query(self,query_texts,n_results):
        return self.collection.query(query_embeddings=[self.vectors[q] for q in query_texts],n_results=n_results)

def main():
    output=Path('docs/evidence/l2-cosine-controlled-v1.json')
    if output.exists():raise FileExistsError(output)
    previous=json.loads(Path('docs/evidence/retrieval-deepseek-topk-pool-v1.json').read_text())
    with tempfile.TemporaryDirectory(prefix='miniad-metric-ablation-') as tmp:
        client=chromadb.PersistentClient(path=tmp,settings=Settings(anonymized_telemetry=False))
        l2=KnowledgeBase.__new__(KnowledgeBase);l2._client=client
        l2._collection=client.get_or_create_collection('metric_l2',metadata={'hnsw:space':'l2'})
        l2._ensure_default_docs()
        corpus=hashlib.sha256(json.dumps(l2._default_docs(),ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        assert corpus==previous['corpus_sha256']
        source=l2._collection.get(include=['documents','metadatas','embeddings'])
        emb=np.asarray(source['embeddings'],dtype=np.float32)
        cos_col=client.get_or_create_collection('metric_cosine',metadata={'hnsw:space':'cosine'})
        cos_col.add(ids=source['ids'],documents=source['documents'],metadatas=source['metadatas'],embeddings=emb)
        queries=list(dict.fromkeys(q for row in previous['details'] for q in row['trace']['queries']))
        q_emb=np.asarray(l2._collection._embedding_function(queries),dtype=np.float32)
        vectors=dict(zip(queries,q_emb.tolist()))
        l2_col=l2._collection;l2._collection=FrozenQueryCollection(l2_col,vectors)
        cosine=KnowledgeBase.__new__(KnowledgeBase);cosine._collection=FrozenQueryCollection(cos_col,vectors)
        query_results={}; changes=[]; max_distance_residual=0.
        for q in queries:
            a=l2.search(q,30);b=cosine.search(q,30)
            query_results[q]={'l2':a,'cosine':b}
            if [x['id'] for x in a]!=[x['id'] for x in b]:changes.append(q)
            # Project scores round to 4 decimals; compare original distances directly.
            da=l2_col.query(query_embeddings=[vectors[q]],n_results=30)
            db=cos_col.query(query_embeddings=[vectors[q]],n_results=30)
            bd=dict(zip(db['ids'][0],db['distances'][0]))
            max_distance_residual=max(max_distance_residual,max(abs(d-2*bd[i]) for i,d in zip(da['ids'][0],da['distances'][0])))
        arms={}
        candidate_changes=[]
        for name in ('l2','cosine'):
            details=[]
            for row in previous['details']:
                merged=[];seen=set()
                for q in row['trace']['queries']:
                    for chunk in query_results[q][name][:10]:
                        if chunk['id'] not in seen:seen.add(chunk['id']);merged.append(chunk)
                old_pool=set(row['trace']['candidate_ids'])
                if name=='l2':assert seen==old_pool, f'Baseline candidate mismatch {row["id"]}'
                elif seen!=old_pool:candidate_changes.append(row['id'])
                # Freeze the real previous model's final ID ranking; do not feed gold labels.
                order=[x['id'] for x in row['retrieved']]
                by_id={x['id']:x for x in merged}
                ranked=[by_id[i] for i in order if i in by_id]
                ranked+= [x for x in merged if x['id'] not in set(order)]
                if name=='l2':assert [x['id'] for x in ranked[:10]]==order
                details.append({**row,'retrieved':ranked[:10],'trace':{**row['trace'],'candidate_ids':[x['id'] for x in merged]}})
            arms[name]={'model':previous['model'],'corpus_sha256':corpus,'details':details,
                        'limitations':['Previous model query and output ranking snapshots replayed; no new LLM calls','Synthetic small corpus, not final-answer accuracy or online load test']}
        summaries={name:summarize(arm) for name,arm in arms.items()}
        report={'run_at_utc':datetime.now(timezone.utc).isoformat(),'method':'Real L2/cosine Chroma indexes, exact same stored/query vectors; frozen previous DeepSeek query rewrites/failures and final ID ranking. Top3/5/10 final cutoffs.',
                'model_snapshot':'docs/evidence/retrieval-deepseek-topk-pool-v1.json','corpus_sha256':corpus,
                'document_vectors_sha256':hashlib.sha256(emb.tobytes()).hexdigest(),'query_vectors_sha256':hashlib.sha256(q_emb.tobytes()).hexdigest(),
                'documents':30,'questions':68,'unique_queries':len(queries),
                'document_norm_min':float(np.linalg.norm(emb,axis=1).min()),'document_norm_max':float(np.linalg.norm(emb,axis=1).max()),
                'query_norm_min':float(np.linalg.norm(q_emb,axis=1).min()),'query_norm_max':float(np.linalg.norm(q_emb,axis=1).max()),
                'max_squared_l2_minus_2_cosine_distance':max_distance_residual,
                'full_ranking_changed_queries':changes,'candidate_pool_changed_cases':candidate_changes,
                'summaries':summaries,'arms':arms,'query_results':query_results}
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2)); print(json.dumps({k:v for k,v in report.items() if k not in ('arms','query_results','summaries')},ensure_ascii=False,indent=2))
        print(json.dumps({name:{k:s['all'] for k,s in result['summary'].items()} for name,result in summaries.items()},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
