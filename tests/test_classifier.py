import io
import json
import unittest
from unittest.mock import patch

from endocrine.classifier import HTTPClassifier
from endocrine.model import Event, iso


class ClassifierContractTests(unittest.TestCase):
    def event(self):
        return Event.parse({"agent_id":"a","event_id":"m","occurred_at":iso(1792141200),"text":"我考试没过，很难过","actor_id":"p"})

    def test_valid_evidence_contract(self):
        text=self.event().text
        extracted={"kind":"loss","target":"actor","agency":"external","speech_act":"direct",
                   "certainty":.9,"relevance":.8,"intensity":.6,"evidence":[text]}
        raw=json.dumps({"choices":[{"message":{"content":json.dumps({"facts":[{"fact_id":"loss","goal_effects":{"connection":-.35},**extracted}]},ensure_ascii=False)}}]}).encode()
        with patch('endocrine.classifier.request_json',return_value=json.loads(raw)) as request:
            a=HTTPClassifier('https://provider.invalid/chat/completions','configured-model','test-key').classify(self.event())
            self.assertEqual(a.kind,'loss')
            self.assertEqual(a.target,'actor')
            self.assertEqual(a.classifier_status,'semantic')
            submitted=request.call_args.args[1]
            self.assertEqual(len(submitted['messages']),2)
            self.assertEqual(request.call_count,2)
            self.assertIn('semantic_audit',json.loads(submitted['messages'][1]['content']))
            payload=json.loads(submitted['messages'][1]['content'])
            self.assertEqual(payload['current_actor_id'],'p');self.assertEqual(payload['agent_id'],'a')
            self.assertEqual(payload['occurred_at'],self.event().occurred_at)

    def test_provider_timeout_explicit_unknown(self):
        with patch('endocrine.classifier.request_json',side_effect=TimeoutError):
            a=HTTPClassifier('https://provider.invalid','configured-model','test-key').classify(self.event())
            self.assertEqual(a.kind,'unknown')
            self.assertEqual(a.classifier_status,'provider_unavailable')

    def test_invalid_provider_structure_rejected(self):
        with patch('endocrine.classifier.request_json',return_value={'choices':[]}):
            # Empty arrays must be handled as a provider failure, not crash the queue.
            a=HTTPClassifier('https://provider.invalid','configured-model','test-key').classify(self.event())
            self.assertEqual(a.classifier_status,'invalid_provider_output')

    def test_missing_key_no_network_call(self):
        with patch('endocrine.classifier.request_json') as request:
            a=HTTPClassifier('https://provider.invalid','configured-model','').classify(self.event())
            request.assert_not_called()
            self.assertEqual(a.classifier_status,'missing_key')

    def test_top_level_evidence_repaired_by_model_under_same_total_budget(self):
        clock=[0.0];calls=[]
        def request(*args,**kwargs):
            calls.append((args,kwargs))
            if len(calls)==1:
                clock[0]+=25
                return {'choices':[{'message':{'content':'{"facts":[],"trust_evidence":[]}'}}]}
            return {'model':'actual-cloud-model','choices':[{'message':{'content':'{"facts":[]}'}}]}
        with patch('endocrine.classifier.request_json',side_effect=request),patch('endocrine.classifier.time.monotonic',side_effect=lambda:clock[0]):
            a=HTTPClassifier('https://provider.invalid','m','key',timeout=30).classify(self.event())
        self.assertEqual(a.classifier_status,'semantic');self.assertIn('format-repair-1',a.model_version)
        self.assertEqual(len(calls),2);self.assertEqual(calls[1][1]['timeout'],5)
        repaired=json.loads(calls[1][0][1]['messages'][1]['content'])
        self.assertEqual(repaired['text'],self.event().text)
        self.assertEqual(repaired['format_repair']['validation_error'],'unexpected_top_level_fields')

    def test_draft_is_not_committed_without_semantic_audit(self):
        with patch('endocrine.classifier.request_json',side_effect=[{'choices':[{'message':{'content':'{"facts":[]}'}}]},TimeoutError]):
            a=HTTPClassifier('https://provider.invalid','m','key').classify(self.event())
        self.assertEqual(a.classifier_status,'provider_unavailable');self.assertFalse(a.facts)

    def test_audit_can_restore_a_fact_missing_from_draft(self):
        text=self.event().text
        final={'facts':[{'fact_id':'recovered','evidence':[text],'target':'actor','agency':'external',
            'speech_act':'direct','goal_effects':{'connection':-.4},'certainty':.9,'relevance':.9,'intensity':.5}]}
        responses=[{'choices':[{'message':{'content':'{"facts":[]}'}}]},
            {'choices':[{'message':{'content':json.dumps(final,ensure_ascii=False)}}]}]
        with patch('endocrine.classifier.request_json',side_effect=responses):
            a=HTTPClassifier('https://provider.invalid','m','key').classify(self.event())
        self.assertEqual(len(a.facts),1);self.assertIn('semantic-audit-1',a.model_version)
