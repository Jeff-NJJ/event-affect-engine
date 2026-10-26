import json,tempfile,unittest
from pathlib import Path
from endocrine.clock import FrozenClock,VirtualLeaseClock
from endocrine.model import Config,Event,Assessment,DomainError,iso,canonical,digest
from endocrine.store import Repository
from endocrine.service import Service

class InternalNamespaceTests(unittest.TestCase):
    def test_external_messages_cannot_occupy_internal_ids(self):
        for prefix in ('semantic-resolution:','expect-expiry:'):
            with self.assertRaisesRegex(DomainError,'reserved_internal_event_id'):
                Event.parse({'agent_id':'a','event_id':prefix+'reserved','occurred_at':iso(1792288800),'text':'消息'})

    def test_old_database_collision_does_not_mark_job_resolving(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Repository(Path(tmp)/'n.db',Config(),lease_clock=VirtualLeaseClock(1792288800))
            svc=Service(repo,clock=FrozenClock(1792288800))
            svc.submit({'agent_id':'a','actor_id':'p','event_id':'original','occurred_at':iso(svc.clock.now()),'text':'旧内容'})
            job=repo.claim_classification(force=True)
            # Simulate an immutable collision inherited from an earlier release.
            body=Event('a','placeholder',iso(svc.clock.now()),text='占位').to_dict()
            eid='semantic-resolution:'+digest(['a','original']);body['event_id']=eid
            with repo.transaction() as c:c.execute("INSERT INTO events(agent_id,event_id,fingerprint,body,received_at,status) VALUES(?,?,?,?,?,'pending')",
                ('a',eid,digest(body),canonical(body),svc.clock.now()))
            with self.assertRaisesRegex(DomainError,'internal_event_id_conflict'):
                repo.complete_classification(job,Assessment(classifier_status='semantic'),[],resolved_at=svc.clock.now())
            with repo.connect() as c:status=c.execute("SELECT status FROM classification_jobs WHERE event_id='original'").fetchone()[0]
            self.assertEqual(status,'processing')

    def test_full_8000_character_message_and_explicit_limit(self):
        raw={'agent_id':'a','event_id':'long','occurred_at':iso(1792288800),'text':'字'*8000}
        self.assertEqual(len(Event.parse(raw).text),8000)
        with self.assertRaisesRegex(DomainError,'invalid_text'):Event.parse({**raw,'text':'字'*8001})

    def test_imported_memory_ids_are_reserved_and_text_cannot_be_replaced(self):
        for prefix in ('legacy:v3:','legacy:v4:'):
            with self.assertRaisesRegex(DomainError,'reserved_legacy_memory_id'):
                Event.parse({'agent_id':'a','event_id':prefix+'1','occurred_at':iso(1792288800),'text':'新消息'})
        with tempfile.TemporaryDirectory() as tmp:
            repo=Repository(Path(tmp)/'i.db',Config());svc=Service(repo,clock=FrozenClock(1792288800))
            svc.submit({'agent_id':'a','event_id':'one','actor_id':'p','occurred_at':iso(svc.clock.now()),'text':'不可篡改原文'})
            memory=repo.find_memory('a','one');memory['text']='替换过的原文'
            with self.assertRaisesRegex(DomainError,'memory_event_id_content_conflict'):
                with repo.transaction() as c:repo._insert_memory(c,memory)
            self.assertEqual(repo.find_memory('a','one')['text'],'不可篡改原文')

if __name__=='__main__':unittest.main()
