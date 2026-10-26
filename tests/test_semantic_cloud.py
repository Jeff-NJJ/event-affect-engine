import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from endocrine.clock import FrozenClock
from endocrine.model import Config,DomainError,iso
from endocrine.service import Service
from endocrine.store import Repository
from endocrine.semantic import SemanticMemory,HTTPParagraphReranker

class CloudContracts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.repo=Repository(Path(self.tmp.name)/'s.db',Config())
        self.svc=Service(self.repo,clock=FrozenClock(1792288800))
        for agent,actor,eid,text in [('a','p','yes','那次他把秘密传给同事了。'),('a','p','no','他没有泄露秘密。'),
            ('a','other','excluded','别人说了秘密'),('b','p','different_agent','秘密')]:
            self.svc.submit({'agent_id':agent,'actor_id':actor,'event_id':eid,'occurred_at':iso(self.svc.clock.now()),'text':text})
        self.ranker=HTTPParagraphReranker('https://example.invalid/chat/completions','m','key')
        self.svc.semantic=SemanticMemory(self.repo,self.ranker)

    def response(self,rows,**choice):
        return {'choices':[{'message':{'content':'```json\n'+json.dumps({'ranking':rows},ensure_ascii=False)+'\n```'},**choice}]}

    def test_full_paragraphs_scope_and_readonly(self):
        before=self.repo.export();query='我在找真实发生过的隐私越界，而不是某人仅否认。'
        row={'event_id':'yes','relevance':.99,'evidence':'把秘密传给同事','relation':'supports'}
        with patch('endocrine.semantic.request_json',return_value=self.response([row])) as call:
            result=self.svc.memories('a',query=query,actor_id='p',limit=1)
        data=json.loads(call.call_args.args[1]['messages'][1]['content'])
        self.assertEqual(data['query'],query)
        self.assertEqual({c['event_id'] for c in data['candidates']},{'yes','no'})
        self.assertEqual(result['scanned_documents'],2);self.assertFalse(result['keyword_fallback_used'])
        self.assertEqual(before,self.repo.export())

    def test_no_match_is_empty_not_top_keyword(self):
        with patch('endocrine.semantic.request_json',return_value=self.response([])):
            self.assertEqual(self.svc.memories('a',query='无关问题')['items'],[])

    def test_invented_ids_and_evidence_are_rejected(self):
        for bad in [{'event_id':'invented','relevance':1,'evidence':'秘密','relation':'supports'},
            {'event_id':'yes','relevance':1,'evidence':'他非常诚实从未泄密','relation':'supports'}]:
            with self.subTest(bad=bad),patch('endocrine.semantic.request_json',return_value=self.response([bad])):
                with self.assertRaises(DomainError):self.svc.memories('a',query='问题')

    def test_truncation_and_failure_never_become_partial_success(self):
        with patch('endocrine.semantic.request_json',return_value=self.response([],finish_reason='length')):
            with self.assertRaisesRegex(DomainError,'semantic_output_truncated'):self.svc.memories('a',query='问题')
        with patch('endocrine.semantic.request_json',side_effect=TimeoutError):
            with self.assertRaisesRegex(DomainError,'semantic_provider_unavailable'):self.svc.memories('a',query='问题')

    def test_empty_scope_does_not_call_model(self):
        with patch('endocrine.semantic.request_json') as call:
            self.assertEqual(self.svc.memories('absent',query='问题')['items'],[]);call.assert_not_called()

    def test_long_paragraph_end_is_present(self):
        text='天气和办公用品。'*400+'但是最后我决定搬走，终于轻松了。'
        self.svc.submit({'agent_id':'long','event_id':'long','actor_id':'p','occurred_at':iso(self.svc.clock.now()),'text':text})
        with patch('endocrine.semantic.request_json',return_value=self.response([])) as call:self.svc.memories('long',query='释然')
        body=json.loads(call.call_args.args[1]['messages'][1]['content'])
        self.assertEqual(body['candidates'][0]['text'],text)

    def test_oversized_scope_explicitly_requires_filter(self):
        self.svc.semantic.max_batches=0
        with patch('endocrine.semantic.request_json') as call:
            with self.assertRaisesRegex(DomainError,'semantic_scope_too_large'):self.svc.memories('a',query='问题')
            call.assert_not_called()

if __name__=='__main__':unittest.main()
