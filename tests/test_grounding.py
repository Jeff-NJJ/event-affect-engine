import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from endocrine.api import make_server
from endocrine.clock import FrozenClock
from endocrine.grounding import HTTPDialogueModel, evidence_snapshot, validate_review
from endocrine.model import Config, DomainError, digest, iso, timestamp
from endocrine.service import Service
from endocrine.store import Repository

ACCEPT={'decision':'accept','issues':[],'claims':[]}

def revise(text,kind='unsupported_history'):
    return {'decision':'revise','issues':[{'kind':kind,'span':text,'reason':'缺少真实来源'}],'claims':[]}


class ScriptedModel:
    def __init__(self,*replies):self.replies=list(replies);self.calls=[];self.callback=None
    def complete(self,system,data,**kwargs):
        self.calls.append((system,copy.deepcopy(data),kwargs))
        if self.callback:self.callback()
        value=self.replies.pop(0)
        if isinstance(value,Exception):raise value
        return copy.deepcopy(value)


class GroundingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now=timestamp('2026-10-18T05:00:00Z')
        self.repo=Repository(Path(self.tmp.name)/'db.sqlite',Config())
        self.service=Service(self.repo,clock=FrozenClock(self.now),expression_profiles={'a':{
            'persona':{'identity':'独立、温和的虚构角色','values':'重视诚实和自主决定',
                      'boundaries':'未经同意不公开朋友的私人信息'}}})
        self.scope={'agent_id':'a','actor_id':'p','conversation_id':'c','source_event_id':'u1'}
        self.add('u1','“那可太好了”是反话。我很难过，只想让你听我说。')

    def add(self,eid,text,source='user_message',actor='p',conversation='c',agent='a'):
        return self.service.submit({'agent_id':agent,'event_id':eid,'source':source,
            'provenance':'observed_message','text':text,'actor_id':actor,
            'conversation_id':conversation,'occurred_at':iso(self.now)})

    def model(self,*values):
        self.service.grounded.model=ScriptedModel(*values)
        return self.service.grounded.model

    def test_unconfigured_does_not_fake_success(self):
        with self.assertRaisesRegex(DomainError,'dialogue_cloud_not_configured'):
            self.service.grounded.respond(self.scope)

    def test_hallucinated_prior_apology_repaired_without_becoming_history(self):
        bad='刚才我没听出你在说反话，抱歉。'
        good='听着挺难受的，我在听。'
        m=self.model(bad,revise(bad),good,ACCEPT)
        before=self.repo.export()
        result=self.service.grounded.respond(self.scope)
        self.assertEqual(result['reply'],good);self.assertEqual(result['attempts'],2)
        self.assertEqual(result['reply_hash'],digest(good))
        self.assertEqual(before,self.repo.export());self.assertEqual(result['messages_sent'],0)
        self.assertNotIn(bad,json.dumps(m.calls[-1][1]['records'],ensure_ascii=False))

    def test_failed_repair_withholds_both_drafts(self):
        bad='我已经查过日程，肯定有空。'
        self.model(bad,revise(bad),bad,revise(bad))
        result=self.service.grounded.respond(self.scope)
        self.assertFalse(result['success']);self.assertEqual(result['status'],'withheld')
        self.assertNotIn('reply',result);self.assertNotIn(bad,json.dumps(result,ensure_ascii=False))

    def test_abstention_stops_without_rewrite(self):
        bad='那个事情当然是真的。';r=revise(bad,'uncertain');r['decision']='abstain'
        m=self.model(bad,r)
        self.assertFalse(self.service.grounded.respond(self.scope)['success'])
        self.assertEqual(len(m.calls),2)

    def test_provider_failure_never_releases_draft(self):
        self.model('不应释放的草稿',DomainError('dialogue_provider_unavailable_or_invalid',503))
        with self.assertRaises(DomainError):self.service.grounded.respond(self.scope)

    def test_source_must_be_current_same_scope_user(self):
        self.model()
        for key,value in [('agent_id','other'),('actor_id','q'),('conversation_id','d'),('source_event_id','missing')]:
            with self.subTest(key=key),self.assertRaises(DomainError):
                self.service.grounded.respond({**self.scope,key:value})
        self.add('a1','实际已回复',source='agent_message',actor='a')
        with self.assertRaisesRegex(DomainError,'grounding_source_stale'):
            self.service.grounded.respond(self.scope)
        with self.assertRaisesRegex(DomainError,'grounding_source_scope_mismatch'):
            self.service.grounded.respond({**self.scope,'source_event_id':'a1'})

    def test_new_turn_during_generation_discards_result(self):
        m=self.model('我在听。',ACCEPT)
        def append():
            m.callback=None;self.add('u2','等一下，刚才那条发错了。')
        m.callback=append
        with self.assertRaisesRegex(DomainError,'grounding_source_stale'):
            self.service.grounded.respond(self.scope)

    def test_pending_new_turn_also_invalidates_result(self):
        self.model('我在听。',ACCEPT)
        from endocrine.model import Event
        event=Event.parse({'agent_id':'a','event_id':'pending','source':'user_message',
            'provenance':'observed_message','text':'新消息','actor_id':'p','conversation_id':'c','occurred_at':iso(self.now)})
        self.repo.reserve(event,self.now)
        with self.assertRaisesRegex(DomainError,'grounding_source_stale'):
            self.service.grounded.respond(self.scope)

    def test_other_people_and_conversations_not_exposed(self):
        self.add('private','SECRET',actor='q',conversation='other')
        self.add('u2','仍然只希望你听我说。');self.scope['source_event_id']='u2'
        m=self.model('我听着。',ACCEPT)
        self.service.grounded.respond(self.scope)
        self.assertNotIn('SECRET',json.dumps(m.calls,ensure_ascii=False))

    def test_shared_group_history_rejected_instead_of_leaking_assistant_replies(self):
        self.add('group','其他人的事情',actor='q')
        self.add('u2','我的事情');self.scope['source_event_id']='u2';self.model()
        with self.assertRaisesRegex(DomainError,'grounding_multi_actor_unsupported'):
            self.service.grounded.respond(self.scope)

    def test_user_claim_cannot_certify_assistant_autobiography(self):
        raw={'decision':'accept','issues':[],'claims':[{'kind':'prior_self','span':'我答应过',
            'status':'supported','evidence':[{'event_id':'u','quote':'你答应过'}]}]}
        result=validate_review(raw,'我答应过',[{'event_id':'u','source':'user_message','text':'你答应过'}])
        self.assertEqual(result['decision'],'revise')
        self.assertEqual(result['claims'][0]['status'],'unsupported')

    def test_own_utterance_does_not_prove_completed_action(self):
        raw={'decision':'accept','issues':[],'claims':[{'kind':'completed_action','span':'我查过日程',
            'status':'supported','evidence':[{'event_id':'a','quote':'我查过日程'}]}]}
        result=validate_review(raw,'我查过日程',[{'event_id':'a','source':'agent_message','text':'我查过日程'}])
        self.assertEqual(result['decision'],'revise')

    def test_actual_prior_statement_with_citation_can_pass(self):
        raw={'decision':'accept','issues':[],'claims':[{'kind':'prior_self','span':'我说过不能透露',
            'status':'supported','evidence':[{'event_id':'a','quote':'不能透露'}]}]}
        result=validate_review(raw,'我说过不能透露',[{'event_id':'a','source':'agent_message','text':'不能透露'}])
        self.assertEqual(result['decision'],'accept')

    def test_no_missing_persona_substitution(self):
        self.service.expression_profiles={};self.model()
        with self.assertRaisesRegex(DomainError,'authored_persona_required'):
            self.service.grounded.respond(self.scope)

    def test_review_hash_scope_and_readonly(self):
        self.model(ACCEPT);before=self.repo.export()
        result=self.service.grounded.review({**self.scope,'reply':'我在听。'})
        self.assertTrue(result['accepted']);self.assertEqual(result['reply_hash'],digest('我在听。'))
        self.assertEqual(self.repo.export(),before)

    def test_schema_and_literal_citation_fail_closed(self):
        for result in ({'decision':'accept','issues':[{'x':1}]},{'decision':'revise','issues':[]},
                       revise('不存在的片段'),{'decision':'accept','issues':[],'send':True}):
            with self.subTest(result=result),self.assertRaises(DomainError):validate_review(result,'原文')

    def test_caller_cannot_inject_history_or_persona(self):
        self.model()
        for key in ('history','persona','records','system_prompt'):
            with self.subTest(key=key),self.assertRaisesRegex(DomainError,'invalid_grounding_request'):
                self.service.grounded.respond({**self.scope,key:'override'})

    def test_support_and_counterevidence_preserved_no_promotion(self):
        self.add('pref1','平时我喜欢安静独处。')
        self.add('pref2','但今天我想和大家聚会。');self.scope['source_event_id']='pref2'
        self.model({'summary':'通常喜欢独处，但今天希望聚会。','evidence':[
            {'event_id':'pref1','quote':'喜欢安静独处','relation':'supports'},
            {'event_id':'pref2','quote':'今天我想和大家聚会','relation':'contradicts'}]})
        before=self.repo.export()
        result=self.service.grounded.reflect({**self.scope,'hypothesis':'用户在所有情境下都只喜欢独处'})
        self.assertEqual(result['status'],'disputed');self.assertEqual(result['support_events'],1)
        self.assertEqual(result['contradicting_events'],1);self.assertFalse(result['automatic_persona_promotion'])
        self.assertEqual(before,self.repo.export())

    def test_self_generated_belief_cannot_support_itself(self):
        self.add('a1','你一直只喜欢独处。',source='agent_message',actor='a')
        self.add('u2','继续聊吧');self.scope['source_event_id']='u2'
        self.model({'summary':'待验证的推测','evidence':[
            {'event_id':'a1','quote':'一直只喜欢独处','relation':'supports'}]})
        result=self.service.grounded.reflect({**self.scope,'hypothesis':'用户只喜欢独处'})
        self.assertEqual(result['status'],'unsubstantiated');self.assertEqual(result['support_events'],0)

    def test_fabricated_or_cross_scope_citation_rejected(self):
        for ref in ({'event_id':'other','quote':'内容','relation':'supports'},
                    {'event_id':'u1','quote':'完全不存在','relation':'supports'}):
            self.model({'summary':'推测','evidence':[ref]})
            with self.subTest(ref=ref),self.assertRaises(DomainError):
                self.service.grounded.reflect({**self.scope,'hypothesis':'用户偏好'})

    def test_copied_citations_not_counted_as_independent_events(self):
        refs=[{'event_id':'u1','quote':'反话','relation':'supports'},
              {'event_id':'u1','quote':'难过','relation':'supports'}]
        self.model({'summary':'暂定','evidence':refs})
        result=self.service.grounded.reflect({**self.scope,'hypothesis':'当前希望倾听'})
        self.assertEqual(result['support_events'],1);self.assertEqual(result['status'],'tentative')
        self.model({'summary':'暂定','evidence':[refs[0],refs[0]]})
        with self.assertRaisesRegex(DomainError,'duplicate_reflection_evidence'):
            self.service.grounded.reflect({**self.scope,'hypothesis':'当前希望倾听'})

    def test_context_limit_explicit_not_silent_partial_memory(self):
        for i in range(64):self.add('more'+str(i),'一段话')
        self.scope['source_event_id']='more63';self.model()
        with self.assertRaisesRegex(DomainError,'grounding_context_limit'):
            self.service.grounded.reflect({**self.scope,'hypothesis':'偏好'})

    def test_authentication_and_new_routes_real_http(self):
        self.model(ACCEPT)
        server=make_server(self.service,port=0,token='local-test-token')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            url='http://127.0.0.1:'+str(server.server_port)+'/v2/expression/review'
            data=json.dumps({**self.scope,'reply':'我在听。'}).encode()
            request=urllib.request.Request(url,data=data,headers={'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as denied:urllib.request.urlopen(request)
            self.assertEqual(denied.exception.code,403)
            request.add_header('X-Token','local-test-token')
            with urllib.request.urlopen(request) as response:result=json.load(response)
            self.assertTrue(result['accepted']);self.assertEqual(result['source_event_id'],'u1')
        finally:server.shutdown();server.server_close();thread.join()


class CloudContractTests(unittest.TestCase):
    def test_fences_budget_and_no_completion_accepted_on_length(self):
        import time
        model=HTTPDialogueModel('https://example.invalid/chat','test','test-key')
        response={'choices':[{'finish_reason':'stop','message':{'content':'```json\n{"decision":"accept","issues":[],"claims":[]}\n```'}}]}
        with patch('endocrine.grounding.request_json',return_value=response) as call:
            self.assertEqual(model.complete('prompt',{},deadline=time.monotonic()+150,structured=True),ACCEPT)
            self.assertEqual(call.call_args.args[1]['max_tokens'],8192)
            self.assertGreater(call.call_args.kwargs['timeout'],30)
        response['choices'][0]['finish_reason']='length'
        with patch('endocrine.grounding.request_json',return_value=response),self.assertRaises(DomainError):
            model.complete('prompt',{},deadline=time.monotonic()+60)

    def test_total_deadline_before_network_and_sanitized_failure(self):
        import time
        model=HTTPDialogueModel('https://example.invalid/chat','test','test-key')
        with patch('endocrine.grounding.request_json') as call,self.assertRaisesRegex(DomainError,'dialogue_total_deadline'):
            model.complete('p',{},deadline=time.monotonic()-1)
        call.assert_not_called()
        with patch('endocrine.grounding.request_json',side_effect=OSError('secret')):
            with self.assertRaisesRegex(DomainError,'dialogue_provider_unavailable_or_invalid'):
                model.complete('p',{},deadline=time.monotonic()+60)

    def test_configured_budgets_are_bounded_and_used(self):
        import time
        for options in ({'timeout':True},{'timeout':float('nan')},{'timeout':181},{'max_tokens':True},{'max_tokens':20000}):
            with self.subTest(options=options),self.assertRaises(ValueError):
                HTTPDialogueModel('https://example.invalid','test','secret',**options)
        model=HTTPDialogueModel('https://example.invalid','test','secret',timeout=95,max_tokens=10000)
        response={'choices':[{'finish_reason':'stop','message':{'content':'ok'}}]}
        with patch('endocrine.grounding.request_json',return_value=response) as call:
            self.assertEqual(model.complete('p',{},deadline=time.monotonic()+150),'ok')
            self.assertEqual(call.call_args.args[1]['max_tokens'],10000)
            self.assertEqual(call.call_args.kwargs['timeout'],95)

    def test_incomplete_and_timeout_have_distinct_sanitized_codes(self):
        import time
        model=HTTPDialogueModel('https://example.invalid','test','secret')
        for value,code in (({'choices':[{'finish_reason':'length','message':{'content':'private'}}]},'dialogue_provider_output_incomplete'),
                           (TimeoutError('secret'),'dialogue_provider_deadline')):
            with self.subTest(code=code),self.assertLogs('endocrine.dialogue',level='WARNING') as logs:
                with patch('endocrine.grounding.request_json',**({'side_effect':value} if isinstance(value,Exception) else {'return_value':value})):
                    with self.assertRaisesRegex(DomainError,code):model.complete('p',{},deadline=time.monotonic()+150)
                self.assertNotIn('secret',''.join(logs.output));self.assertNotIn('private',''.join(logs.output))
