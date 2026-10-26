"""Read a consistent v4.1 snapshot and rebuild v5 into a new database only."""
import argparse
from contextlib import closing
import copy
import json
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from endocrine.core import initial_state, validate_state
from endocrine.model import Assessment, Config, Event, canonical, digest
from endocrine.store import Repository
from tools.replay_events import replay

V41_SIGNATURE='1d508769cc10c7ff3ccadac169142f220de718d59343e1e09fbe9c54d673535c'


def source_snapshot(path):
    uri=Path(path).resolve().as_uri()+'?mode=ro'
    with closing(sqlite3.connect(uri,uri=True)) as c:
        c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
        if c.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Source corrupt')
        if c.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0]!='1':
            raise ValueError('Only v4.1 schema 1 is supported')
        names=('metadata','events','agent_states','initial_states','legacy_archives','state_changes','relationships','expectations','outbox')
        data={name:[dict(r) for r in c.execute('SELECT * FROM '+name)] for name in names}
    for row in data['agent_states']+data['initial_states']:
        state=json.loads(row['state'])
        if state.get('config_signature')!=V41_SIGNATURE:raise ValueError('Source rules are not the supported v4.1 defaults')
    agents={r['agent_id'] for r in data['agent_states']}
    required={r['agent_id'] for r in data['events'] if r['status']=='applied'}|{r['agent_id'] for r in data['initial_states']+data['legacy_archives']}
    if not required<=agents:raise ValueError('Source state missing; restore source before migration')
    return data


def convert_baseline(old,config):
    s=initial_state(old['agent_id'],old['state_time'],config)
    for field in ('revision','last_applied_seq','fast_valence','fast_arousal','mood_valence','mood_arousal',
                  'stress','habits','expectations','events_processed','last_appraisal','legacy'):
        s[field]=copy.deepcopy(old[field])
    from endocrine.core import _relationship
    for person,relation in old['relationships'].items():
        r=_relationship(s,person,config)
        for field in ('closeness','familiarity','contact_count','last_contact_at'):
            r[field]=copy.deepcopy(relation[field])
        r.update(legacy_prior_trust=relation['trust'],legacy_prior_semantics='uncalibrated_v4_prior_preserved_for_review')
    s['legacy']['v4_initial_state_sha256']=digest(old)
    validate_state(s,config)
    return s


def migrate(source,destination):
    if not Path(source).is_file() or Path(destination).exists():raise ValueError('Source must exist and destination must be new')
    data=source_snapshot(source);config=Config();repo=Repository(destination,config)
    with repo.transaction() as c:c.execute("INSERT INTO metadata VALUES('migration_status','building')")
    states={r['agent_id']:json.loads(r['state']) for r in data['agent_states']}
    baselines={r['agent_id']:json.loads(r['state']) for r in data['initial_states']}
    archives={r['agent_id']:json.loads(r['graph']) for r in data['legacy_archives']}
    for agent,old_current in states.items():
        applied=sorted((r for r in data['events'] if r['agent_id']==agent and r['status']=='applied'),key=lambda r:r['seq'])
        if agent in baselines:baseline=convert_baseline(baselines[agent],config)
        elif applied:baseline=initial_state(agent,applied[0]['received_at'],config)
        else:baseline=convert_baseline(old_current,config)
        archive={"graph":archives.get(agent,{}),"v4_audit":{k:[r for r in rows if r.get('agent_id')==agent] for k,rows in data.items() if k!='outbox'}}
        repo.import_state(baseline,archive)
    for row in sorted(data['events'],key=lambda r:r['seq']):
        body=Event.parse(json.loads(row['body'])).to_dict()
        with repo.transaction() as c:
            c.execute('INSERT INTO events(seq,agent_id,event_id,fingerprint,body,received_at,status,assessment,response,error_code) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (row['seq'],row['agent_id'],row['event_id'],digest(body),canonical(body),row['received_at'],
                 'pending' if row['status']=='applied' else ('pending' if row['status']=='processing' else row['status']),
                 row['assessment'],row['response'] if row['status']=='rejected' else None,row.get('error_code')))
        if row['status']=='applied':
            # Process only this historical fact from its original baseline. It is
            # never appended to the already-final v4 state as a new experience.
            with repo.transaction() as c:
                c.execute("UPDATE events SET status='processing',lease_token='migration' WHERE seq=?",(row['seq'],))
            current=repo.get_event(row['agent_id'],row['event_id'])
            assessment=Assessment.parse(json.loads(row['assessment']),body['text'])
            repo.save_assessment(current,assessment,[])
            result=repo.finish(current,assessment)
            if not result or not result['success']:raise ValueError('Historical event no longer valid; migration stopped for review')
    with repo.transaction() as c:
        for row in data['outbox']:
            status='delivered' if row['status']=='delivered' else 'unknown_delivery'
            c.execute('INSERT INTO outbox(operation_id,fingerprint,payload,status,claim_token,updated_at,receipt) VALUES(?,?,?,?,NULL,?,?)',
                (row['operation_id'],row['fingerprint'],row['payload'],status,row['updated_at'],row['receipt']))
    exported=repo.export()
    if replay(exported)!=exported['states']:raise ValueError('v5 replay mismatch')
    with repo.transaction() as c:
        c.execute("UPDATE metadata SET value='complete' WHERE key='migration_status'")
    return {"source_rules":"affect-v4.1-default","source_snapshot_sha256":digest(data),"destination_rules":config.version,
        "source_events":len(data['events']),"destination_events":len(exported['events']),
        "classification_jobs":len(exported['classification_jobs']),"outbox_operations":len(exported['outbox']),
        "memory_entries":len(exported['memory_entries']),"replay_matches":True,
        "state_differences":[{"agent_id":s['agent_id'],"old_trust":{p:r['trust'] for p,r in states[s['agent_id']]['relationships'].items()},
            "new_trust":{p:r['trust'] for p,r in s['relationships'].items()},"events_processed":s['events_processed']} for s in exported['states']],
        "policy":"v4 original states and traces archived; unique facts re-evaluated from baseline; no delivery dispatch"}


def main():
    p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('new_db',type=Path);p.add_argument('--report',type=Path,required=True)
    args=p.parse_args()
    if args.report.resolve() in {args.source.resolve(),args.new_db.resolve()}:
        raise SystemExit("Report must be separate from databases")
    if args.report.exists():raise SystemExit('Report must be a new file')
    report=migrate(args.source,args.new_db)
    with args.report.open('x',encoding='utf-8') as f:
        f.write(json.dumps(report,ensure_ascii=False,indent=2))
    print('Migration complete; source preserved; review report before switching writers.')
if __name__=='__main__':main()
