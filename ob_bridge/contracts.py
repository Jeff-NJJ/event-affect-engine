"""Explicit provenance and temporal scope. No keyword-based relevance rules."""
import copy
from urllib.parse import urlsplit

from endocrine.model import DomainError, canonical, digest, finite, identity, iso, timestamp
from endocrine.expression import bounded_text


def local_or_https(url):
    p=urlsplit(url)
    if p.username or p.password or p.fragment or not p.hostname or (
        p.scheme!='https' and not (p.scheme=='http' and p.hostname in ('127.0.0.1','localhost','::1'))):
        raise DomainError('integration_endpoint_requires_https_or_loopback')
    return url


def search_request(raw):
    if not isinstance(raw,dict) or set(raw)!={'agent_id','actor_id','conversation_id','query','as_of','limit'}:
        raise DomainError('invalid_ob_search_request')
    result={k:identity(raw[k],k) for k in ('agent_id','actor_id','conversation_id')}
    result['query']=bounded_text(raw['query'],8000,'invalid_ob_query')
    result['as_of']=iso(timestamp(raw['as_of']))
    if type(raw['limit']) is not int or not 1<=raw['limit']<=16:raise DomainError('invalid_ob_limit')
    result['limit']=raw['limit']
    return result


def normalize_memory(raw,request,scope_id):
    required={'id','scope_id','text','kind','origin_system','known_at','valid_from','valid_to','source_refs','lineage','relevance'}
    if not isinstance(raw,dict) or not required<=set(raw) or set(raw)-required-{'temporal_basis','original_timestamps'}:
        raise DomainError('invalid_ob_memory',503)
    rid=identity(raw['id'],'ob_record_id')
    if raw['scope_id']!=scope_id:raise DomainError('ob_scope_mismatch',503)
    text=bounded_text(raw['text'],8000,'invalid_ob_text')
    if raw['kind'] not in ('source_excerpt','summary','inference'):raise DomainError('invalid_ob_kind',503)
    origin=identity(raw['origin_system'],'origin_system')
    if not isinstance(raw['lineage'],list) or len(raw['lineage'])>64:raise DomainError('invalid_ob_lineage',503)
    lineage=[identity(v,'lineage_id') for v in raw['lineage']]
    refs=raw['source_refs']
    if not isinstance(refs,list) or len(refs)>32:raise DomainError('invalid_ob_source_refs',503)
    refs=[identity(v,'source_ref') for v in refs]
    # Reject returned interpretations from this affect engine as independent
    # evidence, even if the memory server has renamed their titles/IDs.
    if origin=='event_affect' or any(x.startswith('event_affect:') for x in lineage):
        return None,'self_derived_feedback_excluded'
    known=timestamp(raw['known_at']);asof=timestamp(request['as_of'])
    if known>asof:return None,'future_knowledge_excluded'
    start=timestamp(raw['valid_from']) if raw['valid_from'] is not None else None
    end=timestamp(raw['valid_to']) if raw['valid_to'] is not None else None
    if start is not None and end is not None and end<start:raise DomainError('invalid_ob_temporal_interval',503)
    score=finite(raw['relevance'])
    # Summaries, even with valid references, are not verified observations or
    # a record of an actual assistant turn. No summary can certify prior_self.
    normalized={**copy.deepcopy(raw),'known_at':iso(known),'valid_from':iso(start) if start is not None else None,
        'valid_to':iso(end) if end is not None else None,'source_refs':refs,'lineage':lineage,
        'source':'ob_memory','provenance':'ob_'+raw['kind'],'epistemic_status':'external_memory_unverified',
        'agent_id':request['agent_id'],'actor_id':request['actor_id'],
        'event_id':'ob:'+digest([scope_id,rid,raw]),'text_hash':digest(text),
        'valid_at_query_time':(start is None or start<=asof) and (end is None or asof<end),
        'relevance':score}
    return normalized,None


def archive_item(raw):
    required={'archive_id','agent_id','actor_id','source_event_id','source_seq','occurred_at','known_at','conclusion','lineage','origin_system'}
    if not isinstance(raw,dict) or set(raw)!=required:raise DomainError('invalid_archive_item')
    for k in ('archive_id','agent_id','source_event_id'):identity(raw[k],k)
    if raw['actor_id'] is not None:identity(raw['actor_id'],'actor_id')
    if type(raw['source_seq']) is not int or raw['source_seq']<1:raise DomainError('invalid_source_seq')
    timestamp(raw['occurred_at']);timestamp(raw['known_at'])
    if raw['origin_system']!='event_affect':raise DomainError('invalid_archive_origin')
    if not isinstance(raw['lineage'],list) or raw['lineage']!=['event_affect:'+raw['agent_id']+':'+raw['source_event_id']]:
        raise DomainError('invalid_archive_lineage')
    conclusion=raw['conclusion']
    if not isinstance(conclusion,dict) or set(conclusion)!={'trust_delta','expectation_status','classifier_status','interpretation'}:
        raise DomainError('invalid_archive_conclusion')
    finite(conclusion['trust_delta'],-1,1)
    if conclusion['expectation_status'] not in (None,'fulfilled','unmet','withdrawn','expired_unknown'):
        raise DomainError('invalid_archive_expectation')
    identity(conclusion['classifier_status'],'classifier_status')
    if conclusion['interpretation']!='agent_assessment_not_observed_world_fact':raise DomainError('invalid_archive_interpretation')
    if len(canonical(raw).encode())>8000:raise DomainError('archive_too_large')
    return copy.deepcopy(raw)
