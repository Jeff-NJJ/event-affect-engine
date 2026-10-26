import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from endocrine.model import Config,DomainError
from endocrine.store import Repository
from endocrine.service import Service
from endocrine.operit_native import accept_message,context,receipt


class NativeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.service=Service(Repository(str(Path(self.tmp.name)/'affect.sqlite3'),Config()))
        self.now=self.service.clock.now()
        from endocrine.model import iso
        self.body={'agent_id':'a','actor_id':'u','conversation_id':'c','message_id':'user:123',
            'role':'user','occurred_at':iso(self.now+3600),'client_now':iso(self.now+3600),'text':'合成验收消息'}

    def test_clock_drift_and_retry_do_not_change_event(self):
        first=accept_message(self.service,self.body)
        retry={**self.body,'client_now':self.body['occurred_at']}
        again=accept_message(self.service,retry)
        self.assertEqual(first['event_id'],again['event_id']);self.assertTrue(again['deduplicated'])
        event=json.loads(self.service.repo.get_event('a',first['event_id'])['body'])
        from endocrine.model import timestamp
        self.assertLess(abs(timestamp(event['occurred_at'])-self.now),2)
        self.assertEqual(event['payload']['clock_audit']['original_occurred_at'],self.body['occurred_at'])

    def test_edit_cannot_reapply_existing_message(self):
        accept_message(self.service,self.body)
        with self.assertRaises(DomainError) as error:accept_message(self.service,{**self.body,'text':'edited'})
        self.assertEqual(error.exception.code,'operit_message_edited_after_acceptance')

    def test_assistant_is_context_only_and_receipt_scope_exact(self):
        r=accept_message(self.service,{**self.body,'role':'assistant','message_id':'ai:1'})
        row=self.service.repo.get_event('a',r['event_id']);event=json.loads(row['body'])
        self.assertEqual(event['source'],'agent_message');self.assertEqual(event['actor_id'],'a')
        self.assertEqual(receipt(self.service,'a',r['event_id'])['status'],'pending')
        with self.assertRaises(DomainError):receipt(self.service,'other',r['event_id'])

    def test_query_is_not_an_event_and_failure_is_explicit(self):
        r=context(self.service,{'agent_id':'a','actor_id':'u','conversation_id':'c','recent_assistant':[],'query':'完整问题'})
        self.assertEqual(r['memory_context']['status'],'not_configured')
        with self.service.repo.connect(readonly=True) as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)

    def test_memory_injection_preserves_unverified_provenance(self):
        class External:
            def search(self,scope,query,asof):
                return [{'event_id':'ob:1','text':'合成旧摘要','source':'ob_memory','provenance':'ob_summary',
                    'epistemic_status':'external_memory_unverified','source_refs':['ob_bucket:1']}],{'status':'available','complete':False}
        self.service.grounded.external_memory=External()
        r=context(self.service,{'agent_id':'a','actor_id':'u','conversation_id':'c','recent_assistant':[],'query':'完整问题'})
        self.assertIn('合成旧摘要',r['prompt_suffix']);self.assertEqual(r['memory_context']['item_count'],1)
        self.assertIn('未核验',r['prompt_suffix']);self.assertFalse(r['state_mutated'])
