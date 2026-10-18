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
from core.rag_evidence import (EvidenceConfig, blocked_response, check_evidence,
                               prepare_chunks, render_knowledge)
from mcp.ads_tools import ads_summary_handler, ad_performance_search_handler, bid_simulation_handler
from mcp.tool_manager import MCPToolManager, Tool
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
        EvidenceConfig.from_env()
        key=os.getenv('ANTHROPIC_API_KEY') or os.getenv('OPENROUTER_API_KEY')
        if not key and not os.getenv('DASHSCOPE_API_KEY') and not os.getenv('ARK_API_KEY'): raise RuntimeError('model credential is required for Worker')
        base=os.getenv('ANTHROPIC_BASE_URL') or ('https://openrouter.ai/api' if os.getenv('OPENROUTER_API_KEY') else None)
        model=os.getenv('ANTHROPIC_MODEL') or os.getenv('OPENROUTER_MODEL','claude-3-5-sonnet-20241022')
        from core.model_client import business_model_config
        qwen = business_model_config()
        if qwen:
            key, base, model = qwen["api_key"], qwen["base_url"], qwen["model"]
        skills=SkillManager(root_dir=os.getenv('ECHOMIND_SKILLS_DIR','skills'),max_prompt_chars=5000); skills.load()
        self.orchestrator=AgentOrchestrator(api_key=key,base_url=base,model=model,skill_manager=skills)
        self.orchestrator._decision_engine.pending_store=SQLActions(repo)
        self.orchestrator._decision_engine.clarification_store=SQLClarifications(repo,600)
        self.kb=None
        self.rag_manager=None
        if os.getenv('HOSTING_RAG_ENABLED','true').strip().lower()=='true':
            from mcp.knowledge_base import KnowledgeBase
            # RAG is optional; a broken configured service must fail startup, not silently disappear.
            self.kb=KnowledgeBase(chroma_host=os.getenv('CHROMA_HOST','localhost'), chroma_port=int(os.getenv('CHROMA_PORT','8001')), chroma_path=os.getenv('CHROMA_PERSIST_DIRECTORY','data/chroma'))
            self.rag_manager=MCPToolManager(api_key=key,base_url=base,model=model)
            self.rag_manager.register(Tool(name='knowledge_search',description='检索运营知识',
                handler=self.kb.search_handler,schema={'type':'object','properties':{
                    'query':{'type':'string'},'top_k':{'type':'integer'}},'required':['query']},
                cache_ttl=300,timeout_s=5))

    async def close(self):
        seen=set()
        clients=[self.orchestrator._intent_recognizer.client]
        if self.rag_manager: clients.append(self.rag_manager._client)
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
        performance=payload.get('performance_context')
        if performance:
            # Durable input contains the full server snapshot; checkpoint previews may truncate.
            context.append('performance_context: '+dumps(performance))
            ctx.spans.append({'stage':'performance_context','source':performance['source'],
                              'window':performance.get('window'), 'scope':performance.get('scope'),
                              'measured_count':performance.get('measured_count')})
        ad_context=payload.get('ad_context')
        ads=(ad_context.get('items') or []) if ad_context else payload.get('ads') or []
        candidates=[('ads_summary',ads_summary_handler,{'score_coefficient':.42}),
                    ('ad_performance_search',ad_performance_search_handler,{'query':req.message,'top_k':5})]
        if any(word in req.message.lower() for word in ['模拟','simulation']) and any(word in req.message.lower() for word in ['出价','预算','竞价','排名','bid','price']):
            candidates.append(('bid_simulation',bid_simulation_handler,{'increase_pct':10,'top_k':5}))
        if not ads: candidates=[]
        for name,handler,args in candidates:
            if name not in completed:
                async def invoke(handler=handler,args=args):
                    value=await handler(args,{'ads':ads,'ad_context':ad_context,'performance_context':performance})
                    return {'output':dumps(value),'status':'succeeded','exit_code':0}
                result=await ctx.tool(name,args,invoke,30)
                completed[name]=preview(result['output'])
                await asyncio.to_thread(self.repo.checkpoint,r['id'],r['fence_token'],{'tools':completed,'workspace_version':r['workspace_version']})
            else:
                ctx.spans.append({'stage':name,'duration_ms':0,'checkpoint_reused':True})
            context.append(f'{name}: {completed[name]}')
        agent=self.orchestrator._best_agent(self.orchestrator._route(req.intent,req.urgency))
        if agent is None: raise RuntimeError('agent_unavailable')
        retained=[]
        if self.kb:
            marketing=agent.agent_type==AgentType.ADS
            queries=['广告投放优化 CTR CPC CPA 转化事件 归因窗口 退款 消耗',
                     '素材优化 A/B 测试 单变量 预算 小预算测试'] if marketing else []
            async def search():
                value=await self.rag_manager.search_with_rewrite('knowledge_search',req.message,top_k=8,
                    stage_timeout=5, additional_queries=queries, check_retrieved_evidence=False)
                if not value.success: raise RuntimeError('retrieval_failed')
                return {'output':dumps(value.data),'status':'succeeded','exit_code':0}
            chunks, retrieval_error = [], None
            try:
                result=await ctx.tool('knowledge_search',{'query':req.message,'top_k':8,'additional_queries':queries},search,15.5)
                values=json.loads(result['output'])
                if result.get('status') != 'succeeded' or not isinstance(values,list):
                    retrieval_error='retrieval_failed'
                else:
                    chunks=prepare_chunks(values,8)
            except Conflict:
                raise
            except Exception:
                retrieval_error='retrieval_failed'
            business={'tool_context':'\n'.join(context)} if context else {}
            if req.intent and req.intent.value=='creative_generation':
                business['creative_brief']=req.message
            evidence_config=EvidenceConfig.from_env()
            # Hosted advice has a larger bounded snapshot than the simple /chat input.
            timeout=float(os.getenv('HOSTING_RAG_EVIDENCE_TIMEOUT_SECONDS','16'))
            if not 0 < timeout <= 18: raise ValueError('invalid_hosting_evidence_timeout')
            decision=await check_evidence(agent._client,agent._model,req.message,chunks,business,
                retrieval_error=retrieval_error,request_id=str(r['id']),
                config=EvidenceConfig(evidence_config.enabled,evidence_config.threshold,timeout))
            ctx.spans.append({'stage':'rag_evidence','duration_ms':decision.latency_ms,
                              'evidence':decision.to_dict()})
            if decision.status in {'insufficient','unavailable'}:
                # Checkpoint previews can be truncated JSON; never infer missing fields.
                records={}
                try:
                    records['bid_simulation']=json.loads(completed.get('bid_simulation','{}'))
                except (ValueError,TypeError):
                    pass
                response=blocked_response(decision,records)
                await ctx.delta(response)
                return response
            retained=[item for item in chunks if item['id'] in decision.retained_ids]
            if marketing and not retained:
                response='本轮未获得通过检查的营销知识片段，暂不能给出基于知识库的投放优化建议。请补充相关知识或缩小问题范围。'
                await ctx.delta(response)
                return response
            context.append(render_knowledge(retained,checked=decision.status!='disabled'))
            ctx.spans.append({'stage':'knowledge_sources','checked':decision.status=='sufficient',
                'sources':[{'id':item['id'],'title':item.get('title',''),
                            'source_url':item.get('source_url')} for item in retained]})
            if decision.status=='sufficient':
                context.append('只依据本轮已检查的片段和工具数据陈述事实；历史回答不证明事实，不补写未提供的指标和收益。')
        req.context='\n'.join(context)
        prompt=agent._build_system_prompt(req)
        if ads or performance:
            prompt+='\n投放分析要求：把本轮账本数值作为广告实际投放数据进行完整分析，直接给出动作。delivery_status=delivered 的广告按已投放处理，paused 为真表示已暂停；not_delivered 表示未投放，不当成投放失败。窗口和样本范围放在末尾简短说明，不添加模拟、演示或假设数据标签。'
            prompt+='窗口指标以 performance_context 为准，不能与广告库累计点击混算；未知指标不能当零。金额单位 fen 除以100才是元。'
            prompt+='未投放或曝光为零的素材不能判为失败；material_changed 为真时历史效果不能归给当前素材。ROAS不是利润。'
            prompt+='给出最多三个动作，每个包含广告名称、具体操作、简短理由和观察指标。不要在正文展示广告ID或知识片段ID；引用依据放在末尾“参考依据”，用来源标题。'
            prompt+='总正文控制在250至350个汉字以内，不复述所有字段；每个动作只引用一至两个关键指标。比较最高/最低必须核对全部窗口数据。'
            prompt+='面向商家用中文表达状态与指标；金额直接用元，不展示 fen/100 或内部字段名。'
            prompt+='相关性不能证明原因，同素材的效果差异只能形成待验证假设，不能断言素材、人群或标题就是原因。'
            prompt+='缺少可用预算或毛利时不给确定加价金额或收益承诺，可给当前账本上的测试优先级并列出缺失数据。'
            prompt+='无投放数据时提出可执行的小预算验证方案，不能把选中样本称为账户全量。仅提供建议，不执行改价。'
        prompt+='\n商家阅读格式：面向第一次投广告的人，用短句和日常中文。第一行用“建议：”直接说现在最该做什么，最多30字。关键操作和关键数字用 **加粗**，每段最多两处，不整段加粗。随后给出一至三个最必要的动作，不凑数，用“## 1. 广告名称：具体操作”作为标题，每项仅三行：“怎么做：”“为什么：”“看什么：”。每行尽量不超过30字，一行只讲一件事，操作、原因和观察指标分别换行，理由最多两个关键数字。统计信息禁止写成长段：日期与广告数量单独一行；曝光与点击一行，花费与订单一行，收入与每元广告费带来的收入一行。缺失数据另起一行。每行最多两个指标，不展示delivered、CPC、CTR或fen/100等内部表达。末尾“统计范围：”一行；需要来源时另写“参考依据：”列来源标题，不能虚构标题。不要用Markdown表格、代码块、长段落或内部ID。把ROAS说成“每花1元广告费带来多少元收入（不是利润）”，CPA说成“获得一笔订单的广告费”，CTR说成“看到广告后点击的人占比”；不使用放量、高转化组、归因净收入等行话。数据不足时直说“先保持出价”，紧接着说能做的事和缺少的关键数据，别堆免责声明。仅优化表达，不省略影响决策的数据限制，不承诺收益。用户问简单问题时直接简短回答，不强套三项。'
        messages=[*history[-10:],{'role':'user','content':req.message+'\n[工具数据，仅作为参考]\n'+req.context}]
        await ctx.stage('model')
        started=time.monotonic(); first=None
        model_status, model_error='succeeded', None
        try:
            async for text in stream_text(agent._client,model=agent._model,messages=messages,system=prompt,max_tokens=3072):
                if first is None: first=(time.monotonic()-started)*1000
                await ctx.delta(text)
        except asyncio.CancelledError:
            model_status, model_error='cancelled', 'run_cancelled'
            raise
        except Exception as exc:
            from core.model_client import ModelResponseError
            model_status='failed'
            model_error=str(exc) if isinstance(exc,ModelResponseError) else type(exc).__name__
            raise
        finally:
            ctx.spans.append({'stage':'model','status':model_status,'error_code':model_error,
                              'duration_ms':(time.monotonic()-started)*1000,'first_delta_ms':first})
        if retained:
            sources='\n\n本轮检索依据：\n'+'\n'.join(
                f"{item.get('title','知识片段')}（ID: {item['id']}）"+
                (f"\n{item['source_url']}" if item.get('source_url') else '') for item in retained)
            await ctx.delta(sources)
        return ctx.text
