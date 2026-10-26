"""Appraise evidence against goals; preserve simultaneous, separately attributed effects."""
import math
from datetime import datetime
from .facts import Fact
from .model import Assessment,Event,contextual_assessment,timestamp,iso,digest
from .social import prepare_relationship,update_evidence,independent_claim_slots


def legacy_candidates(a,event):
    """Explicit compatibility for archived v4/v5 assessments and offline fixtures.

    New HTTP semantic providers must return facts, rather than use this adapter.
    """
    if not a.evidence:return []
    goal='connection';value=0;interaction='none';trust=[]
    if a.observed_affect in ('sad','anxious','tired') and a.target in ('actor','third_party'):value=-.35
    elif a.kind in ('support','affection','gratitude','playful'):value=.7
    elif a.kind=='apology':value=.2;interaction='apology'
    elif a.kind=='joy':value=.8
    elif a.kind in ('rejection','criticism','loss'):
        goal='respect' if a.kind=='criticism' else 'connection';value=-.7
    elif a.kind=='curiosity':goal='exploration';value=.2
    if a.speech_act=='technical':
        goal='competence';value=-.15 if a.kind=='criticism' else 0
    if a.target=='agent' and a.agency=='actor' and a.speech_act=='direct':
        sign='supports' if a.kind in ('support','affection','gratitude') else 'violates' if a.kind in ('rejection','criticism') else None
        if sign:trust=[{'dimension':'benevolence','scope':'interaction','direction':sign,'strength':a.intensity,'behavior_status':'completed_report'}]
    raw={'fact_id':'legacy-candidate','kind':a.kind,'evidence':list(a.evidence)[:3],
        'target':a.target,'agency':a.agency,'speech_act':a.speech_act,'certainty':a.certainty,
        'relevance':a.relevance,'intensity':a.intensity,'controllability':a.controllability,
        'expectedness':a.expectedness,'goal_effects':{goal:value} if value else {},
        'interaction':interaction,'trust_evidence':trust}
    return [Fact.parse(raw,event.text)]


def label_for(goal,effect,fact):
    if effect>0:
        return {'autonomy':'relief','rest':'relief','connection':'warmth','exploration':'curiosity'}.get(goal,'joy')
    if fact.target in ('actor','third_party') and goal=='connection':return 'concern'
    if goal=='competence':return 'task_frustration'
    if goal in ('respect','autonomy') and fact.interaction=='boundary_violation':return 'anger'
    return 'hurt' if fact.target=='agent' else 'disappointment'


