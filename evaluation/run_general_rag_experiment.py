"""General/public corpus only. Frozen-label retrieval experiment, no private media.

Baseline: existing Chroma cosine and uncached text embedding.
Optimization: cached query embedding + weighted title/body character retrieval.
Weights chosen on source-family development split, never on test labels.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import re
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
import chromadb
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
from mcp.external_marketing import records
from evaluation.run_multimodal_rag_pilot import cases as public_cases

def sha(b):return hashlib.sha256(b).hexdigest()
def save(p,d):p.write_text(json.dumps(d,ensure_ascii=False,indent=2))
def tokens(text):
    words=re.findall(r'[a-z0-9]+',text.lower())
    chars=re.findall(r'[\u4e00-\u9fff]+',text)
    return words+[s[i:i+2] for s in chars for i in range(len(s)-1)]

def main(out):
    out.mkdir(parents=True,exist_ok=False)
    rng=np.random.default_rng(20261014);rr=random.Random(20261014)
    client=chromadb.PersistentClient(path=str(ROOT/'data/chroma'),settings=chromadb.Settings(anonymized_telemetry=False))
    rows=client.get_collection('knowledge_base_cosine_v1').get(include=['documents','metadatas','embeddings'])
    ids=rows['ids'];vec=np.asarray(rows['embeddings'],dtype='float32');meta=rows['metadatas'];docs=rows['documents']
    if len(ids)!=43:raise ValueError('Expected frozen 43-chunk corpus; review changed corpus before rerunning')
    corpus=sha(json.dumps([ids,docs,meta],ensure_ascii=False,sort_keys=True).encode())
    ef=DefaultEmbeddingFunction()
    copy=chromadb.PersistentClient(path=str(out/'index'),settings=chromadb.Settings(anonymized_telemetry=False))
    index=copy.create_collection('general_public',metadata={'hnsw:space':'cosine'},embedding_function=ef)
    index.add(ids=ids,documents=docs,metadatas=meta,embeddings=vec.tolist())
    save(out/'corpus.json',{'ids':ids,'documents':docs,'metadatas':meta})
    np.savez(out/'vectors.npz',ids=ids,embeddings=vec)
    public=records();by_source={}
    for x in public:by_source.setdefault(x['source_id'],[]).append(x['id'])
    cases=[]
    for x in json.loads((ROOT/'docs/evidence/vector-retrieval-cases-v1.json').read_text()):
        cases.append({'id':x['id'],'query':x['question'],'gold':[set([i]) for i in x['gold_ids']],
                      'group':x['group'],'scope':'general','answerable':True})
    for x in public_cases():
        cases.append({'id':'external-'+x['id'],'query':x['query'],'gold':[set(by_source[s]) for s in x['gold_sources']],
                      'group':'external-multi' if len(x['gold_sources'])>1 else 'external-single','scope':'external','answerable':x['answerable']})
    if not all(g<=set(ids) for x in cases for g in x['gold']):raise ValueError('Frozen gold IDs not present')
    # Related queries and multi-source questions share a connected family.
    parent={i:i for i in ids}
    def find(a):
        while parent[a]!=a:a=parent[a]
        return a
    for x in cases:
        related=sorted(set().union(*x['gold'])) if x['gold'] else []
        for i in related[1:]:parent[find(i)]=find(related[0])
    families=sorted({find(i) for x in cases for g in x['gold'] for i in g},key=lambda i:sha(i.encode()))
    dev=set(families[:max(1,round(len(families)*.25))])
    for x in cases:
        x['family']=find(next(iter(x['gold'][0]))) if x['gold'] else 'negative'
        x['split']='dev' if x['family'] in dev else 'test'
        x['gold']=[sorted(g) for g in x['gold']]
    save(out/'cases.json',cases)
    weights=[0,.25,.5,.75,1]
    counts=[Counter(tokens(d))+Counter({k:4*v for k,v in Counter(tokens(m.get('title',''))).items()}) for d,m in zip(docs,meta)]
    df=Counter(k for c in counts for k in c)
    idf={k:math.log((len(docs)+1)/(n+1))+1 for k,n in df.items()}
    vectors=[{k:(1+math.log(v))*idf[k] for k,v in c.items()} for c in counts]
    norms=[math.sqrt(sum(v*v for v in d.values())) or 1 for d in vectors]
    def lexical(q):
        c=Counter(tokens(q));v={k:(1+math.log(n))*idf[k] for k,n in c.items() if k in idf}
        norm=math.sqrt(sum(n*n for n in v.values())) or 1
        return np.asarray([sum(n*d.get(k,0) for k,n in v.items())/(norm*dn) for d,dn in zip(vectors,norms)])
    ef([cases[0]['query']])
    cache={x['query']:ef([x['query']])[0] for x in cases}
    np.savez(out/'query-vectors.npz',queries=list(cache),vectors=np.asarray(list(cache.values()),dtype='float32'))
    def retrieve(q,weight=0,cached=True):
        e=cache[q] if cached else ef([q])[0]
        r=index.query(query_embeddings=[e],n_results=min(10,len(ids)),include=['distances'])
        dense=r['ids'][0]
        if not weight:return dense[:5]
        score={i:(1-weight)/(60+k) for k,i in enumerate(dense,1)}
        ls=lexical(q)
        for rank,j in enumerate(np.argsort(-ls,kind='stable')[:10],1):
            if ls[j]>0:score[ids[j]]=score.get(ids[j],0)+weight/(60+rank)
        return sorted(score,key=lambda i:(-score[i],i))[:5]
    trace=[]
    def metric(x,hits):
        found=sum(bool(set(g)&set(hits)) for g in x['gold'])
        return {'recall':found/len(x['gold']) if x['gold'] else None,'complete':int(found==len(x['gold'])) if x['gold'] else None}
    for x in cases:
        arms={str(w):retrieve(x['query'],w) for w in weights}
        trace.append({**x,'arms':arms,'metrics':{k:metric(x,v) for k,v in arms.items()}})
    development={str(w):float(np.mean([x['metrics'][str(w)]['recall'] for x in trace if x['split']=='dev' and x['answerable']])) for w in weights}
    selected=min(weights,key=lambda w:(-development[str(w)],w))
    save(out/'development-selection.json',{'weights':weights,'dev_recall':development,'selected_weight':selected,'rule':'maximum development recall, ties prefer lower lexical weight'})
    save(out/'trace.json',trace)
    results={}
    for name,w in [('baseline',0),('optimized',selected)]:
        results[name]={}
        for scope in ['all','general','external']:
            subset=[x for x in trace if x['split']=='test' and x['answerable'] and (scope=='all' or x['scope']==scope)]
            results[name][scope]={'n':len(subset),'recall_at5':float(np.mean([x['metrics'][str(w)]['recall'] for x in subset])),
                                 'complete_at5':float(np.mean([x['metrics'][str(w)]['complete'] for x in subset]))}
    test=[x for x in trace if x['split']=='test' and x['answerable']]
    grouped={f:[x for x in test if x['family']==f] for f in {x['family'] for x in test}}
    groups=list(grouped.values());boot=[]
    for _ in range(10000):
        sample=[x for i in rng.integers(0,len(groups),len(groups)) for x in groups[i]]
        boot.append(np.mean([x['metrics'][str(selected)]['recall']-x['metrics']['0']['recall'] for x in sample]))
    latency=[]
    jobs=[(x,r) for x in test for r in range(5)];rr.shuffle(jobs)
    for x,repeat in jobs:
        arms=[('baseline_uncached',0,False),('execution_cached',0,True),('optimized_cached',selected,True)];rr.shuffle(arms)
        answers={}
        for name,w,c in arms:
            t=time.perf_counter();answers[name]=retrieve(x['query'],w,c)
            latency.append({'id':x['id'],'repeat':repeat,'arm':name,'ms':(time.perf_counter()-t)*1000})
        if answers['baseline_uncached']!=answers['execution_cached']:raise AssertionError('embedding cache changed ranking')
    save(out/'latency.json',latency)
    speed={a:{'n':len(v),'p50_ms':float(np.percentile(v,50)),'p95_ms':float(np.percentile(v,95))}
           for a in ['baseline_uncached','execution_cached','optimized_cached']
           for v in [[x['ms'] for x in latency if x['arm']==a]]}
    summary={'quality':results,'speed':speed,'selected_weight':selected,'dev_cases':sum(x['split']=='dev' for x in cases),
        'test_cases':sum(x['split']=='test' for x in cases),'test_positive_families':len(groups),
        'recall_delta_ci95':np.percentile(boot,[2.5,97.5]).tolist(),'recall_delta':results['optimized']['all']['recall_at5']-results['baseline']['all']['recall_at5'],
        'limits':['Frozen existing author-labeled questions; not independent blind annotations, previously used general questions.',
        'General gold exact IDs; external gold equivalent source IDs; each reported separately.',
        'Character bigram title/body ranking, not trained STAIR or visual embedding.',
        '100% embedding-cache hit workload; no online traffic hit-rate claim or generation/authorization/model rewrite latency.',
        'No private merchant data, no model answer scoring; quality means retrieval relevance only.']}
    save(out/'summary.json',summary)
    save(out/'manifest.json',{'scope':'general and public references only','corpus_sha256':corpus,'script_sha256':sha(Path(__file__).read_bytes()),
        'vectors_sha256':sha(vec.tobytes()),'case_sha256':sha((out/'cases.json').read_bytes()),'git_head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'seed':20261014,'platform':platform.platform(),'chunks':len(ids),'embedding':'Chroma default ONNX all-MiniLM-L6-v2',
        'model_calls':0,'status':'completed','business_index_mutated':False})
    print(json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args();main(Path(a.output))
