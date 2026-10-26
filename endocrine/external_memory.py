"""Trusted server-to-server OB evidence; never accepts history from API callers."""
from .model import DomainError,identity,iso
from .transport import request_json
from ob_bridge.contracts import local_or_https


class OBMemoryClient:
    def __init__(self,base,token):self.base,self.token=local_or_https(base).rstrip('/'),token

    def search(self,scope,query,as_of):
        body={k:scope[k] for k in ('agent_id','actor_id','conversation_id')}
        body.update(query=query,as_of=iso(as_of),limit=8)
        result=request_json(self.base+'/bridge/search',body,headers={'X-Token':self.token,
            'Content-Type':'application/json'},timeout=35,max_bytes=131072)
        if (not isinstance(result,dict) or result.get('success') is not True
            or any(result.get(k)!=scope[k] for k in ('agent_id','actor_id','conversation_id'))
            or not isinstance(result.get('items'),list) or len(result['items'])>8):
            raise DomainError('invalid_ob_bridge_response',503)
        records=[]
        for r in result['items']:
            if (not isinstance(r,dict) or r.get('source')!='ob_memory' or r.get('epistemic_status')!='external_memory_unverified'
                or r.get('agent_id')!=scope['agent_id'] or r.get('actor_id')!=scope['actor_id']
                or r.get('origin_system')=='event_affect' or any(x.startswith('event_affect:') for x in r.get('lineage',[]))):
                raise DomainError('invalid_ob_bridge_evidence',503)
            identity(r.get('event_id'),'ob_evidence_id')
            if not isinstance(r.get('text'),str) or not r['text'] or len(r['text'])>8000:
                raise DomainError('invalid_ob_bridge_evidence',503)
            records.append(r)
        return records,{'status':'available','complete':False,'fingerprint':result.get('evidence_fingerprint'),
            'fetched_at':result.get('fetched_at'),'coverage':'ob_top_k_derived_memory_not_verified_history'}