def transition(state,event,a,now,seq,config,wall_at=None):
    from .core import advance,clamp,_relationship,_expect,validate_state,_coupled_decay,_timezone
    event=Event.parse(event.to_dict());a=contextual_assessment(a,event)
    wall=now if wall_at is None else wall_at
    s=advance(state,now,config);effective=s['state_time']
    before={k:state[k] for k in ('fast_valence','fast_arousal','mood_valence','stress')}
    reason={'event_id':event.event_id,'kind':a.kind,'target':a.target,'speech_act':a.speech_act,
        'classifier_status':a.classifier_status,'observed_affect':a.observed_affect,'agency':a.agency,
        'certainty':a.certainty,'controllability':a.controllability,'expectedness':a.expectedness,
        'gain':0.0,'trust_delta':0.0,'evidence':list(a.evidence),'late_by_seconds':max(0,wall-timestamp(event.occurred_at)),
        'occurred_at':event.occurred_at,'observed_at':event.observed_at or iso(wall),
        'ingestion_mode':event.ingestion_mode,'effective_at':iso(effective),'facts':[]}
    if event.event_type=='profile_update':s['personality'].update(event.payload);reason['reason']='explicit_personality_update'
    elif event.event_type=='sleep_update':s['physiology']['mode']=event.payload['mode'];reason['reason']='explicit_sleep_schedule_update'
    elif event.event_type=='goal_update':s['appraisal_goals'].update(event.payload);reason['reason']='explicit_goal_update'
    elif event.event_type.startswith('expect_'):reason.update(_expect(s,event,effective,config))
    elif event.event_type=='time_advance':reason['reason']='elapsed_time_only'
    else:
        is_resolution=event.event_type=='semantic_resolution'
        candidates=list(a.facts) if a.facts else legacy_candidates(a,event)
        contact=event.source=='user_message' and event.provenance=='observed_message' and event.actor_id
        original_contact=is_resolution and event.payload.get("original_source")=="user_message"
        external_observation=(event.source=='external_event' and event.provenance=='observed_event') or (
            is_resolution and event.payload.get('original_source')=='external_event' and event.payload.get('original_provenance')=='observed_event')
        observed_social=(external_observation and event.actor_id and event.actor_id!=event.agent_id and any(
            f.target=='agent' and f.agency=='actor' and f.speech_act in ('direct','technical') and f.certainty>=config.relation_evidence_gate
            and any(ev.get('behavior_status') in ('observed','completed_report') for ev in f.trust_evidence) for f in candidates))
        r=_relationship(s,event.actor_id,config) if contact or original_contact or observed_social else None
        if r is not None and contact and not is_resolution:
            r['contact_count']+=1;r['last_contact_at']=event.observed_at or iso(wall)
            r['familiarity']+=.002*(1-r['familiarity'])
        if r is not None and observed_social:r['observed_interaction_count']=r.get('observed_interaction_count',0)+1
        reason['candidate_semantics']='multi_fact_goal_effects' if a.facts else 'legacy_candidate_adapter'
        s['habits']={k:h for k,h in s['habits'].items() if effective-h['t']<7*86400}
        prepared=[]
        for fact in candidates:
            suppressed=fact.speech_act in ('quote','hypothetical','unknown')
            effects={} if suppressed or event.source=='agent_message' else fact.goal_effects
            # A narrated person's autonomy/competence/rest are not the agent's
            # own goals. Only explicitly extracted relational concern/joy is
            # admitted for a non-self target; do not invent empathy from words.
            if fact.target in ('actor','third_party'):
                effects={g:v for g,v in effects.items() if g=='connection'}
            elif fact.target=='unknown':effects={}
            weights={g:s['appraisal_goals'][g] for g,e in effects.items() if e and s['appraisal_goals'][g]>0}
            weight=sum(weights.values())
            key=digest([event.actor_id or event.source,fact.kind,sorted(effects),fact.target])
            previous=s['habits'].get(key,{'count':0.0,'t':effective})
            count=previous['count']*2**(-max(0,effective-previous['t'])/(3600*config.habit_half_life_h))
            habit=max(.3,math.exp(-count/4))
            raw=.35*fact.relevance*fact.certainty*fact.intensity*(1+.2*(1-fact.expectedness))*habit
            raw*=sum(weights.values())/len(weights) if weights else 0
            raw*=(.5+s['personality']['sensitivity'])*(1-.3*(s['personality']['regulation']-.5))
            if 'connection' in weights:raw*=.75+.5*s['personality']['sociability']
            if 'exploration' in weights:raw*=.75+.5*s['personality']['curiosity']
            fatigue=s['physiology']['fatigue']-.3
            negative=any(v<0 for v in effects.values())
            raw*=max(.5,1+.3*fatigue if negative else 1-.15*fatigue)
            prepared.append((fact,effects,weights,weight,key,count,habit,raw))
        total=sum(item[-1] for item in prepared)
        scale=min(1,config.maximum_event_gain/total) if total else 1
        reflection=event.source=='self_reflection' or event.event_type=='recall'
        # First knowledge and archive replay are distinct operations. A raw old
        # world timestamp never silently means the agent had already appraised it.
        observed=timestamp(event.observed_at) if event.observed_at else (timestamp(event.occurred_at) if event.ingestion_mode=='historical_replay' else wall)
        age=max(0,wall-observed)/3600 if not reflection else 0
        reason['affect_age_hours']=age
        relation_slots=independent_claim_slots([item[0] for item in prepared],event.text,config.relation_evidence_gate,
            allow_observed_narration=external_observation)
        for fact_index,(fact,effects,weights,weight,key,count,habit,raw) in enumerate(prepared):
            gain=raw*scale*(.25 if reflection else 1)
            if gain:s['habits'][key]={'count':count+1,'t':effective}
            item={'fact_id':fact.fact_id,'kind':fact.kind,'target':fact.target,'agency':fact.agency,
                'evidence':list(fact.evidence),'gain':gain,'goal_effects':effects,'trust_delta':0.0,
                'proposed_goal_effects':fact.goal_effects,
                'semantic_review_required':bool(set(fact.goal_effects)-set(effects)) or
                    (fact.agency=='agent' and fact.target=='agent' and event.source=='user_message'),
                'interaction':fact.interaction,'habituation':habit}
            for goal in weights:
                component=effects[goal]*weights[goal]/weight
                v=component*gain
                coping=fact.controllability*(1-.3*s['physiology']['fatigue'])*(1-.2*s['physiology']['sleep_debt_h']/24)
                arousal=abs(component)*gain*(.3+.3*(1-coping))
                label=label_for(goal,component,fact)
                if age:
                    # Use the same personality/sleep trajectory as normal advance.
                    pulse=__import__('copy').deepcopy(s)
                    pulse['state_time']=effective-age*3600
                    for field in ('fast_valence','fast_arousal','mood_valence','mood_arousal','stress'):pulse[field]=0.0
                    pulse['emotion_tracks']={}
                    pulse['fast_valence']=v;pulse['fast_arousal']=arousal;pulse['stress']=max(0,-v)*.25
                    aged=advance(pulse,effective,config)
                    values={k:aged[k] for k in ('fast_valence','fast_arousal','mood_valence','mood_arousal','stress')}
                else:values={'fast_valence':v,'fast_arousal':arousal,'mood_valence':0,'mood_arousal':0,'stress':max(0,-v)*.25}
                for field,value in values.items():
                    s[field]=clamp(s[field]+value,-1,1) if field!='stress' else clamp(s[field]+value)
                track_key=digest([label,event.actor_id,fact.target,goal])
                track=s['emotion_tracks'].setdefault(track_key,{'label':label,'activation':0.0,'causes':[],
                    'actor_id':event.actor_id,'target':fact.target,'goal':goal})
                track['activation']=clamp(track['activation']+abs(v)*2**(-age/config.emotion_half_life_h))
                track['causes']=(track['causes']+[event.event_id+':'+fact.fact_id])[-3:]
                item['label']=label
            direct=(r is not None and event.actor_id!=event.agent_id and fact.agency=='actor'
                and fact.target=='agent' and (fact.speech_act=='direct' or (external_observation and fact.speech_act=='technical'))
                and fact.certainty>=config.relation_evidence_gate and not reflection)
            if direct:
                day=datetime.fromtimestamp(observed,_timezone(config.timezone)).date().isoformat()
                if fact.interaction=='apology' or fact.kind=='apology':
                    r['repair_acknowledgements']=(r['repair_acknowledgements']+[event.event_id])[-30:]
                else:
                    for evidence_index,ev in enumerate(fact.trust_evidence):
                        if (fact_index,evidence_index) not in relation_slots:
                            item['relation_evidence_suppressed_as_duplicate_zero_or_unverified']=True
                            continue
                        sign=1 if ev['direction']=='supports' else -1
                        delta,mass=update_evidence(r,ev['dimension'],ev['scope'],sign,ev['strength']*fact.certainty,
                            day,config,serious=fact.intensity>=.8)
                        item['trust_delta']+=delta;item['effective_relation_weight']=item.get('effective_relation_weight',0)+mass
                        r['last_evidence_event_id']=event.event_id
                        if sign<0:r['open_violations']=(r['open_violations']+[{'event_id':event.event_id,'dimension':ev['dimension'],'scope':ev['scope']}])[-50:]
                if fact.interaction=='responsive_reply' and fact.related_event_id:
                    disclosure=s['dialogue']['disclosures'].get(fact.related_event_id)
                    verified_reference=(fact.related_actor_id==event.agent_id and any(ref["event_id"]==fact.related_event_id and ref["meaning"]=="disclosure" for ref in fact.context_evidence))
                    if verified_reference or (disclosure and disclosure['actor_id']==event.agent_id and disclosure['conversation_id']==event.conversation_id):
                        values=fact.responsiveness
                        for dimension,value in values.items():
                            r['perceived_responsiveness'][dimension]+=.1*fact.certainty*(value-r['perceived_responsiveness'][dimension])
                        quality=sum(values.values())/len(values) if values else 0
                        r['closeness']+=.03*fact.certainty*quality*(1-r['closeness'])
                if fact.interaction=='repair_confirmed' and fact.related_event_id:
                    r['open_violations']=[v for v in r['open_violations'] if v['event_id']!=fact.related_event_id]
                congruence=sum(effects[g]*weights[g] for g in weights)/weight if weight else 0
                r['satisfaction']=clamp(r['satisfaction']+.025*fact.certainty*congruence)
            if (r is not None and not reflection and fact.agency=='actor' and fact.speech_act=='direct'
                and fact.interaction=='boundary_request'):
                r.setdefault('expressed_boundaries',[])
                origin=event.payload['original_event_id'] if is_resolution else event.event_id
                r['expressed_boundaries']=(r['expressed_boundaries']+[{'event_id':origin,'fact_id':fact.fact_id,
                    'expressed_by':event.actor_id,'conversation_id':event.conversation_id,'evidence':list(fact.evidence),
                    'observed_at':iso(wall),'semantics':'stated_preference_not_action_authorization'}])[-50:]
            if fact.interaction=='disclosure':
                s['dialogue']['disclosures'][event.event_id]={'actor_id':event.actor_id,'conversation_id':event.conversation_id,'at':effective}
            reason['gain']+=gain;reason['trust_delta']+=item['trust_delta'];reason['facts'].append(item)
        s['dialogue']['disclosures']={k:v for k,v in s['dialogue']['disclosures'].items() if effective-v['at']<=30*86400}
        if reason['facts']:
            last=reason['facts'][-1]
            reason.update(label=last.get('label','neutral'),habituation=last['habituation'],
                effective_relation_weight=sum(f.get('effective_relation_weight',0) for f in reason['facts']))
        reason['reason']='goal_appraisal' if prepared else 'unknown_or_context_only'
    s['revision']+=event.event_type!='semantic_resolution';s['generation']+=event.event_type=='semantic_resolution';s['events_processed']+=event.event_type not in ('time_advance','semantic_resolution') and event.source!='agent_message';s['last_applied_seq']=seq;s['last_appraisal']=reason
    validate_state(s,config)
    return s,{'event_id':event.event_id,'seq':seq,'config_version':config.version,'before':before,
        'after':{k:s[k] for k in before},'appraisal':reason}
