import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from endocrine.core import initial_state
from endocrine.model import Assessment, Config, DomainError, Event, canonical, digest, iso
from endocrine.service import Service
from endocrine.store import Repository
from tools.migrate_v4 import V41_SIGNATURE, migrate
from tools.replay_events import replay


class MigrateV4Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.source=Path(self.tmp.name)/'old.db';self.target=Path(self.tmp.name)/'new.db'
        self.now=1792141200

    def fixture(self):
        config=Config();state=initial_state('a',self.now,config)
        for key in ('generation','physiology','personality','emotion_tracks'):state.pop(key)
        state.update(schema_version=1,config_version='affect-v4.1-default',config_signature=V41_SIGNATURE)
        current=json.loads(canonical(state));current.update(revision=1,last_applied_seq=1,events_processed=1)
        e=Event('a','existing',iso(self.now),text='旧事实',actor_id='p').to_dict()
        e.pop('observed_at');e.pop('ingestion_mode')  # actual old wire defaults
        a=Assessment(classifier_status='provider_unavailable').to_dict()
        rows={'metadata':[{'key':'schema_version','value':'1'}],
            'agent_states':[{'agent_id':'a','state':canonical(current)}],
            'initial_states':[{'agent_id':'a','state':canonical(state)}],
            'legacy_archives':[], 'state_changes':[], 'relationships':[], 'expectations':[],
            'events':[{'seq':1,'agent_id':'a','event_id':'existing','body':canonical(e),'fingerprint':digest(e),
                'received_at':self.now,'status':'applied','assessment':canonical(a),'response':'{}','error_code':None}],
            'outbox':[{'operation_id':'sent-unknown','fingerprint':'f','payload':'{}','status':'sending',
                'claim_token':'old-owner','updated_at':self.now,'receipt':None}]}
        shapes={'metadata':['key','value'],'agent_states':['agent_id','state'],'initial_states':['agent_id','state'],
            'legacy_archives':['agent_id','graph'],'state_changes':['agent_id','trace'], 'relationships':['agent_id','data'],
            'expectations':['agent_id','data']}
        with closing(sqlite3.connect(self.source)) as c:
            for name,items in rows.items():
                cols=list(items[0]) if items else shapes[name]
                c.execute('CREATE TABLE '+name+'('+','.join(cols)+')')
                for item in items:c.execute('INSERT INTO '+name+' VALUES('+','.join('?' for _ in cols)+')',tuple(item[k] for k in cols))
            c.commit()

    def test_source_preserved_ids_replay_retry_jobs_and_unknown_delivery(self):
        self.fixture();before=hashlib.sha256(self.source.read_bytes()).hexdigest()
        report=migrate(self.source,self.target)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),before)
        self.assertEqual(report['source_events'],report['destination_events'])
        self.assertEqual(report['classification_jobs'],1)
        repo=Repository(self.target,Config());data=repo.export()
        self.assertEqual(replay(data),data['states'])
        self.assertEqual(data['states'][0]['events_processed'],1)
        self.assertEqual(data['outbox'][0]['status'],'unknown_delivery')
        e=json.loads(data['events'][0]['body'])
        self.assertTrue(Service(repo).submit(e)['deduplicated'])

    def test_existing_target_and_unsupported_rules_rejected(self):
        self.fixture()
        self.target.write_text('preserve',encoding='utf-8')
        with self.assertRaises(ValueError):migrate(self.source,self.target)
        self.assertEqual(self.target.read_text(encoding='utf-8'),'preserve')

    def test_incomplete_migration_refuses_startup(self):
        repo=Repository(self.target,Config())
        with repo.transaction() as c:c.execute("INSERT INTO metadata VALUES('migration_status','building')")
        with self.assertRaises(DomainError):Repository(self.target,Config())
