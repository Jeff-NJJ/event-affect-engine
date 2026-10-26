import json
import time
from collections import Counter

from endocrine.model import DomainError, canonical, digest, iso, timestamp
from .contracts import normalize_memory,search_request


class BridgeService:
    def __init__(self,backend,store,clock=time.time,reranker=None):
        self.backend,self.store,self.clock,self.reranker=backend,store,clock,reranker

    def search(self,raw):
        request=search_request(raw)
        scope,items=self.backend.search(request,timeout=8)
        records=[];excluded=Counter();seen={}
        for item in items:
            record,reason=normalize_memory(item,request,scope)
            if reason:excluded[reason]+=1;continue
            if record['id'] in seen:
                if seen[record['id']]!=digest(record):raise DomainError('ob_record_version_conflict',503)
                continue
            seen[record['id']]=digest(record);records.append(record)
        # Relevance is the backend's whole-query score, not a truth probability.
        # Don't make an old enduring boundary disappear via an arbitrary decay λ.
        if self.reranker is not None and records:
            from endocrine.semantic import SemanticMemory
            deadline=time.monotonic()+22
            batches=list(SemanticMemory.batches(records))
            if len(batches)>4:raise DomainError('ob_semantic_scope_limit',503)
            selected=[]
            def rank(batch):
                remaining=deadline-time.monotonic()
                if remaining<1:raise DomainError('ob_semantic_deadline',503)
                return self.reranker.rank(request['query'],batch,min(request['limit'],len(batch)),timeout=remaining)
            for batch in batches:selected.extend(rank(batch))
            if len(batches)>1 and selected:
                if len(list(SemanticMemory.batches(selected)))>1:raise DomainError('ob_semantic_merge_limit',503)
                selected=rank(selected)
            selected=selected[:request['limit']]
        elif getattr(self.backend,'config',{}).get('backend')=='ob_structured_v1':
            if records:raise DomainError('ob_semantic_cloud_not_configured',503)
            selected=[]
        else:
            records.sort(key=lambda r:(-r['relevance'],r['id']))
            selected=records[:request['limit']]
        return {'success':True,'agent_id':request['agent_id'],'actor_id':request['actor_id'],
            'conversation_id':request['conversation_id'],'items':selected,'excluded':dict(excluded),
            'retrieval_mode':'cloud_full_paragraph_embeddings' if getattr(self.backend,'config',{}).get('backend')=='ob_semantic_v2' else 'ob_semantic_query','provenance':'external_memory_unverified',
            'evidence_fingerprint':digest(selected),'fetched_at':iso(self.clock()),
            'complete':False,'coverage':'backend_top_k_not_exhaustive_history',
            'state_events_created':0,'messages_sent':0}

    def dispatch_one(self):
        job=self.store.claim(self.clock())
        if job is None:return {'success':True,'status':'no_eligible_job'}
        try:
            receipt=self.backend.archive(json.loads(job['body']),timeout=30)
            if (not isinstance(receipt,dict) or receipt.get('archive_id')!=job['archive_id']
                or receipt.get('fingerprint')!=job['fingerprint'] or receipt.get('status')!='stored'):
                raise DomainError('ob_archive_receipt_mismatch',409)
            status='delivered'
        except Exception:
            status='unknown';receipt={'archive_id':job['archive_id'],'status':'unknown','retry_permitted':False}
        self.store.settle(job,status,receipt,self.clock())
        return {'success':status=='delivered','archive_id':job['archive_id'],'status':status,'messages_sent':0}

    def consolidation(self,start,end):
        lo,hi=timestamp(start),timestamp(end)
        if hi<=lo:raise DomainError('invalid_consolidation_interval')
        # Same IDs as daytime channel B. This is a material view, never a second
        # archival writer or a source of re-applied emotional experiences.
        items=[json.loads(r['body']) for r in self.store.rows()
            if lo<=timestamp(json.loads(r['body'])['known_at'])<hi]
        return {'schema':'ob-consolidation-v1','start':iso(lo),'end':iso(hi),'items':items,
            'material_id':digest([start,end,items]),'writes_performed':0,
            'instruction':'Interpretations only; consume archive_id once. Do not replay as experiences or rearchive through Grow.'}

    def reconcile_one(self):
        rows=[r for r in self.store.rows() if r['status']=='unknown']
        if not rows:return {'success':True,'status':'no_unknown_job'}
        job=min(rows,key=lambda r:r['updated_at'])
        try:
            receipt=self.backend.lookup(job['archive_id'],timeout=8)
            if not isinstance(receipt,dict) or receipt.get('archive_id')!=job['archive_id']:raise ValueError()
            if receipt.get('status')=='stored' and receipt.get('fingerprint')==job['fingerprint']:
                status='delivered'
            elif receipt.get('status')=='not_found' and receipt.get('authoritative') is True and receipt.get('dedupe_supported') is True:
                status='pending'
            else:raise ValueError()
            self.store.reconcile(job['archive_id'],job['fingerprint'],status,receipt,self.clock())
            return {'success':True,'archive_id':job['archive_id'],'status':status}
        except Exception:return {'success':False,'archive_id':job['archive_id'],'status':'unknown'}
