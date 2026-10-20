"""Authenticated local HTTP/Worker check using an isolated validation identity.
Never prints credentials; stores only synthetic requests and evidence in its report.
"""
import asyncio
import json
import os
from pathlib import Path
import sys
import time
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1]/'.env')

async def safe_post(http,url,**kwargs):
    # Preserve the SAME payload and idempotency key when the existing limiter rejects a run.
    for attempt in range(13):
        response=await http.post(url,**kwargs)
        if response.status_code!=429:return response
        if attempt==12:return response
        await asyncio.sleep(min(5,max(.1,float(response.headers.get('Retry-After','5')))))

async def main():
    base=os.getenv('AGENT_HOSTING_URL','http://127.0.0.1:8002')
    headers={'Authorization':'Bearer '+os.environ['AGENT_SERVICE_TOKEN']}
    evidence=[]
    async with httpx.AsyncClient(base_url=base,timeout=27) as http:
        auth=await http.post('/auth/session',json={'subject':'memory-validation'},headers=headers);auth.raise_for_status()
        identity=auth.json();headers['X-Agent-Session']=identity['session_token']
        async def session(title):
            response=await http.post('/sessions',headers=headers,json={'workspace_id':identity['workspace_id'],'title':title})
            response.raise_for_status();return response.json()['id']
        async def question(sid,message):
            response=await safe_post(http,'/sessions/'+sid+'/chat',headers={**headers,'Idempotency-Key':str(uuid.uuid4())},json={'message':message})
            response.raise_for_status();value=response.json()
            if response.status_code==202:
                for _ in range(160):
                    current=await http.get('/runs/'+value['run_id'],headers=headers);current.raise_for_status()
                    if current.json()['status'] in ('succeeded','failed','cancelled','timed_out','interrupted'):break
                    await asyncio.sleep(.3)
                history=await http.get('/sessions/'+sid+'/messages',headers=headers);history.raise_for_status()
                answer=next((v['content_preview'] for v in history.json()['items'] if v['run_id']==value['run_id'] and v['role']=='assistant'),'')
                value.update(status=current.json()['status'],response=answer)
            return value
        first=await session('记忆链路验证')
        set_result=await http.put('/sessions/'+first+'/memory/preferences',headers=headers,json={'field':'优化目标','values':['获取咨询'],'expected_version':0})
        if set_result.status_code==409:
            existing=(await http.get('/sessions/'+first+'/memory/preferences',headers=headers)).json()
            version=next(v['version'] for v in existing if v['field']=='优化目标')
            set_result=await http.put('/sessions/'+first+'/memory/preferences',headers=headers,json={'field':'优化目标','values':['获取咨询'],'expected_version':version})
        set_result.raise_for_status();version=set_result.json()['version']
        second=await session('跨会话验证')
        answer=await question(second,'我的长期优化目标是什么？')
        assert answer['status']=='succeeded' and '获取咨询' in answer['response'],answer
        evidence.append({'case':'cross_session_preference','passed':True,'response':answer['response']})
        revoked=await http.put('/sessions/'+second+'/memory/preferences',headers=headers,json={'field':'优化目标','values':[],'expected_version':version})
        revoked.raise_for_status()
        after=await question(second,'我的长期优化目标是什么？')
        assert '获取咨询' not in after['response'],after
        evidence.append({'case':'retraction_effective','passed':True,'response':after['response']})
        # A real generated answer must create a durable memory job after successful finish.
        generated=await question(first,'请记住：我的长期素材偏好是真人口播。')
        assert generated['status']=='succeeded',generated
        for _ in range(120):
            jobs=(await http.get('/sessions/'+first+'/memory/jobs',headers=headers)).json()
            relevant=[j for j in jobs if j['run_id']==generated['run_id']]
            if relevant and relevant[0]['status'] in ('succeeded','dead'):break
            await asyncio.sleep(.5)
        assert relevant and relevant[0]['status']=='succeeded',relevant
        evidence.append({'case':'real_model_to_durable_extraction','passed':True,'job_status':relevant[0]['status']})
        preferences=(await http.get('/sessions/'+first+'/memory/preferences',headers=headers)).json()
        assert any(p['field']=='素材偏好' and p['status']=='active' and '真人口播' in p['value_json'] for p in preferences),preferences
        evidence.append({'case':'grounded_real_preference_extracted','passed':True})
        data={'title':'隔离咖啡测试流程','objective':'隔离咖啡测试','conditions':['隔离咖啡测试'],
            'steps':['先固定一个变量，再记录基线'],'limitations':['仅验证流程，不保证效果'],'source_title':'验证用人工审核规范'}
        draft=await http.post('/sessions/'+first+'/memory/procedures',headers=headers,json=data);draft.raise_for_status()
        ident=draft.json()['id']
        reviewed=await http.post('/sessions/'+first+'/memory/procedures/'+ident+'/decision',headers=headers,json={'action':'review','expected_version':1});reviewed.raise_for_status()
        procedure=await question(first,'查询操作经验：隔离咖啡测试')
        assert '先固定一个变量' in procedure['response'],procedure
        withdrawn=await http.post('/sessions/'+first+'/memory/procedures/'+ident+'/decision',headers=headers,json={'action':'withdraw','expected_version':2});withdrawn.raise_for_status()
        after_withdrawal=await question(first,'查询操作经验：隔离咖啡测试')
        assert '先固定一个变量' not in after_withdrawal['response'],after_withdrawal
        evidence.append({'case':'reviewed_procedure_read_and_withdraw','passed':True})
        # /chat compatibility goes through the same hosted worker and authenticated identity.
        async with httpx.AsyncClient(base_url='http://127.0.0.1:8000',timeout=27) as legacy:
            key=str(uuid.uuid4());legacy_headers={'Authorization':headers['Authorization'],'Idempotency-Key':key}
            body={'message':'我的长期素材偏好是什么？','user_id':'memory-validation','conv_id':'memory-compat-validation'}
            response=await safe_post(legacy,'/chat',json=body,headers=legacy_headers);response.raise_for_status()
            value=response.json()
            assert value['status']=='succeeded' and '真人口播' in value['response'],value
            again=await safe_post(legacy,'/chat',json=body,headers=legacy_headers);again.raise_for_status()
            assert again.json()['run_id']==value['run_id']
            evidence.append({'case':'legacy_sync_alias_and_idempotency','passed':True,'response':value['response']})

    report={'subject':'isolated validation identity','evidence':evidence,'scope':'local actual HTTP + Worker + configured business model; synthetic data only'}
    target=Path('docs/validation/agent-memory-experience/runtime-verification.json');target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':asyncio.run(main())
