import copy
import concurrent.futures
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from endocrine.clock import FrozenClock
from endocrine.integration import conclusion_feed
from endocrine.model import Config,DomainError,Event,canonical,digest,iso,timestamp
from endocrine.service import Service
from endocrine.store import Repository
from endocrine.grounding import validate_review
from ob_bridge.api import make_server as bridge_server
from ob_bridge.backend import MCPBackend,pointer
from ob_bridge.contracts import archive_item,local_or_https,normalize_memory
from ob_bridge.feed import poll
from ob_bridge.service import BridgeService
from ob_bridge.store import ArchiveStore

NOW=timestamp('2026-10-18T05:00:00Z')
REQUEST={'agent_id':'a','actor_id':'p','conversation_id':'c','query':'完整问题，不是关键词列表',
         'as_of':iso(NOW),'limit':8}

def memory(**changes):
    return {'id':'m1','scope_id':'ap','text':'过去的摘要：用户当时希望先询问再公开个人信息。',
        'kind':'summary','origin_system':'ob_native','known_at':'2026-10-01T10:00:00+08:00',
        'valid_from':None,'valid_to':None,'source_refs':['source-1'],'lineage':[],
        'relevance':.9,**changes}

def item(n=1):
    return {'archive_id':'archive'+str(n),'agent_id':'a','actor_id':'p','source_event_id':'e'+str(n),
        'source_seq':n,'occurred_at':iso(NOW),'known_at':iso(NOW),
        'conclusion':{'trust_delta':-.01,'expectation_status':None,'classifier_status':'model',
                      'interpretation':'agent_assessment_not_observed_world_fact'},
        'origin_system':'event_affect','lineage':['event_affect:a:e'+str(n)]}

