"""Use existing preflight, agents and advertising tools with durable runtime adapters."""
import asyncio
import hashlib
import json
import os
import time
from dataclasses import asdict
from sqlalchemy import select
from agents.agent_orchestrator import AgentOrchestrator, Request, AgentType
from core.action_decision import PendingActionStore, PendingClarificationStore
from core.skill_loader import SkillManager
from mcp.ads_tools import ads_summary_handler, ad_performance_search_handler, bid_simulation_handler
from .repository import Conflict, dumps
from .objects import preview
from .streaming import stream_text
from . import schema as s

class SQLPendingMixin:
    def __init__(self, repo, ttl_seconds=300):
        super().__init__(ttl_seconds=ttl_seconds)
        self.repo=repo
    def _put_raw(self,user_id,conv_id,raw):
        self.repo.pending_op(self._key(user_id,conv_id),'put',raw,self.ttl_seconds)
    def _get_raw(self,user_id,conv_id): return self.repo.pending_op(self._key(user_id,conv_id),'get')
    def _pop_raw(self,user_id,conv_id): return self.repo.pending_op(self._key(user_id,conv_id),'pop')
class SQLActions(SQLPendingMixin,PendingActionStore): pass
class SQLClarifications(SQLPendingMixin,PendingClarificationStore): pass

class WaitingApproval(Exception): pass

def permissions(subject):
    try:
        value=json.loads(os.getenv('ACTION_PERMISSION_MAP','{}')).get(subject,[])
        return value if isinstance(value,list) else []
    except ValueError: return []

