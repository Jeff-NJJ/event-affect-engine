"""Multiple evidence-bound facts and semantic goal effects, not numeric brain commands."""
from dataclasses import dataclass,asdict,field
from .model import DomainError,finite,identity

GOALS={'connection','respect','autonomy','competence','exploration','rest'}
SCOPES={'general','interaction','communication','confidentiality','commitments','technical_work','care','boundaries'}
INTERACTIONS={'none','disclosure','responsive_reply','boundary_violation','promise_fulfilled','promise_broken',
    'apology','repair_confirmed','boundary_request'}


@dataclass(frozen=True)
class Fact:
    fact_id:str
    evidence:tuple
    goal_effects:dict=field(default_factory=dict)
    kind:str='neutral'
    target:str='unknown'
    agency:str='unknown'
    speech_act:str='unknown'
    certainty:float=0.0
    relevance:float=0.0
    intensity:float=0.0
    controllability:float=.5
    expectedness:float=.5
    interaction:str='none'
    related_event_id:str|None=None
    trust_evidence:tuple=()
    responsiveness:dict=field(default_factory=dict)
    context_evidence:tuple=()
    related_actor_id:str|None=None

    def to_dict(self):return asdict(self)

    @classmethod
    def parse(cls,raw,text):
        if not isinstance(raw,dict) or set(raw)-set(cls.__dataclass_fields__):raise DomainError('invalid_fact')
        data=dict(raw)
        data['evidence']=tuple(data.get('evidence',())) if isinstance(data.get('evidence'),(list,tuple)) else ()
        if not 1<=len(data['evidence'])<=3 or any(not isinstance(e,str) or not e or len(e)>240 or e not in text for e in data['evidence']):
            raise DomainError('fact_evidence_required_in_current_text')
        for name in ("trust_evidence","context_evidence"):
            if not isinstance(data.get(name,()),(list,tuple)):
                raise DomainError("invalid_fact_collection")
            data[name]=tuple(dict(v) if isinstance(v,dict) else v for v in data.get(name,()))
        try:f=cls(**data)
        except TypeError:raise DomainError('invalid_fact_fields') from None
        identity(f.fact_id,'fact_id');identity(f.kind,'fact_kind')
        if f.target not in ('agent','actor','third_party','unknown') or f.agency not in ('agent','actor','external','unknown'):
            raise DomainError('invalid_fact_attribution')
        if f.speech_act not in ('direct','quote','hypothetical','technical','unknown') or f.interaction not in INTERACTIONS:
            raise DomainError('invalid_fact_interaction')
        for name in ('certainty','relevance','intensity','controllability','expectedness'):finite(getattr(f,name))
        if not isinstance(f.goal_effects,dict) or set(f.goal_effects)-GOALS:raise DomainError('invalid_goal_effects')
        for effect in f.goal_effects.values():finite(effect,-1,1)
        if f.related_event_id is not None:identity(f.related_event_id,'related_event_id')
        if not isinstance(f.responsiveness,dict) or set(f.responsiveness)-{'understanding','validation','care'}:
            raise DomainError('invalid_responsiveness')
        for value in f.responsiveness.values():finite(value)
        if len(f.context_evidence)>2:raise DomainError("too_many_context_references")
        for item in f.context_evidence:
            if (not isinstance(item,dict) or set(item)!={"event_id","evidence","meaning"}
                    or item["meaning"] not in ("disclosure","repair_reference")
                    or not isinstance(item["evidence"],str) or not 1<=len(item["evidence"])<=240):
                raise DomainError("invalid_context_reference")
            identity(item["event_id"],"context_event_id")
        if f.related_actor_id is not None:identity(f.related_actor_id,"related_actor_id")
        if len(f.trust_evidence)>3:raise DomainError('too_many_trust_dimensions')
        for item in f.trust_evidence:
            if not isinstance(item,dict) or set(item)-{'dimension','scope','direction','strength','behavior_status'} or not {'dimension','scope','direction','strength'}<=set(item):raise DomainError('invalid_trust_evidence')
            if item['dimension'] not in ('ability','benevolence','integrity') or item['direction'] not in ('supports','violates'):
                raise DomainError('invalid_trust_evidence_dimension')
            identity(item['scope'],'trust_scope');finite(item['strength'])
            if item['scope'] not in SCOPES:raise DomainError('unknown_trust_scope')
            item.setdefault('behavior_status','unknown')
            if item['behavior_status'] not in ('observed','completed_report','intent','request','interpretation','unknown'):
                raise DomainError('invalid_behavior_status')
        return f
