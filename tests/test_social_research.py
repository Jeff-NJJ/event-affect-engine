import json
import tempfile
import unittest
from pathlib import Path
from endocrine.classifier import FixedClassifier
from endocrine.clock import FrozenClock,VirtualLeaseClock
from endocrine.core import initial_state,apply
from endocrine.facts import Fact
from endocrine.model import Config,Event,Assessment,DomainError,iso
from endocrine.service import Service
from endocrine.store import Repository
from endocrine.social import update_evidence
from tools.replay_events import replay


class ResearchRegression(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.config=Config();self.now=1792288800.0
        self.clock=FrozenClock(self.now);self.lease=VirtualLeaseClock(self.now)
        self.repo=Repository(Path(self.tmp.name)/'r.db',self.config,lease_clock=self.lease)
        self.svc=Service(self.repo,clock=self.clock)

    def event(self,n='e',**kw):
        return {'agent_id':'a','actor_id':'p','event_id':n,'occurred_at':iso(self.clock.now()),
            'conversation_id':'c','text':'谢谢你陪我，但是你把秘密说出去了。',**kw}

    def fact(self,text,**kw):
        if 'trust_evidence' in kw:kw['trust_evidence']=[{'behavior_status':'completed_report',**item} for item in kw['trust_evidence']]
        return Fact.parse({'fact_id':'f','evidence':[text],'goal_effects':{'connection':.7},
            'kind':'support','target':'agent','agency':'actor','speech_act':'direct',
            'certainty':.95,'relevance':.9,'intensity':.9,**kw},text)

    def run_fact(self,raw,*facts):
        self.svc.classifier=FixedClassifier(Assessment(facts=tuple(facts),classifier_status='semantic'))
        return self.svc.submit(raw)

    def state(self):return self.repo.load_state('a',self.clock.now())
    def assertReplay(self):
        data=self.repo.export();self.assertEqual(replay(data),data['states'])

    def test_mixed_paragraph_preserves_both_appraisals(self):
        raw=self.event();text=raw['text']
        a=self.fact(text,fact_id='care',evidence=['谢谢你陪我'])
        b=self.fact(text,fact_id='breach',evidence=['你把秘密说出去了'],kind='criticism',
            goal_effects={'respect':-.9},interaction='boundary_violation')
        result=self.run_fact(raw,a,b)
        labels={v['label'] for v in self.state()['emotion_tracks'].values()}
        self.assertTrue({'warmth','anger'}<=labels)
        self.assertEqual(len(result['last_appraisal']['facts']),2)
        self.assertLessEqual(result['last_appraisal']['gain'],self.config.maximum_event_gain)
        self.assertReplay()

    def test_loss_can_relieve_autonomy(self):
        raw=self.event(text='终于离开那份压榨人的工作，我松了一口气。')
        result=self.run_fact(raw,self.fact(raw['text'],kind='loss',goal_effects={'autonomy':.8}))
        self.assertGreater(result['core_affect']['valence'],0)
        self.assertEqual(result['last_appraisal']['label'],'relief')

    def test_others_autonomy_is_not_my_own_autonomy(self):
        raw=self.event(text='我离职后自由多了。')
        result=self.run_fact(raw,self.fact(raw['text'],kind='loss',target='actor',goal_effects={'autonomy':.8}))
        self.assertEqual(result['core_affect']['valence'],0)
        self.assertEqual(result['last_appraisal']['facts'][0]['goal_effects'],{})
        self.assertEqual(result['last_appraisal']['facts'][0]['proposed_goal_effects'],{'autonomy':.8})

    def test_ability_and_integrity_do_not_collapse(self):
        raw=self.event();fact=self.fact(raw['text'],trust_evidence=[{'dimension':'ability','scope':'technical_work','direction':'violates','strength':.8}])
        self.run_fact(raw,fact);r=self.state()['relationships']['p']
        self.assertIn('technical_work',r['trustworthiness']['ability'])
        self.assertEqual(r['trustworthiness']['integrity'],{})
        self.assertEqual(r['closeness'],.5)

    def test_apology_does_not_erase_breach_or_self_declare_repair(self):
        raw=self.event();evidence=[{'dimension':'integrity','scope':'confidentiality','direction':'violates','strength':.9}]
        self.run_fact(raw,self.fact(raw['text'],trust_evidence=evidence))
        before=self.state()['relationships']['p']['trust']
        raw=self.event('sorry',text='对不起，下次一定不会。')
        self.run_fact(raw,self.fact(raw['text'],interaction='apology',trust_evidence=[{**evidence[0],'direction':'supports'}]))
        r=self.state()['relationships']['p'];self.assertEqual(r['trust'],before)
        self.assertEqual(len(r['open_violations']),1)

    def test_contact_count_does_not_manufacture_closeness(self):
        for i in range(30):self.svc.submit(self.event(str(i)))
        r=self.state()['relationships']['p']
        self.assertEqual(r['contact_count'],30);self.assertEqual(r['closeness'],.5)
        self.assertEqual(r['trust'],.5);self.assertGreater(r['familiarity'],0)

    def test_later_serious_violation_has_nonzero_weight(self):
        for i in range(25):
            raw=self.event(str(i));self.run_fact(raw,self.fact(raw['text'],trust_evidence=[{'dimension':'integrity','scope':'commitments','direction':'supports','strength':1}]))
        before=self.state()['relationships']['p']['trust']
        raw=self.event('harm');result=self.run_fact(raw,self.fact(raw['text'],goal_effects={'respect':-1},trust_evidence=[{'dimension':'integrity','scope':'commitments','direction':'violates','strength':1}]))
        self.assertLess(self.state()['relationships']['p']['trust'],before)
        self.assertGreater(result['last_appraisal']['effective_relation_weight'],.1)

    def test_live_old_world_fact_and_archive_are_different(self):
        raw=self.event(occurred_at=iso(self.now-7*86400));fact=self.fact(raw['text'],goal_effects={'respect':-.8})
        result=self.run_fact(raw,fact);self.assertEqual(result['last_appraisal']['affect_age_hours'],0)
        self.assertLess(result['core_affect']['valence'],-.01)

    def test_rollback_does_not_age_fresh_message_two_days(self):
        self.svc.submit(self.event());self.clock.advance(-2*86400);self.lease.advance(5)
        raw=self.event('fresh');result=self.run_fact(raw,self.fact(raw['text'],goal_effects={'respect':-.8}))
        self.assertEqual(result['last_appraisal']['affect_age_hours'],0)
        self.assertEqual(result['last_appraisal']['late_by_seconds'],0)
        self.assertReplay()

    def test_recall_cannot_replace_original_text(self):
        self.svc.submit(self.event())
        raw=self.event('r',source='self_reflection',provenance='self_reflection',event_type='recall',causation_id='e',text='虚构背叛')
        with self.assertRaisesRegex(DomainError,'recall_content_mismatch'):self.svc.submit(raw)

    def test_assistant_context_complete_without_self_credit(self):
        raw=self.event('assistant',source='agent_message',actor_id='a',text='这是完整上下文。'*160+'最后我说：我很害怕。')
        self.svc.submit(raw)
        self.assertEqual(self.state()['events_processed'],0)
        self.assertEqual(self.state()['relationships'],{})
        row,_=self.repo.reserve(Event.parse(self.event('next')),self.now)
        ctx=self.repo.classification_context(row)
        self.assertEqual(ctx[0]['role'],'assistant');self.assertEqual(ctx[0]['text'],raw['text'])

    def test_responsiveness_requires_bound_disclosure(self):
        self.svc.submit(self.event('a1',source='agent_message',actor_id='a',text='我今天很害怕失败。'))
        raw=self.event('p1',text='你怕失败，我听懂了；这不代表你没价值，我陪你练习。')
        f=self.fact(raw['text'],interaction='responsive_reply',related_event_id='a1',
            context_evidence=[{'event_id':'a1','evidence':'我今天很害怕失败','meaning':'disclosure'}],
            responsiveness={'understanding':1,'validation':1,'care':1})
        self.run_fact(raw,f);self.assertGreater(self.state()['relationships']['p']['closeness'],.5)
        self.assertReplay()

    def test_invented_context_is_unknown_not_lost_event(self):
        raw=self.event();result=self.run_fact(raw,self.fact(raw['text'],related_event_id='missing'))
        self.assertTrue(result['classification_pending']);self.assertEqual(self.state()['events_processed'],1)
        self.assertEqual(self.state()['fast_valence'],0)

    def test_unbound_repair_is_not_positive_trust_evidence(self):
        raw=self.event(text='我不是在怪你。')
        self.run_fact(raw,self.fact(raw['text'],interaction='repair_confirmed',trust_evidence=[
            {'dimension':'integrity','scope':'commitments','direction':'supports','strength':1}]))
        self.assertEqual(self.state()['relationships']['p']['trust'],.5)

    def test_semantic_repairs_append_linearly_and_leave_original_immutable(self):
        for i in range(15):self.svc.submit(self.event(str(i)))
        original=self.repo.get_event('a','0')['assessment']
        class Reader:
            def classify(inner,event):return Assessment(facts=(self.fact(event.text),),classifier_status='semantic')
        self.svc.classifier=Reader();self.assertEqual(self.svc.retry_classifications(limit=15,force=True),15)
        data=self.repo.export()
        self.assertEqual(len(data['state_history']),30);self.assertEqual(len(data['events']),30)
        self.assertEqual(self.repo.get_event('a','0')['assessment'],original)
        self.assertEqual(self.state()['events_processed'],15)
        self.assertEqual(self.state()['relationships']['p']['contact_count'],15)
        self.assertEqual(self.state()['generation'],15);self.assertReplay()

    def test_fatigue_changes_appraisal_not_only_display(self):
        event=Event.parse(self.event());a=Assessment(facts=(self.fact(event.text,goal_effects={'respect':-.8}),),classifier_status='semantic')
        gains=[]
        for fatigue in (.1,.9):
            s=initial_state('a',self.now,self.config);s['physiology']['fatigue']=fatigue
            _,trace=apply(s,event,a,self.now,1,self.config);gains.append(trace['appraisal']['gain'])
        self.assertGreater(gains[1],gains[0])

    def test_out_of_order_learning_day_preserves_newer_counters(self):
        from endocrine.core import _relationship
        state=initial_state('a',self.now,self.config);r=_relationship(state,'p',self.config)
        update_evidence(r,'integrity','general',1,1,'2026-10-18',self.config)
        update_evidence(r,'integrity','general',1,1,'2026-10-17',self.config)
        self.assertIn('2026-10-18:integrity:general',r['learning_mass'])

    def test_undefined_semantics_never_falls_back_to_keywords(self):
        self.svc.submit(self.event())
        with self.assertRaisesRegex(DomainError,'semantic_cloud_not_configured'):
            self.svc.memories('a',query='谢谢')

    def test_same_quote_cannot_multiply_trust_by_duplicate_facts(self):
        raw=self.event();ev={'dimension':'benevolence','scope':'interaction','direction':'supports','strength':.2}
        facts=[self.fact(raw['text'],fact_id=str(n),trust_evidence=[{**ev,'strength':strength}]) for n,strength in enumerate((.2,1,.5))]
        self.run_fact(raw,*facts);r=self.state()['relationships']['p']
        self.assertEqual(r['positive_evidence'],1)
        self.assertAlmostEqual(r['alpha']-self.config.relation_prior_mass,.95*self.config.relation_evidence_weight)
        self.assertReplay()

    def test_distinct_evidence_spans_can_remain_independent(self):
        raw=self.event();ev={'dimension':'benevolence','scope':'interaction','direction':'supports','strength':.5}
        a=self.fact(raw['text'],fact_id='one',evidence=['谢谢你陪我'],trust_evidence=[ev])
        b=self.fact(raw['text'],fact_id='two',evidence=['你把秘密说出去了'],trust_evidence=[{**ev,'direction':'violates'}])
        self.run_fact(raw,a,b);r=self.state()['relationships']['p']
        self.assertEqual(r['positive_evidence'],1);self.assertEqual(r['negative_evidence'],1)

    def test_zero_strength_is_not_new_relationship_evidence(self):
        raw=self.event();self.run_fact(raw,self.fact(raw['text'],trust_evidence=[
            {'dimension':'integrity','scope':'general','direction':'supports','strength':0}]))
        self.assertEqual(self.state()['relationships']['p']['evidence_count'],0)

    def test_request_or_intent_does_not_imply_fulfilled_trust(self):
        for n,status in enumerate(('intent','request','interpretation','unknown')):
            raw=self.event(str(n));self.run_fact(raw,self.fact(raw['text'],trust_evidence=[
                {'dimension':'integrity','scope':'commitments','direction':'supports','strength':1,'behavior_status':status}]))
        self.assertEqual(self.state()['relationships']['p']['evidence_count'],0)

    def test_boundary_is_remembered_without_rewarding_compliance(self):
        raw=self.event(text='我的病情只同意告诉医生，请不要告诉同事。')
        self.run_fact(raw,self.fact(raw['text'],interaction='boundary_request',trust_evidence=[
            {'dimension':'integrity','scope':'confidentiality','direction':'supports','strength':1,'behavior_status':'completed_report'}]))
        r=self.state()['relationships']['p'];self.assertEqual(r['trust'],.5)
        self.assertEqual(r['expressed_boundaries'][0]['evidence'],[raw['text']])
        self.assertReplay()

    def test_observed_real_world_help_can_inform_relationship(self):
        raw=self.event(text='对方冒雨赶来，把我安全送到了家。',source='external_event',provenance='observed_event',event_type='external_experience')
        self.run_fact(raw,self.fact(raw['text'],speech_act='technical',trust_evidence=[
            {'dimension':'benevolence','scope':'care','direction':'supports','strength':.8,'behavior_status':'observed'}]))
        r=self.state()['relationships']['p']
        self.assertGreater(r['trust'],.5);self.assertEqual(r['contact_count'],0)
        self.assertEqual(r['observed_interaction_count'],1)
        self.assertReplay()

    def test_anonymous_world_event_does_not_invent_an_interpersonal_actor(self):
        raw=self.event(text='雨停了，远处的公园开放了。',actor_id=None,source='external_event',provenance='observed_event',event_type='external_experience')
        self.run_fact(raw,self.fact(raw['text'],trust_evidence=[
            {'dimension':'benevolence','scope':'care','direction':'supports','strength':.8,'behavior_status':'observed'}]))
        self.assertEqual(self.state()['relationships'],{})

    def test_delayed_world_interpretation_is_one_observed_interaction(self):
        raw=self.event(text='对方送我回了家。',source='external_event',provenance='observed_event',event_type='external_experience')
        self.svc.submit(raw)
        self.svc.classifier=FixedClassifier(Assessment(facts=(self.fact(raw['text'],trust_evidence=[
            {'dimension':'benevolence','scope':'care','direction':'supports','strength':.8,'behavior_status':'observed'}]),),classifier_status='semantic'))
        self.assertEqual(self.svc.retry_classifications(force=True),1)
        r=self.state()['relationships']['p'];self.assertEqual(r['observed_interaction_count'],1)
        self.assertEqual(r['contact_count'],0);self.assertEqual(self.state()['events_processed'],1)
        self.assertReplay()

    def test_quoted_world_narrative_is_not_an_observed_interpersonal_act(self):
        raw=self.event(text='书中的角色说他把我送回了家。',source='external_event',provenance='observed_event',event_type='external_experience')
        self.run_fact(raw,self.fact(raw['text'],speech_act='quote',trust_evidence=[
            {'dimension':'benevolence','scope':'care','direction':'supports','strength':1,'behavior_status':'observed'}]))
        self.assertEqual(self.state()['relationships'],{})


if __name__=='__main__':unittest.main()