class Pipeline:
    def __init__(self,repo,sandbox=None,redis=None):
        self.repo,self.sandbox,self.redis=repo,sandbox,redis
        key=os.getenv('ANTHROPIC_API_KEY') or os.getenv('OPENROUTER_API_KEY')
        if not key: raise RuntimeError('model credential is required for Worker')
        base=os.getenv('ANTHROPIC_BASE_URL') or ('https://openrouter.ai/api' if os.getenv('OPENROUTER_API_KEY') else None)
        model=os.getenv('ANTHROPIC_MODEL') or os.getenv('OPENROUTER_MODEL','claude-3-5-sonnet-20241022')
        skills=SkillManager(root_dir=os.getenv('ECHOMIND_SKILLS_DIR','skills'),max_prompt_chars=5000); skills.load()
        self.orchestrator=AgentOrchestrator(api_key=key,base_url=base,model=model,skill_manager=skills)
        self.orchestrator._decision_engine.pending_store=SQLActions(repo)
        self.orchestrator._decision_engine.clarification_store=SQLClarifications(repo,600)
        self.kb=None
        if os.getenv('HOSTING_RAG_ENABLED','false')=='true':
            from mcp.knowledge_base import KnowledgeBase
            # RAG is optional; a broken configured service must fail startup, not silently disappear.
            self.kb=KnowledgeBase(chroma_host=os.getenv('CHROMA_HOST','localhost'), chroma_port=int(os.getenv('CHROMA_PORT','8001')), chroma_path=os.getenv('CHROMA_PERSIST_DIRECTORY','data/chroma'))

    async def close(self):
        seen=set()
        clients=[self.orchestrator._intent_recognizer.client]
        for pool in self.orchestrator._pool.values(): clients.extend(a._client for a in pool)
        for client in clients:
            if id(client) not in seen:
                seen.add(id(client)); await client.close()

    def identity(self,r):
        with self.repo.engine.connect() as c:
            session=self.repo._session(c,r['user_id'],r['session_id'])
            return self.repo._row(c,s.users,r['user_id'])['auth_subject'],session['version']

    async def __call__(self,ctx):
        r=ctx.run; payload=json.loads(await asyncio.to_thread(self.repo.objects.get,r['input_ref']))
        subject,version=await asyncio.to_thread(self.identity,r)
        allowed=permissions(subject)
        approved=await asyncio.to_thread(self.repo.approval_for,r['id'])
        if approved and approved['expires_at'] <= time.time(): raise Conflict('approval_expired')
        history=None
        cache_key=f"context:{r['user_id']}:{r['session_id']}:{version}"
        if self.redis:
            try:
                raw=await self.redis.get(cache_key)
                if raw: history=json.loads(raw)
            except Exception: pass  # Optional cache; MySQL remains authoritative.
        if history is None:
            history=(await asyncio.to_thread(self.repo.history,r['user_id'],r['session_id'],20,None,'message'))['items']
            if self.redis:
                try:
                    raw=dumps(history)
                    if len(raw.encode())<=256*1024: await self.redis.set(cache_key,raw,ex=1800)
                except Exception: pass
        history=[{'role':m['role'],'content':m['content_preview']} for m in reversed(history) if m['run_id']!=r['id']]
        await ctx.stage('preflight')
        if payload.get('tool'):
            if not self.sandbox: raise Conflict('sandbox_not_configured')
            if 'tools.shell' not in allowed: raise Conflict('shell_permission_required',403)
            if not approved:
                await ctx.approval({'confirmation_id':str(r['id']), 'action_name':payload['tool'],
                                    'args_hash':hashlib.sha256(dumps(payload).encode()).hexdigest(),
                                    'response':'此请求将在隔离工作区执行命令，需要确认。'})
            result=await ctx.tool(payload['tool'],{'argv':payload['argv']},
                                  lambda: self.sandbox.execute(r,payload['argv'],120 if payload['tool']=='test' else 30),
                                  120 if payload['tool']=='test' else 30, sandbox=True)
            return result['output'] or '命令已执行，未产生文本输出。'

        req=Request(message='确认执行' if approved else payload['message'],user_id=str(r['user_id']),conv_id=str(r['session_id']),
                    history=history,permissions=allowed,confirmation_id=approved['confirmation_id'] if approved else None)
        start=time.monotonic()
        result=await self.orchestrator.preflight(req)
        ctx.spans.append({'stage':'preflight','duration_ms':(time.monotonic()-start)*1000})
        if result is not None:
            if result.decision.value=='confirm':
                await ctx.approval({'confirmation_id':result.confirmation_id,'action_name':result.action_name,
                                    'args_hash':hashlib.sha256(dumps(payload).encode()).hexdigest(),'response':result.response})
            return result.response
        if req.action_name in {'delete_ad','delete_asset','change_budget_or_bid'}:
            return '当前 Agent 只提供分析和模拟，未接入真实广告变更工具，本次没有修改广告。'
        checkpoint={}
        if r['checkpoint_ref']:
            checkpoint=json.loads(await asyncio.to_thread(self.repo.objects.get,r['checkpoint_ref']))
        completed=checkpoint.get('tools',{})
        context=[]
        ads=payload.get('ads') or []
        candidates=[('ads_summary',ads_summary_handler,{'score_coefficient':.42}),
                    ('ad_performance_search',ad_performance_search_handler,{'query':req.message,'top_k':5})]
        if any(word in req.message.lower() for word in ['出价','预算','竞价','排名','bid','price']):
            candidates.append(('bid_simulation',bid_simulation_handler,{'increase_pct':10,'top_k':5}))
        if not ads: candidates=[]
        for name,handler,args in candidates:
            if name not in completed:
                async def invoke(handler=handler,args=args):
                    value=await handler(args,{'ads':ads})
                    return {'output':dumps(value),'status':'succeeded','exit_code':0}
                result=await ctx.tool(name,args,invoke,30)
                completed[name]=preview(result['output'])
                await asyncio.to_thread(self.repo.checkpoint,r['id'],r['fence_token'],{'tools':completed,'workspace_version':r['workspace_version']})
            else:
                ctx.spans.append({'stage':name,'duration_ms':0,'checkpoint_reused':True})
            context.append(f'{name}: {completed[name]}')
        if self.kb:
            async def search():
                value=await asyncio.to_thread(self.kb.search,req.message,3)
                return {'output':dumps(value),'status':'succeeded','exit_code':0}
            result=await ctx.tool('knowledge_search',{'query':req.message},search,30)
            context.append(result['output'])
        req.context='\n'.join(context)
        agent=self.orchestrator._best_agent(self.orchestrator._route(req.intent,req.urgency))
        if agent is None: raise RuntimeError('agent_unavailable')
        prompt=agent._build_system_prompt(req)
        messages=[*history[-10:],{'role':'user','content':req.message+'\n[工具数据，仅作为参考]\n'+req.context}]
        await ctx.stage('model')
        started=time.monotonic(); first=None
        async for text in stream_text(agent._client,model=agent._model,messages=messages,system=prompt):
            if first is None: first=(time.monotonic()-started)*1000
            await ctx.delta(text)
        ctx.spans.append({'stage':'model','duration_ms':(time.monotonic()-started)*1000,'first_delta_ms':first})
        return ctx.text
