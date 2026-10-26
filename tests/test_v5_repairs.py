import copy
import json
import tempfile
import unittest
from pathlib import Path
from endocrine.classifier import FixedClassifier
from endocrine.clock import FrozenClock, VirtualLeaseClock
from endocrine.core import initial_state, advance
from endocrine.model import Config, Event, Assessment, DomainError, iso, timestamp
from endocrine.service import Service
from endocrine.store import Repository
from tools.replay_events import replay


class V5Repairs(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.config=Config();self.now=timestamp('2026-10-17T10:00:00+08:00')
        self.clock=FrozenClock(self.now);self.lease=VirtualLeaseClock(self.now)
        self.repo=Repository(Path(self.tmp.name)/'test.db',self.config,lease_clock=self.lease)
        self.service=Service(self.repo,clock=self.clock)

    def event(self,n='1',**kw):
        return {'agent_id':'a','event_id':str(n),'occurred_at':iso(self.clock.now()),'text':'谢谢你',
            'actor_id':'p','conversation_id':'chat','target_id':'a',**kw}

    def assessed(self,event,kind='gratitude'):
        a=Assessment(kind=kind,target='agent',agency='actor',speech_act='direct',certainty=.95,
            relevance=.9,intensity=.8,evidence=(event['text'],))
        self.service.classifier=FixedClassifier(a)
        return self.service.submit(event)

    def state(self):
        return self.repo.load_state('a',self.clock.now())

    def assertReplay(self):
        data=self.repo.export();self.assertEqual(replay(data),data['states'])

    def test_lease_rollback_does_not_wait_two_hours_and_old_owner_fenced(self):
        self.repo.reserve(Event.parse(self.event()),self.now)
        first=self.repo.claim_next('a');self.assertIsNotNone(first)
        self.lease.rollback_wall(7200);self.lease.advance(121)
        replacement=self.repo.claim_next('a');self.assertIsNotNone(replacement)
        self.assertFalse(self.repo.save_assessment(first,Assessment()))
        self.assertTrue(self.repo.save_assessment(replacement,Assessment()))
        self.repo.finish(replacement,Assessment());self.assertEqual(self.state()['revision'],1)

    def test_long_provider_does_not_outlive_lease(self):
        self.repo.reserve(Event.parse(self.event()),self.now)
        self.assertIsNotNone(self.repo.claim_next('a'))
        self.lease.advance(29);self.assertIsNone(self.repo.claim_next('a'))
        self.lease.advance(32);self.assertIsNone(self.repo.claim_next('a'))
        self.lease.advance(60);self.assertIsNotNone(self.repo.claim_next('a'))

    def test_thirty_days_positive_evidence_does_not_saturate(self):
        for day in range(30):
            for n in range(5):
                self.assessed(self.event(f'{day}:{n}'))
            self.clock.advance(86400)
        r=self.state()['relationships']['p']
        self.assertLess(r['trust'],.9);self.assertGreater(r['trust'],.5)
        self.assertEqual(r['positive_evidence'],150)
        self.assertIn('trustworthiness',r)
        self.assertReplay()

    def test_same_day_burst_records_facts_with_bounded_weight(self):
        for n in range(30):self.assessed(self.event(n))
        r=self.state()['relationships']['p']
        self.assertGreater(r['alpha'],3)
        self.assertLess(r['alpha'],3+30*self.config.relation_evidence_weight)
        self.assertEqual(r['positive_evidence'],30)

    def test_old_first_delivered_message_is_not_fresh_hurt(self):
        old=self.assessed(self.event('old',text='我不想再理你',occurred_at=iso(self.now-7*86400),ingestion_mode='historical_replay'),'rejection')
        self.assertLess(abs(old['core_affect']['valence']),.001)
        self.assertEqual(old['last_appraisal']['late_by_seconds'],7*86400)
        self.assertLess(old['relationships']['p']['trust'],.5)

    def test_failed_classification_repaired_without_second_contact(self):
        first=self.service.submit(self.event())
        self.assertTrue(first['classification_pending'])
        self.service.classifier=FixedClassifier(Assessment(kind='gratitude',target='agent',agency='actor',
            speech_act='direct',certainty=.9,relevance=.8,intensity=.6,evidence=('谢谢你',)))
        self.assertEqual(self.service.retry_classifications(force=True),1)
        state=self.state();self.assertEqual(state['generation'],1)
        self.assertEqual(state['events_processed'],1)
        self.assertEqual(state['relationships']['p']['contact_count'],1)
        self.assertGreater(state['relationships']['p']['trust'],.5)
        self.assertEqual(self.service.retry_classifications(force=True),0)
        self.assertEqual(len(self.repo.export()['assessment_history']),2)
        self.assertEqual(len(self.repo.export()['state_history']),2)
        self.assertReplay()

    def test_repair_does_not_make_yesterdays_emotion_fresh(self):
        self.service.submit(self.event())
        self.clock.advance(86400)
        self.service.submit(self.event('later',text='桌上有书'))
        self.service.classifier=FixedClassifier(Assessment(kind='gratitude',target='agent',agency='actor',
            speech_act='direct',certainty=.9,relevance=.8,intensity=.6,evidence=('谢谢你',)))
        self.service.retry_classifications(limit=1,force=True)
        self.assertGreater(self.service.snapshot('a')['core_affect']['valence'],.01)
        self.assertEqual(self.state()['generation'],1)
        self.assertEqual(self.state()['events_processed'],2)
        self.assertReplay()

    def test_retry_backoff_and_clock_rollback(self):
        self.service.submit(self.event())
        job=self.repo.claim_classification();self.assertIsNone(job)
        self.lease.advance(31);job=self.repo.claim_classification();self.assertIsNotNone(job)
        self.assertFalse(self.repo.complete_classification(job,Assessment(classifier_status='provider_output_truncated'),[]))
        self.assertIsNone(self.repo.claim_classification())
        self.lease.rollback_wall(7200)
        self.assertIsNotNone(self.repo.claim_classification())

    def test_context_is_same_conversation_previous_only(self):
        self.service.submit(self.event('a'))
        self.service.submit(self.event('b',text='其他对话',conversation_id='else'))
        row,_=self.repo.reserve(Event.parse(self.event('c')),self.now)
        ctx=self.repo.classification_context(row)
        self.assertEqual([x['event_id'] for x in ctx],['a'])
        future=self.event('d',text='未来消息');self.service.submit(future,wait_seconds=0)
        self.assertEqual(self.repo.classification_context(row),ctx)

    def test_provider_context_is_passed_but_evidence_must_be_current(self):
        self.service.submit(self.event('a'))
        contexts=[]
        class Classifier:
            def classify_with_context(inner,event,context):
                contexts.append(context)
                return Assessment(kind='gratitude',evidence=('谢谢你',))
        self.service.classifier=Classifier()
        result=self.service.submit(self.event('b',text='好的'))
        self.assertEqual(contexts[0][0]['event_id'],'a')
        self.assertEqual(result['classify_source'],'invalid_classifier_output')

    def test_person_filter_before_limit_and_cursor(self):
        self.service.submit(self.event('old-person',actor_id='rare'))
        for n in range(205):self.service.submit(self.event(f'other-{n}'))
        self.assertEqual(self.repo.recent_memories('a','rare')[0]['event_id'],'old-person')
        first=self.repo.search_memories('a',limit=100)
        second=self.repo.search_memories('a',limit=100,cursor=first['next_cursor'])
        third=self.repo.search_memories('a',limit=100,cursor=second['next_cursor'])
        ids=[m['event_id'] for page in (first,second,third) for m in page['items']]
        self.assertEqual(len(ids),206);self.assertEqual(len(set(ids)),206)
        self.assertIsNone(third['next_cursor'])
        with self.assertRaises(DomainError):self.service.memories('a',query='完整语义查询')

    def test_legacy_memory_search_and_recall_does_not_add_trust(self):
        graph={'format':'legacy-inert-graph-v1','objects':{'1':{'type':'databrain.memory.Event',
            'fields':{'text':'旧日回忆','subject':'someone','id':'old','salience':.8}}}}
        state=initial_state('a',self.now,self.config)
        state['legacy']['person_mapping']={'someone':'p'}
        self.repo.import_state(state,graph)
        m=self.repo.recent_memories('a','p')[0];self.assertEqual(m['text'],'旧日回忆')
        self.assertIsNone(m['occurred_at'])
        event=self.event('recall',text='旧日回忆',source='self_reflection',provenance='self_reflection',
            event_type='recall',causation_id=m['event_id'])
        response=self.assessed(event)
        self.assertEqual(response['relationships'],{})

    def test_legacy_duplicate_ids_and_empty_text_are_preserved(self):
        graph={'format':'legacy-inert-graph-v1','objects':{str(n):{'type':'databrain.memory.Event',
            'fields':{'id':'repeated','text':'' if n==2 else '记录'}} for n in range(3)}}
        self.repo.import_state(initial_state('a',self.now,self.config),graph)
        rows=self.repo.search_memories('a')['items']
        self.assertEqual(len(rows),3)
        self.assertEqual(len({r['event_id'] for r in rows}),3)

    def test_sleep_and_wake_fatigue_and_piecewise_projection(self):
        s=initial_state('a',self.now,self.config)
        s['physiology']['mode']='wake'
        awake=advance(s,self.now+24*3600,self.config)
        self.assertGreater(awake['physiology']['fatigue'],s['physiology']['fatigue'])
        awake['physiology']['mode']='sleep'
        rested=advance(awake,self.now+32*3600,self.config)
        self.assertLess(rested['physiology']['fatigue'],.2)
        s['physiology']['mode']='automatic'
        once=advance(s,self.now+48*3600,self.config)
        parts=s
        for n in range(48):parts=advance(parts,self.now+(n+1)*3600,self.config)
        for key in ('fatigue','sleep_debt_h','total_sleep_h'):
            self.assertAlmostEqual(once['physiology'][key],parts['physiology'][key],places=10)

    def test_personality_is_explicit_persistent_and_replayed(self):
        event=self.event('profile',source='system',provenance='operator_assertion',event_type='profile_update',
            text='',payload={'sensitivity':.8,'resilience':.7})
        result=self.service.submit(event)
        self.assertEqual(result['personality']['sensitivity'],.8)
        self.assertEqual(result['classify_source'],'system_command')
        with self.assertRaises(DomainError):
            self.service.submit({**event,'event_id':'bad','source':'user_message','provenance':'observed_message'})
        self.assertReplay()

    def test_mixed_emotions_preserve_separate_causes(self):
        self.assessed(self.event('warm'))
        response=self.assessed(self.event('hurt',text='我不想再理你'),'rejection')
        tracks={x['label']:x for x in response['emotions_detail']}
        self.assertIn('warmth',tracks);self.assertIn('hurt',tracks)
        self.assertEqual(tracks['warmth']['causes'],['warm:legacy-candidate'])
        self.assertEqual(tracks['hurt']['causes'],['hurt:legacy-candidate'])
        self.assertReplay()

    def test_snapshot_projection_does_not_write_physiology(self):
        self.assessed(self.event())
        before=copy.deepcopy(self.repo.export())
        self.service.snapshot('a',self.now+7*86400)
        self.assertEqual(self.repo.export(),before)

    def test_time_sync_same_raw_retry_deduplicates_and_raw_time_change_conflicts(self):
        event=self.event(occurred_at=iso(self.now+7200),clock_sync={
            'client_at_sync':iso(self.now+7200),'server_at_sync':iso(self.now)})
        self.service.submit(event)
        self.clock.advance(30)
        self.assertTrue(self.service.submit(event)['deduplicated'])
        with self.assertRaises(DomainError):self.service.submit({**event,'occurred_at':iso(self.now+7201)})
