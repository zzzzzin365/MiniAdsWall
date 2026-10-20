import asyncio
import json
import os
import time
import unittest
from unittest.mock import patch
from sqlalchemy import select, update
from tests.test_durable_memory import MemoryTests
from memory import durable_schema as ms
from memory.extended_schema import candidates
from memory.context_service import Scope,MemoryContextService
from marketing.feedback.procedures import ProcedureRepository
from hosting.repository import Conflict
from hosting.api import _object_allowed

class LifecycleTests(MemoryTests):
    def test_preference_history_has_each_value(self):
        p=self.store.preference(self.scope,'优化目标',['咨询'],0)
        self.store.preference(self.scope,'优化目标',['成交'],1)
        revisions=self.store.revisions(self.scope,'preference',p['id'])
        self.assertEqual([json.loads(r['payload'])['value_json'] for r in revisions],['["咨询"]','["成交"]'])
    def test_conflict_candidate_requires_explicit_acceptance(self):
        self.store.preference(self.scope,'优化目标',['成交'],0,source_order=1)
        self.finish();job=self.store.claim()
        self.store.complete(job,'摘要',[{'field':'优化目标','quote':'以后都优先获取咨询','values':['获取咨询']}])
        pending=self.store.candidates(self.scope)
        self.assertEqual(len(pending),1)
        with self.assertRaises(Conflict):self.store.resolve_candidate(self.scope,pending[0]['id'],True,0)
        self.store.resolve_candidate(self.scope,pending[0]['id'],True,1)
        self.assertEqual(json.loads(self.store.list_preferences(self.scope)[0]['value_json']),['获取咨询'])
    def test_cross_session_episodes_are_relevant_and_scoped(self):
        self.finish();self.store.complete(self.store.claim(),'用户长期经营咖啡店，关注咨询。',[])
        other=self.repo.create_session(self.user,self.identity['workspace_id'],'other')
        scope=Scope(self.user,self.identity['workspace_id'],other['id'])
        self.assertIn('咖啡店',MemoryContextService(self.store).build(scope,'咖啡店怎么做')['text'])
        self.assertFalse(self.store.episodes(scope,'完全不相关的话题'))
    def test_source_erasure_invalidates_derived_memory_and_old_snapshots(self):
        run=self.finish();self.store.complete(self.store.claim(),'用户长期关注咨询。',[])
        from hosting import schema as hs
        with self.repo.engine.connect() as c:
            msg=c.execute(select(hs.messages).where(hs.messages.c.run_id==run['id'],hs.messages.c.role=='user')).mappings().one()
        self.store.suppress_source(self.scope,msg['id'])
        self.assertEqual(self.store.read(self.scope)['items'],[])
        self.assertFalse(_object_allowed(self.repo,self.repo.get_run(self.user,run['id']),'a'*64))
        self.assertGreater(self.store.history_cutoff(self.scope),0)
    def test_dead_job_replay_is_owned_and_resets_attempts(self):
        self.finish();job=self.store.claim()
        with self.repo.engine.begin() as c:c.execute(update(ms.jobs).where(ms.jobs.c.id==job['id']).values(status='dead',attempts=5))
        self.store.replay_job(self.scope,job['id'])
        self.assertEqual(self.store.claim()['attempts'],1)
    def test_pinned_memory_never_reintroduces_changed_or_revoked_values(self):
        self.store.preference(self.scope,'优化目标',['咨询'],0)
        service=MemoryContextService(self.store);first=service.build(self.scope)
        self.store.preference(self.scope,'优化目标',['成交'],1)
        self.assertEqual(service.build(self.scope,pinned=first['sources'])['text'],'')
    def procedure(self):
        return dict(title='咖啡店素材测试流程',objective='咖啡店素材测试',conditions=['咖啡店'],
            steps=['先固定一个变量，再记录观察结果'],limitations=['没有效果保证，需检查样本'],source_title='人工审核的操作规范')
    def test_procedures_require_review_permission_and_match_conditions(self):
        repo=ProcedureRepository(self.repo)
        draft=repo.save(self.scope,self.procedure())
        with self.assertRaises(Conflict):repo.save(self.scope,{},draft['id'],1,'review')
        with patch.dict(os.environ,ACTION_PERMISSION_MAP=json.dumps({'alice':['memory.review_procedure']}),AGENT_EXPERIENCE_READ_ENABLED='true'):
            repo.save(self.scope,{},draft['id'],1,'review')
            bundle=MemoryContextService(self.store).build(self.scope,'咖啡店素材测试怎么做')
            self.assertIn('先固定一个变量',bundle['text'])
            self.assertNotIn('先固定一个变量',MemoryContextService(self.store).build(self.scope,'服装店素材测试')['text'])
            repo.save(self.scope,{},draft['id'],2,'withdraw')
            self.assertNotIn('先固定一个变量',MemoryContextService(self.store).build(self.scope,'咖啡店素材测试')['text'])
    def test_new_statement_after_retraction_requires_confirmation(self):
        self.store.preference(self.scope,'优化目标',['成交'],0,source_order=1)
        self.store.preference(self.scope,'优化目标',[],1,source_order=2)
        self.finish();self.store.complete(self.store.claim(),'用户重新声明目标。',[{'field':'优化目标','quote':'以后都优先获取咨询','values':['获取咨询']}])
        self.assertFalse(any(i['kind']=='preference' for i in self.store.read(self.scope)['items']))
        pending=self.store.candidates(self.scope)
        self.assertEqual(len(pending),1)
        self.store.resolve_candidate(self.scope,pending[0]['id'],True,2)
        self.assertTrue(any(i['kind']=='preference' for i in self.store.read(self.scope)['items']))

    def test_source_erasure_blocks_sse_and_redacts_derived_history(self):
        run=self.finish()
        from hosting import schema as hs
        with self.repo.engine.connect() as c:
            source=c.execute(select(hs.messages).where(hs.messages.c.run_id==run['id'],hs.messages.c.role=='user')).mappings().one()
        self.store.suppress_source(self.scope,source['id'])
        with self.assertRaises(Conflict) as error:self.repo.read_events(self.user,run['id'],0)
        self.assertEqual(error.exception.status,410)
        history=self.repo.history(self.user,self.scope.session_id)['items']
        self.assertFalse(any('小规模测试' in r.get('content_preview','') for r in history))
    def test_negated_procedure_condition_does_not_match(self):
        repo=ProcedureRepository(self.repo);draft=repo.save(self.scope,self.procedure())
        with patch.dict(os.environ,ACTION_PERMISSION_MAP=json.dumps({'alice':['memory.review_procedure']})):
            repo.save(self.scope,{},draft['id'],1,'review')
        self.assertEqual(repo.search(self.scope,'不是咖啡店，不要推荐咖啡店素材测试'),[])

    def test_procedure_scope_and_unproven_outcomes(self):
        repo=ProcedureRepository(self.repo)
        body=self.procedure();body['steps']=['已验证有效，收益提升50%']
        with self.assertRaises(Conflict):repo.save(self.scope,body)
        alice=repo.save(self.scope,self.procedure())
        bob=self.repo.provision('bob');bs=self.repo.create_session(bob['user_id'],bob['workspace_id'],'b')
        scope=Scope(bob['user_id'],bob['workspace_id'],bs['id'])
        self.assertEqual(repo.list(scope),[])
        with self.assertRaises(Conflict):repo.save(scope,{},alice['id'],1,'review')
