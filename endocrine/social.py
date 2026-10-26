"""Research-informed constructs; update rates are explicit engineering assumptions."""
import math
from datetime import date,timedelta
from .model import DomainError,finite,identity

DIMENSIONS=('ability','benevolence','integrity')


def independent_claim_slots(facts,text,gate,*,allow_observed_narration=False):
    """One quoted observation is not multiple independent trust observations.

    Compare literal provenance spans, never keywords or semantic categories.
    Keep the strongest overlapping claim for a dimension/scope/direction.
    """
    claims=[]
    for fi,fact in enumerate(facts):
        presentation_ok=fact.speech_act=='direct' or (allow_observed_narration and fact.speech_act=='technical')
        if (fact.target!='agent' or fact.agency!='actor' or not presentation_ok or fact.certainty<gate
            or fact.interaction in ('apology','boundary_request') or fact.kind=='apology'):continue
        spans=[(text.index(e),text.index(e)+len(e)) for e in fact.evidence]
        for ei,ev in enumerate(fact.trust_evidence):
            if ev.get('behavior_status') not in ('observed','completed_report'):continue
            strength=ev['strength']*fact.certainty
            if strength>0:claims.append((strength,fi,ei,(ev['dimension'],ev['scope'],ev['direction']),spans))
    kept=[];slots=set()
    for claim in sorted(claims,key=lambda x:(-x[0],x[1],x[2])):
        _,fi,ei,key,spans=claim
        duplicate=any(old[3]==key and any(a<d and c<b for a,b in spans for c,d in old[4]) for old in kept)
        if not duplicate:kept.append(claim);slots.add((fi,ei))
    return slots


def prepare_relationship(r,config):
    if 'trustworthiness' not in r:
        r['trustworthiness']={d:{} for d in DIMENSIONS}
        r['satisfaction']=.5
        r['perceived_responsiveness']={'understanding':.5,'validation':.5,'care':.5}
        r['open_violations']=[]
        r['repair_acknowledgements']=[]
        r['learning_mass']={}
        r['expressed_boundaries']=[]
        r['observed_interaction_count']=0
        r['trust_semantics']='uncalibrated_belief_index_not_a_probability_or_permission'
    return r


def update_evidence(r,dimension,scope,sign,strength,day,config,*,serious=False):
    prepare_relationship(r,config)
    if dimension not in DIMENSIONS:raise DomainError('invalid_trust_dimension')
    identity(scope,'trust_scope')
    beliefs=r['trustworthiness'][dimension]
    p=beliefs.setdefault(scope,{'alpha':config.relation_prior_mass,'beta':config.relation_prior_mass,
        'supports':0,'violations':0})
    key=day+':'+dimension+':'+scope
    # Diminishing influence is strictly positive. No late severe fact is zeroed.
    previous=r['learning_mass'].get(key,0.0)
    weight=strength*config.relation_evidence_weight/math.sqrt(1+previous)
    if serious and sign<0:weight=max(weight,strength*config.relation_evidence_weight)
    # Retain a bounded recent window without letting an out-of-order historical
    # observation erase the newer day's evidence counters.
    latest=max([day]+[k[:10] for k in r['learning_mass']])
    cutoff=(date.fromisoformat(latest)-timedelta(days=30)).isoformat()
    r['learning_mass']={k:v for k,v in r['learning_mass'].items() if k[:10]>=cutoff}
    if day>=cutoff:r['learning_mass'][key]=previous+weight
    old=r['trust']
    p['alpha' if sign>0 else 'beta']+=weight
    p['supports' if sign>0 else 'violations']+=1
    r['alpha' if sign>0 else 'beta']+=weight
    r['positive_evidence' if sign>0 else 'negative_evidence']+=1
    r['evidence_count']+=1
    r['trust']=r['alpha']/(r['alpha']+r['beta'])
    mass=r['alpha']+r['beta']-2*config.relation_prior_mass
    r['confidence']=mass/(mass+2*config.relation_prior_mass)
    return r['trust']-old,weight
