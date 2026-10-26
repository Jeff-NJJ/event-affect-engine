import json,tempfile,unittest
from pathlib import Path
from endocrine.clock import FrozenClock,VirtualLeaseClock
from endocrine.model import Config,Assessment,iso,DomainError
from endocrine.facts import Fact
from endocrine.service import Service
from endocrine.store import Repository
from endocrine.semantic import SemanticMemory
from tools.replay_events import replay

class LongContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.repo=Repository(Path(self.tmp.name)/'c.db',Config(),lease_clock=VirtualLeaseClock(1792288800))
        self.svc=Service(self.repo,clock=FrozenClock(1792288800));self.calls=[]
        self.svc.submit(self.raw('old',source='agent_message',actor_id='a',text='我害怕在很多人面前演讲。'))
        for i in range(10):self.svc.submit(self.raw('recent'+str(i),text='普通日常叙述'))
        self.svc.submit(self.raw('elsewhere',conversation_id='else',text='其他对话里的无关披露'))
        outer=self
        class FixtureRanker:
            def rank(inner,query,items,limit,*,timeout=None):
                outer.calls.append((query,items,timeout))
                return [m for m in items if m['event_id']=='old'] # contract fixture, not semantic implementation
        self.svc.semantic=SemanticMemory(self.repo,FixtureRanker())

    def raw(self,eid,**kw):return {'agent_id':'a','actor_id':'p','event_id':eid,'occurred_at':iso(self.svc.clock.now()),'conversation_id':'c','text':'我记得你曾担心公开发言，我懂那种紧张，可以陪你练习。',**kw}
    def provider(self):
        outer=self
        class Provider:
            def classify_with_context(inner,event,context):
                outer.seen_context=context
                f=Fact.parse({'fact_id':'reply','evidence':[event.text],'goal_effects':{'connection':.5},
                    'target':'agent','agency':'actor','speech_act':'direct','certainty':.95,'relevance':.9,'intensity':.5,
                    'interaction':'responsive_reply','related_event_id':'old',
                    'context_evidence':[{'event_id':'old','evidence':'我害怕在很多人面前演讲','meaning':'disclosure'}],
                    'responsiveness':{'understanding':1,'validation':1,'care':1}},event.text)
                return Assessment(facts=(f,),classifier_status='semantic')
        return Provider()

    def test_old_disclosure_retrieved_and_bound_before_classification(self):
        self.svc.classifier=self.provider();result=self.svc.submit(self.raw('current'))
        self.assertEqual(result['classify_source'],'semantic')
        ids=[c['event_id'] for c in self.seen_context]
        self.assertIn('old',ids);self.assertLessEqual(len(ids),12)
        self.assertNotIn('elsewhere',ids)
        self.assertGreater(self.repo.load_state('a',self.svc.clock.now())['relationships']['p']['closeness'],.5)
        self.assertLessEqual(self.calls[0][2],20)

    def test_retry_resolution_keeps_long_context_references(self):
        self.svc.submit(self.raw('current'))
        with self.repo.transaction() as c:c.execute("DELETE FROM classification_jobs WHERE event_id!='current'")
        self.svc.classifier=self.provider();self.assertEqual(self.svc.retry_classifications(force=True),1)
        data=self.repo.export();resolution=next(r for r in data['events'] if json.loads(r['body'])['event_type']=='semantic_resolution')
        self.assertIn('old',json.loads(resolution['body'])['payload']['context_event_ids'])
        self.assertEqual(json.loads(resolution['assessment'])['classifier_status'],'semantic')
        self.assertEqual(replay(data),data['states'])

    def test_future_and_recent_messages_not_sent_as_old_candidates(self):
        self.svc.submit(self.raw('target'),wait_seconds=0)
        self.svc.submit(self.raw('future'),wait_seconds=0)
        self.svc.classifier=self.provider();self.svc.recover_pending(limit=1)
        candidates={m['event_id'] for m in self.calls[0][1]}
        self.assertNotIn('target',candidates);self.assertNotIn('future',candidates)
        self.assertNotIn('recent9',candidates);self.assertNotIn('elsewhere',candidates)

    def test_context_lookup_failure_is_pending_not_false_complete(self):
        class Broken:
            def rank(self,*args,**kwargs):raise TimeoutError()
        self.svc.semantic=SemanticMemory(self.repo,Broken());self.svc.classifier=self.provider()
        result=self.svc.submit(self.raw('current'))
        self.assertTrue(result['classification_pending'])
        self.assertEqual(result['classify_source'],'context_retrieval_unavailable')

    def test_crash_after_assessment_preserves_retrieved_context(self):
        self.svc.classifier=self.provider()
        def crash(connection):raise RuntimeError('simulated_crash_before_commit')
        self.svc.before_commit=crash
        with self.assertRaisesRegex(RuntimeError,'simulated_crash'):self.svc.submit(self.raw('current'))
        self.svc.before_commit=None;self.repo.lease_clock.advance(121)
        self.svc.recover_pending(limit=1)
        row=self.repo.get_event('a','current')
        self.assertEqual(json.loads(row['assessment'])['classifier_status'],'semantic')
        self.assertGreater(self.repo.load_state('a',self.svc.clock.now())['relationships']['p']['closeness'],.5)
        self.assertEqual(replay(self.repo.export()),self.repo.export()['states'])

if __name__=='__main__':unittest.main()
