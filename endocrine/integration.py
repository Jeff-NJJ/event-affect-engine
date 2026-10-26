"""Read-only, ordered conclusion feed over committed events. Never raw dialogue."""
import json
from .model import DomainError,digest,identity,iso


def conclusion_feed(repo,agent,after=0,limit=100):
    identity(agent,'agent_id')
    if type(after) is not int or after<0 or type(limit) is not int or not 1<=limit<=100:
        raise DomainError('invalid_change_cursor')
    with repo.connect(readonly=True) as c:
        c.execute('BEGIN')
        high=c.execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE agent_id=?',(agent,)).fetchone()[0]
        pending=c.execute("SELECT MIN(seq) FROM events WHERE agent_id=? AND status NOT IN ('applied','rejected')",(agent,)).fetchone()[0]
        stable=high if pending is None else c.execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE agent_id=? AND seq<?',(agent,pending)).fetchone()[0]
        rows=c.execute('SELECT e.*,s.trace FROM events e LEFT JOIN state_changes s ON e.agent_id=s.agent_id AND e.event_id=s.event_id '
            'WHERE e.agent_id=? AND e.seq>? ORDER BY e.seq LIMIT ?',(agent,after,limit)).fetchall()
        cursor=after;items=[];blocked=False
        for row in rows:
            if row['status'] not in ('applied','rejected'):blocked=True;break
            e=json.loads(row['body'])
            if row['status']=='applied':
                if not row['trace']:raise DomainError('change_trace_missing',503)
                t=json.loads(row['trace'])['appraisal']
                # Legacy replay remains read-only and cannot be reintroduced as
                # fresh emotional conclusions. Assistant context has no effect.
                eligible=(e.get('ingestion_mode','live')!='historical_replay' and e.get('source')!='agent_message'
                          and not e['event_id'].startswith('legacy:'))
                delta=t.get('trust_delta',0)
                expectation=t.get('status') if e.get('event_type')=='expect_resolve' else None
                if eligible and (delta!=0 or expectation in ('fulfilled','unmet','withdrawn','expired_unknown')):
                    items.append({'archive_id':'affect-conclusion:'+digest([agent,e['event_id'],repo.config.signature]),
                        'agent_id':agent,'actor_id':e.get('actor_id'),'source_event_id':e['event_id'],'source_seq':row['seq'],
                        'occurred_at':e['occurred_at'],'known_at':iso(row['received_at']),
                        'conclusion':{'trust_delta':delta,'expectation_status':expectation,
                            'classifier_status':t.get('classifier_status','system_command'),
                            'interpretation':'agent_assessment_not_observed_world_fact'},
                        'origin_system':'event_affect','lineage':['event_affect:'+agent+':'+e['event_id']]})
            cursor=row['seq']
    return {'success':True,'agent_id':agent,'after':after,'next_cursor':cursor,'high_watermark':high,'stable_start_cursor':stable,
        'items':items,'blocked_on_pending':blocked,'complete_to_high_watermark':cursor>=high,
        'raw_dialogue_included':False,'state_mutated':False,'messages_sent':0}
