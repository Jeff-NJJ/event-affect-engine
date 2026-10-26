import hashlib,json,sqlite3,tempfile,unittest
from pathlib import Path
from endocrine.clock import FrozenClock
from endocrine.model import Config,iso,canonical,digest
from endocrine.store import Repository
from endocrine.service import Service
from tools.migrate_v5 import migrate,V50_SIGNATURE
from tools.replay_events import replay

class MigrationV5Tests(unittest.TestCase):
    def test_preserve_unknown_pending_memory_and_dedupe_old_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source.db';dest=Path(tmp)/'target.db'
            repo=Repository(source,Config());svc=Service(repo,clock=FrozenClock(1792288800))
            raw={'agent_id':'a','event_id':'one','actor_id':'p','text':'未理解内容','occurred_at':iso(svc.clock.now())}
            svc.submit(raw);svc.submit({**raw,'event_id':'pending'},wait_seconds=0)
            svc.delivery.queue('op',{'agent_id':'a','conversation_id':'c','text':'未确认发送'})
            with repo.transaction() as c:
                c.execute("UPDATE metadata SET value='2' WHERE key='schema_version'")
                for table in ('agent_states','initial_states'):
                    for row in c.execute('SELECT * FROM '+table).fetchall():
                        state=json.loads(row['state']);state.update(schema_version=2,config_signature=V50_SIGNATURE,config_version='affect-v5.0-default')
                        state.pop('appraisal_goals');state.pop('dialogue')
                        c.execute('UPDATE '+table+' SET state=? WHERE agent_id=?',(canonical(state),row['agent_id']))
                for row in c.execute('SELECT * FROM events').fetchall():
                    old=json.loads(row['body']);old.pop('observed_at');old.pop('ingestion_mode')
                    c.execute('UPDATE events SET body=?,fingerprint=? WHERE seq=?',(canonical(old),digest(old),row['seq']))
            before=hashlib.sha256(source.read_bytes()).hexdigest();report=migrate(source,dest)
            self.assertEqual(before,hashlib.sha256(source.read_bytes()).hexdigest())
            target=Repository(dest,Config());data=target.export()
            self.assertEqual(replay(data),data['states']);self.assertEqual(report['destination_events'],2)
            self.assertEqual(len(data['memory_entries']),1)
            self.assertEqual(data['outbox'][0]['status'],'unknown_delivery')
            self.assertTrue(Service(target,clock=svc.clock).submit(raw)['deduplicated'])
            self.assertEqual(target.get_event('a','pending')['status'],'pending')
            with self.assertRaises(ValueError):migrate(source,dest)

if __name__=='__main__':unittest.main()
