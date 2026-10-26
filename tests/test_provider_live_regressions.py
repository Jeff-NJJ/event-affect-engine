import io
import json
import unittest
from unittest.mock import patch
from endocrine.classifier import HTTPClassifier, parse_model_json
from endocrine.model import Event, DomainError, iso, timestamp
from endocrine.time_sync import calibrated_event


class ProviderLiveRegressions(unittest.TestCase):
    def test_fenced_json_and_bom(self):
        for text in ('```json\n{"kind":"unknown"}\n```','```JSON\r\n{"kind":"unknown"}\r\n```','\ufeff  {"kind":"unknown"}  '):
            self.assertEqual(parse_model_json(text),{"kind":"unknown"})

    def test_surrounding_prose_arrays_and_malformed_fences_rejected(self):
        for text in ('explanation\n```json\n{}\n```','```json\n{}','[]','{} trailing',None):
            with self.assertRaises((ValueError,TypeError)):
                parse_model_json(text)

    def test_thinking_budget_and_timeout(self):
        raw={"choices":[{"finish_reason":"stop","message":{"content":"```json\n{\"facts\":[]}\n```","reasoning_content":"思考"*4500}}]}
        e=Event("a","e",iso(1792141200),text="测试")
        for supplied in (1,6,30,60,300):
            with patch('endocrine.classifier.request_json',return_value=raw) as request:
                a=HTTPClassifier('https://provider.invalid','glm-5.3-flash','test',supplied).classify(e)
                self.assertEqual(a.classifier_status,'semantic')
                self.assertEqual(request.call_args.args[1]['max_tokens'],4096)
                self.assertGreater(request.call_args.kwargs['timeout'],0)
                self.assertLessEqual(request.call_args.kwargs['timeout'],60)

    def test_empty_and_truncated_are_observable(self):
        e=Event("a","e",iso(1792141200))
        for finish,content,expected in [('length','', 'provider_output_truncated'),('length','{}','provider_output_truncated'),('stop','  ','empty_provider_output')]:
            raw={"choices":[{"finish_reason":finish,"message":{"content":content}}]}
            with patch('endocrine.classifier.request_json',return_value=raw):
                a=HTTPClassifier('https://provider.invalid','glm','test').classify(e)
                self.assertEqual(a.classifier_status,expected)
                self.assertEqual(a.kind,'unknown')

    def test_explicit_clock_correction_retains_raw_and_is_stable(self):
        now=1792141200
        raw={"agent_id":"a","event_id":"e","occurred_at":iso(now+7200),"payload":{},
             "clock_sync":{"client_at_sync":iso(now+7200),"server_at_sync":iso(now)}}
        event=calibrated_event(raw,now)
        self.assertEqual(timestamp(event['occurred_at']),now)
        self.assertEqual(event['payload']['clock_audit']['original_occurred_at'],raw['occurred_at'])
        self.assertEqual(calibrated_event(raw,now+120),event)

    def test_invalid_clock_calibration_does_not_erase_time(self):
        with self.assertRaises(DomainError):
            calibrated_event({'clock_sync':{'client_at_sync':'bad','server_at_sync':'bad'}},0)

    def test_observation_and_occurrence_share_explicit_client_clock_correction(self):
        now=1792141200
        raw={'occurred_at':iso(now+7200),'observed_at':iso(now+7210),
            'clock_sync':{'client_at_sync':iso(now+7200),'server_at_sync':iso(now)}}
        fixed=calibrated_event(raw,now)
        self.assertEqual(timestamp(fixed['observed_at']),now+10)
        self.assertEqual(fixed['payload']['clock_audit']['original_observed_at'],raw['observed_at'])
        self.assertEqual(fixed,calibrated_event(raw,now+120))
