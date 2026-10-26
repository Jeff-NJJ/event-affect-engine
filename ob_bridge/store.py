"""Separate archive outbox: durable cursors and uncertain writes, not chat delivery."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from endocrine.model import DomainError,canonical,digest,iso
from .contracts import archive_item


class ClosingConnection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()


class ArchiveStore:
    def __init__(self,path):
        self.path=str(path)
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as c:
            c.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS jobs(archive_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,
                body TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',claim_token TEXT,receipt TEXT,
                quota_day TEXT,created_at REAL NOT NULL,updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS cursors(agent_id TEXT PRIMARY KEY,seq INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            ''')

    def connect(self):
        c=sqlite3.connect(self.path,timeout=10,isolation_level=None,factory=ClosingConnection);c.row_factory=sqlite3.Row
        return c

    @contextmanager
    def transaction(self):
        c=self.connect()
        try:
            c.execute('BEGIN IMMEDIATE');yield c;c.commit()
        except BaseException:c.rollback();raise
        finally:c.close()

    def _queue(self,c,item,now):
        item=archive_item(item);fp=digest(item)
        row=c.execute('SELECT * FROM jobs WHERE archive_id=?',(item['archive_id'],)).fetchone()
        if row:
            if row['fingerprint']!=fp:raise DomainError('archive_id_content_conflict',409)
            return dict(row)
        c.execute('INSERT INTO jobs(archive_id,fingerprint,body,created_at,updated_at) VALUES(?,?,?,?,?)',
            (item['archive_id'],fp,canonical(item),now,now))
        return dict(c.execute('SELECT * FROM jobs WHERE archive_id=?',(item['archive_id'],)).fetchone())

    def queue(self,item,now):
        with self.transaction() as c:return self._queue(c,item,now)

    def cursor(self,agent):
        with self.connect() as c:row=c.execute('SELECT seq FROM cursors WHERE agent_id=?',(agent,)).fetchone()
        if row is None:raise DomainError('archive_cursor_not_initialized',409)
        return row[0]

    def initialize_cursor(self,agent,seq):
        if type(seq) is not int or seq<0:raise DomainError('invalid_archive_cursor')
        with self.transaction() as c:
            if c.execute('SELECT 1 FROM cursors WHERE agent_id=?',(agent,)).fetchone():raise DomainError('archive_cursor_already_initialized',409)
            c.execute('INSERT INTO cursors VALUES(?,?)',(agent,seq))

    def ingest(self,agent,expected_cursor,next_cursor,items,now):
        if type(next_cursor) is not int or next_cursor<expected_cursor:raise DomainError('archive_cursor_regression',409)
        with self.transaction() as c:
            current=c.execute('SELECT seq FROM cursors WHERE agent_id=?',(agent,)).fetchone()
            if current is None or current[0]!=expected_cursor:raise DomainError('archive_cursor_changed',409)
            for item in items:
                if item['agent_id']!=agent or not expected_cursor<item['source_seq']<=next_cursor:
                    raise DomainError('archive_feed_scope_mismatch',409)
                self._queue(c,item,now)
            c.execute('UPDATE cursors SET seq=? WHERE agent_id=?',(next_cursor,agent))

    def claim(self,now,cap=10):
        if type(cap) is not int or not 1<=cap<=10:raise DomainError('invalid_archive_daily_cap')
        with self.transaction() as c:
            day=iso(now)[:10]
            old=c.execute("SELECT value FROM metadata WHERE key='quota_day'").fetchone()
            # Clock rollback must not open an extra day's sending allowance.
            if old:day=max(day,old[0])
            c.execute("INSERT INTO metadata VALUES('quota_day',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(day,))
            if c.execute('SELECT COUNT(*) FROM jobs WHERE quota_day=?',(day,)).fetchone()[0]>=cap:return None
            row=c.execute("SELECT * FROM jobs WHERE status='pending' ORDER BY created_at,archive_id LIMIT 1").fetchone()
            if row is None:return None
            claim=uuid.uuid4().hex
            c.execute("UPDATE jobs SET status='sending',claim_token=?,quota_day=?,updated_at=? WHERE archive_id=? AND status='pending'",
                (claim,day,now,row['archive_id']))
            return {**dict(row),'status':'sending','claim_token':claim}

    def settle(self,job,status,receipt,now):
        if status not in ('delivered','unknown'):raise DomainError('invalid_archive_status')
        with self.transaction() as c:
            c.execute("UPDATE jobs SET status=?,receipt=?,claim_token=NULL,updated_at=? WHERE archive_id=? AND status='sending' AND claim_token=?",
                (status,canonical(receipt),now,job['archive_id'],job['claim_token']))

    def recover_interrupted(self,now):
        with self.transaction() as c:
            return c.execute("UPDATE jobs SET status='unknown',claim_token=NULL,updated_at=? WHERE status='sending'",(now,)).rowcount

    def rows(self):
        with self.connect() as c:return [dict(r) for r in c.execute('SELECT * FROM jobs ORDER BY created_at,archive_id')]

    def reconcile(self,archive_id,fingerprint,status,receipt,now):
        if status not in ('delivered','pending'):raise DomainError('invalid_reconciliation_status')
        with self.transaction() as c:
            return c.execute("UPDATE jobs SET status=?,receipt=?,updated_at=? WHERE archive_id=? AND fingerprint=? AND status='unknown'",
                (status,canonical(receipt),now,archive_id,fingerprint)).rowcount==1