class Backend:
    def __init__(self,items=()):self.items=list(items);self.writes=[];self.fail=False
    def search(self,request,timeout):return 'ap',copy.deepcopy(self.items)
    def archive(self,body,timeout):
        self.writes.append(copy.deepcopy(body))
        if self.fail:raise TimeoutError('after commit, reply lost')
        return {'archive_id':body['archive_id'],'fingerprint':digest(body),'status':'stored'}


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=ArchiveStore(Path(self.tmp.name)/'bridge.sqlite')
        self.backend=Backend([memory()]);self.bridge=BridgeService(self.backend,self.store,lambda:NOW)

    def test_summary_is_not_observation_and_query_is_not_keywordized(self):
        result=self.bridge.search(REQUEST);r=result['items'][0]
        self.assertEqual(r['source'],'ob_memory');self.assertEqual(r['provenance'],'ob_summary')
        self.assertEqual(r['epistemic_status'],'external_memory_unverified')
        self.assertFalse(result['complete']);self.assertEqual(result['state_events_created'],0)

    def test_cross_scope_memory_fails_whole_search(self):
        self.backend.items.append(memory(id='private',scope_id='other'))
        with self.assertRaisesRegex(DomainError,'ob_scope_mismatch'):self.bridge.search(REQUEST)

    def test_feedback_loops_excluded_even_with_new_ids(self):
        self.backend.items=[memory(id='renamed',origin_system='event_affect'),
                            memory(id='native-title',lineage=['event_affect:a:e1'])]
        result=self.bridge.search(REQUEST)
        self.assertEqual(result['items'],[]);self.assertEqual(result['excluded']['self_derived_feedback_excluded'],2)

    def test_future_knowledge_not_leaked_and_invalid_timezone_rejected(self):
        self.backend.items=[memory(known_at='2026-10-19T00:00:00Z')]
        self.assertEqual(self.bridge.search(REQUEST)['items'],[])
        self.backend.items=[memory(known_at='2026-10-01T00:00:00')]
        with self.assertRaises(DomainError):self.bridge.search(REQUEST)

    def test_validity_is_separate_from_recency_no_arbitrary_forgetting(self):
        self.backend.items=[memory(valid_to='2026-10-10T00:00:00Z'),
            memory(id='older-but-relevant',known_at='2020-01-01T00:00:00Z',relevance=.95)]
        records=self.bridge.search(REQUEST)['items']
        self.assertEqual(records[0]['id'],'older-but-relevant')
        self.assertFalse(records[1]['valid_at_query_time'])

    def test_unknown_bounds_and_changed_version(self):
        self.backend.items=[memory(),memory(valid_to='2026-10-10T00:00:00Z')]
        with self.assertRaisesRegex(DomainError,'ob_record_version_conflict'):self.bridge.search(REQUEST)
        with self.assertRaises(DomainError):normalize_memory(memory(valid_from='2026-10-11T00:00:00Z',valid_to='2026-10-10T00:00:00Z'),REQUEST,'ap')

    def test_queue_duplicate_is_idempotent_and_conflict_explicit(self):
        a=item();self.store.queue(a,NOW);self.store.queue(a,NOW)
        self.assertEqual(len(self.store.rows()),1)
        changed=copy.deepcopy(a);changed['conclusion']['trust_delta']=.5
        with self.assertRaisesRegex(DomainError,'archive_id_content_conflict'):self.store.queue(changed,NOW)

    def test_lost_receipt_never_blindly_retries(self):
        self.store.queue(item(),NOW);self.backend.fail=True
        self.assertEqual(self.bridge.dispatch_one()['status'],'unknown')
        self.bridge.dispatch_one();self.assertEqual(len(self.backend.writes),1)
        reopened=ArchiveStore(self.store.path)
        BridgeService(self.backend,reopened,lambda:NOW+100000).dispatch_one()
        self.assertEqual(len(self.backend.writes),1)

    def test_crash_sending_recovers_unknown(self):
        self.store.queue(item(),NOW);self.store.claim(NOW)
        self.assertEqual(self.store.recover_interrupted(NOW+100),1)
        self.assertEqual(self.bridge.dispatch_one()['status'],'no_eligible_job')

    def test_mismatched_receipt_does_not_mark_delivered(self):
        self.store.queue(item(),NOW)
        self.backend.archive=lambda body,timeout:{'archive_id':'wrong','fingerprint':digest(body),'status':'stored'}
        self.assertEqual(self.bridge.dispatch_one()['status'],'unknown')

    def test_concurrent_claims_obey_global_daily_cap(self):
        for i in range(1,14):self.store.queue(item(i),NOW)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            claims=list(pool.map(lambda _:self.store.claim(NOW),range(18)))
        self.assertEqual(sum(c is not None for c in claims),10)
        self.assertIsNone(self.store.claim(NOW-86400))
        self.assertIsNotNone(self.store.claim(NOW+86400))

    def test_cursor_and_queue_atomic_and_stale_poller_fenced(self):
        self.store.initialize_cursor('a',0)
        self.store.ingest('a',0,1,[item()],NOW)
        self.assertEqual(self.store.cursor('a'),1)
        with self.assertRaises(DomainError):self.store.ingest('a',0,1,[item()],NOW)
        bad=item(2);bad['agent_id']='other'
        with self.assertRaises(DomainError):self.store.ingest('a',1,2,[bad],NOW)
        self.assertEqual(self.store.cursor('a'),1);self.assertEqual(len(self.store.rows()),1)

    def test_consolidation_reuses_same_ids_without_writes(self):
        self.store.queue(item(),NOW)
        result=self.bridge.consolidation('2026-10-18T00:00:00Z','2026-10-19T00:00:00Z')
        self.assertEqual(result['items'][0]['archive_id'],'archive1');self.assertEqual(result['writes_performed'],0)
        self.assertEqual(self.backend.writes,[])

    def test_archive_schema_cannot_carry_raw_text_or_persona(self):
        for key in ('text','summary','persona','token'):
            with self.subTest(key=key),self.assertRaises(DomainError):archive_item({**item(),key:'private'})

    def test_no_plaintext_external_credentials_and_no_guessed_route(self):
        with self.assertRaises(DomainError):local_or_https('http://server.example.invalid/ombre/mcp')
        config={'endpoint':'http://127.0.0.1:18001/mcp','routes':[],'bindings':{}}
        b=MCPBackend(config)
        with self.assertRaisesRegex(DomainError,'ob_scope_route_missing_or_ambiguous'):b.scope(REQUEST)
        with self.assertRaisesRegex(DomainError,'ob_tool_binding_missing'):b.call('search',{},8)

    def test_json_pointer_explicit_mapping_no_fuzzy_field_guess(self):
        self.assertEqual(pointer({'a':[{'b/c':2}]},'/a/0/b~1c'),2)
        with self.assertRaises(DomainError):pointer({'wrong':[]},'/memories')

    def test_bridge_http_auth_origin_and_archive_default_disabled(self):
        server=bridge_server(self.bridge,token='fixture',port=0)
        t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),t.join()))
        def call(path,body,token='fixture',origin=None):
            headers={'X-Token':token,'Content-Type':'application/json'}
            if origin:headers['Origin']=origin
            req=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+path,json.dumps(body).encode(),headers)
            try:
                with urllib.request.urlopen(req,timeout=5) as r:return r.status,json.load(r)
            except urllib.error.HTTPError as e:return e.code,json.load(e)
        self.assertEqual(call('/bridge/search',REQUEST,'wrong')[0],403)
        self.assertEqual(call('/bridge/search',REQUEST,origin='https://evil.invalid')[0],403)
        self.assertEqual(call('/bridge/search',REQUEST)[0],200)
        self.assertEqual(call('/bridge/archive',{'agent_id':'a'})[1]['error'],'ob_archive_disabled')


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.repo=Repository(Path(self.tmp.name)/'affect.sqlite',Config())
        self.service=Service(self.repo,clock=FrozenClock(NOW))

    def event(self,eid,**changes):
        return {'agent_id':'a','event_id':eid,'occurred_at':iso(NOW),'source':'user_message',
            'provenance':'observed_message','actor_id':'p','conversation_id':'c','text':'PRIVATE-ORIGINAL',**changes}

    def test_pending_gap_not_skipped(self):
        self.repo.reserve(Event.parse(self.event('pending')),NOW)
        page=conclusion_feed(self.repo,'a')
        self.assertEqual(page['next_cursor'],0);self.assertTrue(page['blocked_on_pending'])
        self.assertEqual(page['stable_start_cursor'],0)

    def test_feed_expectation_uses_committed_trace_and_no_raw_text(self):
        self.service.submit(self.event('add',source='system',provenance='operator_assertion',event_type='expect_add',
            payload={'id':'x','expect':'PRIVATE-EXPECTATION','importance':.5,'deadline':iso(NOW+100),'mutual':False}))
        self.service.submit(self.event('resolve',source='system',provenance='operator_assertion',event_type='expect_resolve',
            payload={'id':'x','result':'unmet'}))
        page=conclusion_feed(self.repo,'a');self.assertEqual(len(page['items']),1)
        self.assertEqual(page['items'][0]['conclusion']['expectation_status'],'unmet')
        self.assertNotIn('PRIVATE',canonical(page));archive_item(page['items'][0])
        self.assertEqual(conclusion_feed(self.repo,'a',page['next_cursor'])['items'],[])

    def test_context_only_and_legacy_replay_not_archived(self):
        self.service.submit(self.event('normal'))
        self.service.submit(self.event('assistant',source='agent_message',actor_id='a'))
        page=conclusion_feed(self.repo,'a')
        self.assertEqual(page['items'],[]);self.assertTrue(page['complete_to_high_watermark'])


class ExternalGroundingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.repo=Repository(Path(self.tmp.name)/'affect.sqlite',Config())
        self.service=Service(self.repo,clock=FrozenClock(NOW))
        self.service.submit({'agent_id':'a','event_id':'u','actor_id':'p','conversation_id':'c',
            'source':'user_message','provenance':'observed_message','occurred_at':iso(NOW),'text':'继续聊聊。'})
        self.scope={'agent_id':'a','actor_id':'p','conversation_id':'c','source_event_id':'u'}

    def test_ob_summary_cited_without_becoming_experience_or_verified_fact(self):
        r,_=normalize_memory(memory(),REQUEST,'ap')
        class External:
            def search(self,*args):return [r],{'status':'available','complete':False}
        class Model:
            def complete(self,*args,**kw):return {'summary':'此前摘要提示边界偏好，仍需确认。','evidence':[
                {'event_id':r['event_id'],'quote':'先询问再公开个人信息','relation':'supports'}]}
        self.service.grounded.external_memory=External();self.service.grounded.model=Model()
        before=self.repo.export()
        result=self.service.grounded.reflect({**self.scope,'hypothesis':'用户希望先询问再公开'})
        self.assertEqual(result['status'],'tentative_external');self.assertEqual(result['support_events'],0)
        self.assertEqual(result['external_support_items'],1);self.assertFalse(result['scope_complete'])
        self.assertEqual(self.repo.export(),before)

    def test_ob_outage_explicit_does_not_block_local_event_or_fabricate_context(self):
        class External:
            def search(self,*args):raise TimeoutError('OB down')
        class Model:
            def complete(self,*args,**kw):return {'summary':'没有足够证据','evidence':[]}
        self.service.grounded.external_memory=External();self.service.grounded.model=Model()
        result=self.service.grounded.reflect({**self.scope,'hypothesis':'偏好'})
        self.assertEqual(result['external_memory']['status'],'unavailable')
        self.assertEqual(result['status'],'unsubstantiated')
        self.assertTrue(self.service.submit({'agent_id':'a','event_id':'u2','actor_id':'p','conversation_id':'c',
            'source':'user_message','occurred_at':iso(NOW),'text':'新的消息'})['success'])

    def test_summary_cannot_certify_assistant_past_or_completed_action(self):
        r,_=normalize_memory(memory(text='角色已经答应过'),'ap' if False else REQUEST,'ap')
        review={'decision':'accept','issues':[],'claims':[{'kind':'prior_self','span':'我答应过',
            'status':'supported','evidence':[{'event_id':r['event_id'],'quote':'答应过'}]}]}
        self.assertEqual(validate_review(review,'我答应过',[r])['decision'],'revise')
