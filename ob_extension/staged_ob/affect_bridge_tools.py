"""Opt-in OB-side tools, designed against the supplied 2026-10-18 source.

Does not call breath/hold/grow, touch buckets, or import server at module load.
Derived archives belong to OB but live apart from ordinary human memories.
"""
import copy
from datetime import datetime,timezone,timedelta
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import sqlite3


def canon(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def sha(value):return hashlib.sha256(canon(value).encode()).hexdigest()

def aware(value,offset=None):
    d=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if d.tzinfo is None:
        if type(offset) is not int or not -840<=offset<=840:raise ValueError('OB source timezone declaration required')
        d=d.replace(tzinfo=timezone(timedelta(minutes=offset)))
    return d.astimezone(timezone.utc).isoformat(timespec='milliseconds')


class AffectTools:
    def __init__(self,bucket_manager,config):self.manager,self.config=bucket_manager,copy.deepcopy(config)

    async def context(self,scope_id,as_of,cursor=0):
        if type(cursor) is not int or cursor<0:raise ValueError('Invalid cursor')
        requested=datetime.fromisoformat(aware(as_of))
        if abs((datetime.now(timezone.utc)-requested).total_seconds())>300:
            raise ValueError('Mutable OB buckets cannot provide historical snapshots; synchronize clocks')
        route=self.config.get('scopes',{}).get(scope_id)
        if not isinstance(route,dict):raise ValueError('Unbound memory scope')
        ids=route.get('bucket_ids')
        if not isinstance(ids,list) or len(ids)>128 or len(set(ids))!=len(ids):raise ValueError('Invalid scope manifest')
        records=[]
        for bid in sorted(ids):
            if not isinstance(bid,str) or not bid or len(bid)>120:raise ValueError('Invalid bucket ID')
            bucket=await self.manager.get(bid)
            if bucket is None:continue
            meta=bucket.get('metadata',{})
            # No private-letter/plan/self disclosure through a generic route.
            if meta.get('type','dynamic') not in ('dynamic','permanent'):continue
            if meta.get('deleted_at') or meta.get('tombstone'):continue
            if meta.get('source_tool')=='event_affect_bridge' or bid.startswith('affect_'):continue
            text=bucket.get('content')
            if not isinstance(text,str) or not text or len(text)>8000:raise ValueError('OB source paragraph limit')
            created=aware(meta.get('created'),route.get('source_utc_offset_minutes'))
            if datetime.fromisoformat(created)>requested:continue
            # Existing buckets may be edited without a content revision time.
            # created != when the current text became known. Only attest to the
            # current read, never backdate edited content into an earlier query.
            known=aware(as_of)
            records.append({'id':bid,'scope_id':scope_id,'text':text,'kind':'summary',
                'origin_system':'ob_native','known_at':known,'valid_from':None,'valid_to':None,
                'source_refs':['ob_bucket:'+bid],'lineage':[],'relevance':1.0,
                'temporal_basis':'retrieval_time_mutable_source',
                'original_timestamps':{'created':str(meta.get('created')),
                    'source_utc_offset_minutes':route.get('source_utc_offset_minutes')}})
        version=sha(records)
        if cursor>len(records):raise ValueError('Cursor beyond snapshot')
        # Do not truncate a source paragraph or silently treat partial pages as
        # a complete semantic result. The bridge must consume all pages.
        page=[];size=0;next_cursor=cursor
        for record in records[cursor:]:
            mass=len(canon(record).encode())
            if page and (len(page)>=16 or size+mass>110000):break
            page.append(record);size+=mass;next_cursor+=1
        return {'items':page,'scope_version':version,'total':len(records),
                'next_cursor':next_cursor if next_cursor<len(records) else None,
                'source_timezone_assumption_minutes':route.get('source_utc_offset_minutes'),
                'memory_mutated':False,'semantic_ranking_performed':False}

    def _connect(self):
        path=Path(self.config['archive_db']);path.parent.mkdir(parents=True,exist_ok=True)
        c=sqlite3.connect(path,timeout=10)
        c.execute('CREATE TABLE IF NOT EXISTS conclusions(archive_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,body TEXT NOT NULL)')
        return c

    def validate(self,item):
        required={'archive_id','agent_id','actor_id','source_event_id','source_seq','occurred_at','known_at','conclusion','lineage','origin_system'}
        if not isinstance(item,dict) or set(item)!=required or item['origin_system']!='event_affect':raise ValueError('Invalid derived archive')
        for key in ('archive_id','agent_id','source_event_id'):
            if not isinstance(item[key],str) or not 0<len(item[key])<=240:raise ValueError('Invalid archive identity')
        allowed=self.config.get('archive_agents',[])
        if item['agent_id'] not in allowed:raise ValueError('Unbound archive agent')
        if type(item['source_seq']) is not int or item['source_seq']<1:raise ValueError('Invalid event sequence')
        if item['lineage']!=['event_affect:'+item['agent_id']+':'+item['source_event_id']]:raise ValueError('Invalid lineage')
        aware(item['occurred_at']);aware(item['known_at'])
        c=item['conclusion']
        if not isinstance(c,dict) or set(c)!={'trust_delta','expectation_status','classifier_status','interpretation'}:raise ValueError('Invalid conclusion fields')
        if c['interpretation']!='agent_assessment_not_observed_world_fact':raise ValueError('Not an agent interpretation')
        if isinstance(c['trust_delta'],bool) or not isinstance(c['trust_delta'],(int,float)) or not math.isfinite(c['trust_delta']) or not -1<=c['trust_delta']<=1:raise ValueError('Invalid delta')
        if c['expectation_status'] not in (None,'fulfilled','unmet','withdrawn','expired_unknown'):raise ValueError('Invalid expectation')
        if not isinstance(c['classifier_status'],str) or not 0<len(c['classifier_status'])<=120:raise ValueError('Invalid status')
        if len(canon(item).encode())>8000:raise ValueError('Archive limit')

    async def archive(self,item,signature):
        if self.config.get('archive_enabled') is not True:raise ValueError('OB affect archive disabled')
        self.validate(item);fp=sha(item)
        key=Path(self.config['archive_signing_key_file']).read_bytes().strip()
        if len(key)<32 or not isinstance(signature,str) or not hmac.compare_digest(
            hmac.new(key,canon(item).encode(),hashlib.sha256).hexdigest(),signature):
            raise ValueError('Derived archive signature invalid')
        c=self._connect()
        try:
            c.execute('BEGIN IMMEDIATE')
            existing=c.execute('SELECT fingerprint FROM conclusions WHERE archive_id=?',(item['archive_id'],)).fetchone()
            if existing and existing[0]!=fp:raise ValueError('Archive ID fingerprint conflict')
            if not existing:c.execute('INSERT INTO conclusions VALUES(?,?,?)',(item['archive_id'],fp,canon(item)))
            c.commit()
        except BaseException:c.rollback();raise
        finally:c.close()
        return {'archive_id':item['archive_id'],'fingerprint':fp,'status':'stored',
                'deduplicated':existing is not None,'ordinary_buckets_written':0}

    async def lookup(self,archive_id):
        c=self._connect()
        try:r=c.execute('SELECT fingerprint FROM conclusions WHERE archive_id=?',(archive_id,)).fetchone()
        finally:c.close()
        return {'archive_id':archive_id,'status':'stored' if r else 'not_found',
                'fingerprint':r[0] if r else None,'authoritative':True,'dedupe_supported':True}


def register(mcp,bucket_manager,config):
    tools=AffectTools(bucket_manager,config)
    @mcp.tool()
    async def affect_context_v1(scope_id:str,as_of:str,cursor:int=0)->dict:
        """Read explicitly bound buckets without activation, association or ranking."""
        return await tools.context(scope_id,as_of,cursor)
    @mcp.tool()
    async def affect_archive_v1(item:dict,signature:str)->dict:
        """Idempotent derived-interpretation archive; no ordinary memory or plan write."""
        return await tools.archive(item,signature)
    @mcp.tool()
    async def affect_archive_lookup_v1(archive_id:str)->dict:
        """Read authoritative derived-archive receipt; never search ordinary memories."""
        return await tools.lookup(archive_id)
    return tools


def register_from_environment(mcp,bucket_manager):
    path=os.environ.get('OB_AFFECT_CONFIG')
    if not path:return None
    raw=Path(path).read_bytes()
    if len(raw)>131072:raise ValueError('OB affect config limit')
    config=json.loads(raw)
    if config.get('enabled') is not True:return None
    return register(mcp,bucket_manager,config)
