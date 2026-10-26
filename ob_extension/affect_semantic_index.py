"""Full-text cloud embeddings in a separate derived cache; no bucket writes."""
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request


def digest(text):
    return hashlib.sha256(text.encode('utf8')).hexdigest()


def chunks(text):
    if not isinstance(text, str) or not text.strip() or len(text)>8000:
        raise ValueError('Invalid semantic text')
    # Every character participates; never silently truncate a paragraph/query.
    return [text[i:i+1800] for i in range(0,len(text),1800)]


def normalized(vector, dimensions):
    if not isinstance(vector,list) or len(vector)!=dimensions:
        raise ValueError('Embedding dimension mismatch')
    if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in vector):
        raise ValueError('Invalid embedding vector')
    norm=math.sqrt(sum(x*x for x in vector))
    if norm<=1e-12:raise ValueError('Zero embedding vector')
    return [x/norm for x in vector]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


class ClosingConnection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()


class CloudIndex:
    def __init__(self, config, provider=None):
        self.config=dict(config);self.dimensions=config.get('dimensions',1024)
        if type(self.dimensions) is not int or not 64<=self.dimensions<=3072:raise ValueError('Invalid dimensions')
        self.endpoint=config['endpoint'];self.model=config['model'];self.path=Path(config['cache_db'])
        if not self.endpoint.startswith('https://'):raise ValueError('Cloud HTTPS required')
        self.namespace=digest(json.dumps([self.endpoint,self.model,self.dimensions]))
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock();self.provider=provider or self._embed
        with self.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS vectors(namespace TEXT NOT NULL,text_hash TEXT NOT NULL,vector TEXT NOT NULL,PRIMARY KEY(namespace,text_hash))')

    def connect(self):return sqlite3.connect(str(self.path),timeout=10,factory=ClosingConnection)

    def _embed(self,texts,timeout):
        key=os.environ.get(self.config['key_environment'],'')
        if not key:raise ValueError('Cloud embedding key missing')
        body={'model':self.model,'input':texts,'dimensions':self.dimensions,'encoding_format':'float'}
        payload=json.dumps({'endpoint':self.endpoint,'body':body,'key':key,'timeout':timeout}).encode()
        process=subprocess.Popen([sys.executable,'-B',str(Path(__file__).with_name('affect_embedding_worker.py'))],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        try:
            output,_=process.communicate(payload,timeout=timeout)
            if len(output)>1100000:raise ValueError('Embedding response limit')
            response=json.loads(output)
            if response.get('ok') is not True:raise ValueError('Embedding provider unavailable')
            rows=response.get('data')
        finally:
            if process.poll() is None:process.kill();process.communicate(timeout=3)
        if not isinstance(rows,list) or len(rows)!=len(texts):raise ValueError('Embedding count mismatch')
        by_index={r.get('index'):r.get('embedding') for r in rows if isinstance(r,dict)}
        if set(by_index)!=set(range(len(texts))):raise ValueError('Embedding index mismatch')
        return [normalized(by_index[i],self.dimensions) for i in range(len(texts))]

    def legacy_vector(self,text):
        source=self.config.get('legacy_cache_db')
        if not source or len(text)>2000:return None
        c=sqlite3.connect('file:'+str(source)+'?mode=ro',uri=True,timeout=3)
        try:
            meta=dict(c.execute('SELECT key,value FROM embeddings_meta'))
            if meta.get('model_name')!=self.model:return None
            row=c.execute('SELECT embedding FROM embeddings WHERE content_hash=? LIMIT 1',(digest(text),)).fetchone()
            if row is None:return None
            # Actual cloud output dimension is authoritative; old OB metadata may
            # retain its former default 3072 despite storing 1024-dimensional data.
            return normalized(json.loads(row[0]),self.dimensions)
        finally:c.close()

    def vectors(self,texts,deadline):
        unique=list(dict.fromkeys(texts));found={}
        if not self.lock.acquire(timeout=max(0,deadline-time.monotonic())):
            raise TimeoutError('Semantic index busy')
        try:
            with self.connect() as c:
                for text in unique:
                    row=c.execute('SELECT vector FROM vectors WHERE namespace=? AND text_hash=?',
                                  (self.namespace,digest(text))).fetchone()
                    vector=normalized(json.loads(row[0]),self.dimensions) if row else self.legacy_vector(text)
                    if vector is not None:
                        found[text]=vector
                        if row is None:c.execute('INSERT INTO vectors VALUES(?,?,?)',
                            (self.namespace,digest(text),json.dumps(vector)))
            missing=[text for text in unique if text not in found]
            for start in range(0,len(missing),10):
                remaining=deadline-time.monotonic()
                if remaining<.1:raise TimeoutError('Semantic embedding deadline')
                batch=missing[start:start+10]
                result=self.provider(batch,min(remaining,20))
                if not isinstance(result,list) or len(result)!=len(batch):raise ValueError('Embedding batch mismatch')
                values=[normalized(v,self.dimensions) for v in result]
                with self.connect() as c:
                    for text,value in zip(batch,values):
                        c.execute('INSERT OR REPLACE INTO vectors VALUES(?,?,?)',
                                  (self.namespace,digest(text),json.dumps(value)))
                        found[text]=value
        finally:self.lock.release()
        return [found[text] for text in texts]

    def prepare(self,records,timeout=120):
        texts=[part for r in records for part in chunks(r['text'])]
        self.vectors(texts,time.monotonic()+timeout)
        return {'indexed_records':len(records),'indexed_segments':len(texts),'ordinary_buckets_written':0}

    def search(self,records,query,limit,timeout=3):
        deadline=time.monotonic()+timeout
        query_parts=chunks(query)
        vectors=self.vectors(query_parts,deadline)
        query_vector=normalized([sum(v[i]*len(t) for v,t in zip(vectors,query_parts))
                                 for i in range(self.dimensions)],self.dimensions)
        segments=[part for r in records for part in chunks(r['text'])]
        source_vectors=self.vectors(segments,deadline)
        ranked=[];offset=0
        for record in records:
            count=len(chunks(record['text']))
            score=max(sum(a*b for a,b in zip(query_vector,v)) for v in source_vectors[offset:offset+count])
            offset+=count
            ranked.append({**record,'relevance':round(max(0,min(1,(score+1)/2)),8)})
        ranked.sort(key=lambda r:(-r['relevance'],r['id']))
        if time.monotonic()>deadline:raise TimeoutError('Semantic search deadline')
        return ranked[:limit]
