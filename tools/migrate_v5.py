"""Offline v5.0 -> v5.1 migration. Source is read-only; target must be new."""
import argparse,copy,json,sqlite3,sys
from contextlib import closing
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from endocrine.core import initial_state,validate_state,_relationship
from endocrine.model import Config,Event,Assessment,canonical,digest
from endocrine.store import Repository
from tools.replay_events import replay

V50_SIGNATURE='86ea7cf3349325d9194cc69486976c12dea4eba3157a09da13afc801904e0778'
TABLES=('metadata','events','agent_states','initial_states','legacy_archives','state_changes',
    'relationships','expectations','outbox','delivery_log','classification_jobs','assessment_history','state_history','memory_entries')

def source_snapshot(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as c:
        c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
        if c.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Source corrupt')
        if c.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0]!='2':raise ValueError('Expected v5.0 schema 2')
        data={name:[dict(r) for r in c.execute('SELECT * FROM '+name)] for name in TABLES}
    for row in data['agent_states']+data['initial_states']:
        if json.loads(row['state']).get('config_signature')!=V50_SIGNATURE:raise ValueError('Unsupported v5.0 rule signature')
    have={r['agent_id'] for r in data['agent_states']}
    required={r['agent_id'] for r in data['events'] if r['status']=='applied'}|{r['agent_id'] for r in data['initial_states']+data['legacy_archives']}
    if not required<=have:raise ValueError('Source state missing')
    return data

def baseline(old,config):
    s=initial_state(old['agent_id'],old['state_time'],config)
    for field in ('revision','last_applied_seq','fast_valence','fast_arousal','mood_valence','mood_arousal',
        'stress','habits','expectations','events_processed','last_appraisal','legacy','personality','physiology','emotion_tracks'):
        s[field]=copy.deepcopy(old[field])
    for label,track in s['emotion_tracks'].items():track.setdefault('label',label)
    for person,old_r in old['relationships'].items():
        r=_relationship(s,person,config)
        for field in ('closeness','familiarity','contact_count','last_contact_at'):r[field]=copy.deepcopy(old_r[field])
        r['legacy_prior_trust']=old_r['trust']
        r['legacy_prior_semantics']='v5_0_aggregate_not_invented_dimension_evidence'
    s['legacy']['v5_0_baseline_sha256']=digest(old);validate_state(s,config)
    return s

def migrate(source,destination):
    if not Path(source).is_file() or Path(destination).exists():raise ValueError('Source must exist and destination must be new')
    data=source_snapshot(source);config=Config();repo=Repository(destination,config)
    with repo.transaction() as c:c.execute("INSERT INTO metadata VALUES('migration_status','building')")
    states={r['agent_id']:json.loads(r['state']) for r in data['agent_states']}
    starts={r['agent_id']:json.loads(r['state']) for r in data['initial_states']}
    for agent,old in states.items():
        rows=sorted((r for r in data['events'] if r['agent_id']==agent and r['status']=='applied'),key=lambda r:r['seq'])
        start=baseline(starts[agent],config) if agent in starts else (initial_state(agent,rows[0]['received_at'],config) if rows else baseline(old,config))
        archive={'v5_0_audit':{name:[r for r in values if r.get('agent_id')==agent] for name,values in data.items()}}
        repo.import_state(start,archive)
    for row in sorted(data['events'],key=lambda r:r['seq']):
        event=Event.parse(json.loads(row['body']));body=event.to_dict()
        with repo.transaction() as c:
            c.execute('INSERT INTO events(seq,agent_id,event_id,fingerprint,body,received_at,effective_at,status,assessment,response,error_code) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (row['seq'],row['agent_id'],row['event_id'],digest(body),canonical(body),row['received_at'],row['received_at'],
                'pending' if row['status'] in ('applied','processing') else row['status'],row['assessment'],
                row['response'] if row['status']=='rejected' else None,row.get('error_code')))
        if row['status']=='applied':
            with repo.transaction() as c:c.execute("UPDATE events SET status='processing',lease_token='migration' WHERE seq=?",(row['seq'],))
            claimed=repo.get_event(event.agent_id,event.event_id)
            assessment=Assessment.parse(json.loads(row['assessment']),event.text)
            repo.save_assessment(claimed,assessment,[])
            result=repo.finish(claimed,assessment)
            if not result or not result['success']:raise ValueError('Historical event rejected under new rules; review incomplete target')
    with repo.transaction() as c:
        for m in data['memory_entries']:
            if not c.execute('SELECT 1 FROM memory_entries WHERE agent_id=? AND event_id=?',(m['agent_id'],m['event_id'])).fetchone():repo._insert_memory(c,m)
        for row in data['outbox']:
            status='delivered' if row['status']=='delivered' else 'unknown_delivery'
            c.execute('INSERT INTO outbox(operation_id,fingerprint,payload,status,updated_at,receipt) VALUES(?,?,?,?,?,?)',
                (row['operation_id'],row['fingerprint'],row['payload'],status,row['updated_at'],row['receipt']))
        for row in data['delivery_log']:
            c.execute('INSERT INTO delivery_log(operation_id,status,occurred_at,evidence) VALUES(?,?,?,?)',
                tuple(row[k] for k in ('operation_id','status','occurred_at','evidence')))
    exported=repo.export()
    if replay(exported)!=exported['states']:raise ValueError('Migration replay mismatch')
    with repo.transaction() as c:c.execute("UPDATE metadata SET value='complete' WHERE key='migration_status'")
    return {'source_rules':'affect-v5.0-default','destination_rules':config.version,'source_snapshot_sha256':digest(data),
        'source_events':len(data['events']),'destination_events':len(exported['events']),
        'source_memories':len(data['memory_entries']),'destination_memories':len(exported['memory_entries']),
        'replay_matches':True,'outbox_auto_dispatched':False,
        'policy':'Original states, judgments and histories archived. Re-evaluate unique facts from initial baseline; no invented dimension evidence; unknown sends require reconciliation.',
        'state_differences':[{'agent_id':s['agent_id'],'old_trust':{p:r['trust'] for p,r in states[s['agent_id']]['relationships'].items()},
            'new_trust':{p:r['trust'] for p,r in s['relationships'].items()}} for s in exported['states']]}

def main():
    p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('new_db',type=Path);p.add_argument('--report',type=Path,required=True)
    a=p.parse_args()
    if a.report.exists() or a.report.resolve() in {a.source.resolve(),a.new_db.resolve()}:raise SystemExit('Report must be a separate new file')
    report=migrate(a.source,a.new_db)
    with a.report.open('x',encoding='utf-8') as f:f.write(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('Migration complete; source preserved; review differences before switching writers.')
if __name__=='__main__':main()
