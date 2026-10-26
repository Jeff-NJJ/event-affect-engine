"""SQLite owns event order, leases and committed state. No in-memory truth."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .core import apply, initial_state, snapshot, validate_state
from .clock import LeaseClock, lease_expired, lease_stamp
from .model import Assessment, DomainError, Event, canonical, digest, iso, timestamp, identity

SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(
 seq INTEGER PRIMARY KEY AUTOINCREMENT,agent_id TEXT NOT NULL,event_id TEXT NOT NULL,
 fingerprint TEXT NOT NULL,body TEXT NOT NULL,received_at REAL NOT NULL,effective_at REAL NOT NULL DEFAULT 0,received_stamp TEXT,
 status TEXT NOT NULL DEFAULT 'pending',lease_token TEXT,lease_until REAL,lease_stamp TEXT,
 assessment TEXT,response TEXT,error_code TEXT,
 UNIQUE(agent_id,event_id));
CREATE INDEX IF NOT EXISTS event_queue ON events(agent_id,status,seq);
CREATE TABLE IF NOT EXISTS agent_states(agent_id TEXT PRIMARY KEY,state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS initial_states(agent_id TEXT PRIMARY KEY,state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS legacy_archives(agent_id TEXT PRIMARY KEY,graph TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS state_changes(
 agent_id TEXT NOT NULL,event_id TEXT NOT NULL,revision INTEGER NOT NULL,trace TEXT NOT NULL,
 PRIMARY KEY(agent_id,event_id));
CREATE TABLE IF NOT EXISTS relationships(
 agent_id TEXT NOT NULL,person_id TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(agent_id,person_id));
CREATE TABLE IF NOT EXISTS expectations(
 agent_id TEXT NOT NULL,expect_id TEXT NOT NULL,status TEXT NOT NULL,data TEXT NOT NULL,
 PRIMARY KEY(agent_id,expect_id));
CREATE TABLE IF NOT EXISTS outbox(
 operation_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,payload TEXT NOT NULL,
 status TEXT NOT NULL,claim_token TEXT,updated_at REAL NOT NULL,receipt TEXT,lease_stamp TEXT);
CREATE TABLE IF NOT EXISTS delivery_log(
 id INTEGER PRIMARY KEY AUTOINCREMENT,operation_id TEXT NOT NULL,status TEXT NOT NULL,
 occurred_at REAL NOT NULL,evidence TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS classification_jobs(
 agent_id TEXT NOT NULL,event_id TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 1,
 next_wall REAL NOT NULL,scheduled_stamp TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',
 claim_token TEXT,claim_stamp TEXT,last_error TEXT,PRIMARY KEY(agent_id,event_id));
CREATE TABLE IF NOT EXISTS assessment_history(
 id INTEGER PRIMARY KEY AUTOINCREMENT,agent_id TEXT NOT NULL,event_id TEXT NOT NULL,
 assessment TEXT NOT NULL,context TEXT NOT NULL,recorded_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS state_history(
 agent_id TEXT NOT NULL,generation INTEGER NOT NULL,event_id TEXT NOT NULL,trace TEXT NOT NULL,
 PRIMARY KEY(agent_id,generation,event_id));
CREATE TABLE IF NOT EXISTS memory_entries(
 agent_id TEXT NOT NULL,event_id TEXT NOT NULL,origin TEXT NOT NULL,actor_id TEXT,conversation_id TEXT,
 occurred_at REAL,text TEXT NOT NULL,assessment TEXT,salience REAL NOT NULL,
 provenance TEXT NOT NULL,original_ref TEXT NOT NULL,text_hash TEXT NOT NULL,PRIMARY KEY(agent_id,event_id));
CREATE TABLE IF NOT EXISTS semantic_documents(
 agent_id TEXT NOT NULL,event_id TEXT NOT NULL,encoder_signature TEXT NOT NULL,text_hash TEXT NOT NULL,
 dimensions INTEGER NOT NULL,metadata TEXT NOT NULL,PRIMARY KEY(agent_id,event_id,encoder_signature));
CREATE TABLE IF NOT EXISTS semantic_vectors(
 agent_id TEXT NOT NULL,event_id TEXT NOT NULL,encoder_signature TEXT NOT NULL,chunk INTEGER NOT NULL,
 vector BLOB NOT NULL,PRIMARY KEY(agent_id,event_id,encoder_signature,chunk));
CREATE TABLE IF NOT EXISTS delivery_schedule(
 operation_id TEXT PRIMARY KEY,next_wall REAL NOT NULL,stamp TEXT NOT NULL,failures INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS agent_service_turns(agent_id TEXT PRIMARY KEY,last_turn INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS memory_person ON memory_entries(agent_id,actor_id,occurred_at,event_id);
CREATE INDEX IF NOT EXISTS memory_conversation ON memory_entries(agent_id,conversation_id,occurred_at,event_id);
"""


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Repository:
    def __init__(self, path, config, *, readonly=False, create=True, lease_clock=None):
        self.path, self.config = str(path), config
        self.lease_clock = lease_clock or LeaseClock()
        self.readonly = readonly
        location = Path(path)
        if location.exists():
            # Existing sources must be validated without DDL or journal changes.
            self._validate_existing()
            return
        if readonly or not create:
            raise DomainError("database_source_missing", 503)
        location.parent.mkdir(parents=True, exist_ok=True)
        try:
            with location.open("xb"):
                pass
        except FileExistsError:
            raise DomainError("database_creation_in_progress_retry", 503) from None
        with self.connect() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            c.execute("INSERT INTO metadata VALUES('schema_version','3')")

    def _validate_existing(self):
        try:
            with self.connect(readonly=True) as c:
                tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                required = {"metadata", "events", "agent_states", "initial_states", "legacy_archives",
                            "state_changes", "relationships", "expectations", "outbox", "delivery_log",
                            "classification_jobs", "assessment_history", "state_history", "memory_entries", "semantic_documents", "semantic_vectors", "delivery_schedule", "agent_service_turns"}
                if not required <= tables:
                    raise DomainError("not_an_affect_database", 503)
                found = c.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
                if not found or found[0] != "3":
                    raise DomainError("database_schema_mismatch", 503)
                migration = c.execute("SELECT value FROM metadata WHERE key='migration_status'").fetchone()
                if migration and migration[0] != "complete":
                    raise DomainError("migration_incomplete_review_required", 503)
                if c.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise DomainError("database_corrupt_restore_required", 503)
        except sqlite3.Error:
            raise DomainError("invalid_database_source", 503) from None

    def connect(self, *, readonly=None):
        ro = self.readonly if readonly is None else readonly
        uri = Path(self.path).resolve().as_uri() + ("?mode=ro" if ro else "?mode=rw")
        c = sqlite3.connect(uri, uri=True, timeout=15, isolation_level=None, factory=ClosingConnection)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=15000")
        if ro:
            c.execute("PRAGMA query_only=ON")
        else:
            c.execute("PRAGMA synchronous=FULL")
        return c

    @contextmanager
    def transaction(self):
        if self.readonly:
            raise DomainError("read_only_repository", 503)
        c = self.connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def reserve(self, event, received, server_defaults=None):
        body = canonical(event.to_dict())
        with self.transaction() as c:
            row = c.execute("SELECT * FROM events WHERE agent_id=? AND event_id=?", (event.agent_id, event.event_id)).fetchone()
            if row:
                incoming = event.to_dict()
                # Legacy clients omit time. Resolve defaults under the same lock
                # as uniqueness, including when both adapters observed no row.
                if server_defaults and server_defaults.get("occurred_at"):
                    incoming["occurred_at"] = json.loads(row["body"])["occurred_at"]
                    if "deadline_ttl_h" in server_defaults:
                        incoming["payload"]["deadline"] = iso(
                            timestamp(incoming["occurred_at"]) + server_defaults["deadline_ttl_h"] * 3600)
                if row["fingerprint"] != digest(incoming):
                    raise DomainError("event_id_content_conflict", 409)
                return dict(row), True
            stamp={"wall":received,"mono":self.lease_clock.monotonic()}
            previous=c.execute("SELECT received_at,effective_at,received_stamp FROM events WHERE agent_id=? ORDER BY seq DESC LIMIT 1",(event.agent_id,)).fetchone()
            effective=received
            if previous:
                old=json.loads(previous["received_stamp"]) if previous["received_stamp"] else None
                elapsed=max(0,received-previous["received_at"])
                if old and stamp["mono"]>=old["mono"]:elapsed=max(elapsed,stamp["mono"]-old["mono"])
                effective=(previous["effective_at"] or previous["received_at"])+elapsed
            c.execute("INSERT INTO events(agent_id,event_id,fingerprint,body,received_at,effective_at,received_stamp) VALUES(?,?,?,?,?,?,?)",
                      (event.agent_id,event.event_id,digest(event.to_dict()),body,received,effective,canonical(stamp)))
            return dict(c.execute("SELECT * FROM events WHERE agent_id=? AND event_id=?", (event.agent_id, event.event_id)).fetchone()), False

    def get_event(self, agent_id, event_id):
        with self.connect() as c:
            row = c.execute("SELECT * FROM events WHERE agent_id=? AND event_id=?", (agent_id, event_id)).fetchone()
        return dict(row) if row else None

    def claim_next(self, agent_id):
        with self.transaction() as c:
            row = c.execute("SELECT * FROM events WHERE agent_id=? AND status IN ('pending','processing') ORDER BY seq LIMIT 1", (agent_id,)).fetchone()
            if not row:
                return None
            if (row["status"] == "processing" and row["lease_until"] > self.lease_clock.wall()
                    and not lease_expired(json.loads(row["lease_stamp"]) if row["lease_stamp"] else None,self.lease_clock)):
                return None
            token = str(uuid.uuid4())
            c.execute("UPDATE events SET status='processing',lease_token=?,lease_until=?,lease_stamp=? WHERE seq=?",
                      (token,self.lease_clock.wall()+120,canonical(lease_stamp(self.lease_clock)),row["seq"]))
            result = dict(row)
            result["lease_token"] = token
            return result

    def save_assessment(self, row, assessment, context=None):
        with self.transaction() as c:
            changed=c.execute("UPDATE events SET assessment=? WHERE seq=? AND lease_token=? AND status='processing'",
                              (canonical(assessment.to_dict()),row["seq"],row["lease_token"])).rowcount==1
            if changed:
                c.execute("INSERT INTO assessment_history(agent_id,event_id,assessment,context,recorded_at) VALUES(?,?,?,?,?)",
                          (row["agent_id"],row["event_id"],canonical(assessment.to_dict()),canonical(context or []),self.lease_clock.wall()))
            return changed

    def saved_assessment_context(self,row):
        with self.connect() as c:
            saved=c.execute('SELECT context FROM assessment_history WHERE agent_id=? AND event_id=? AND assessment=? ORDER BY id DESC LIMIT 1',
                (row['agent_id'],row['event_id'],row['assessment'])).fetchone()
        if not saved:return self.classification_context(row)
        records=json.loads(saved['context'])
        return self.classification_context(row,event_ids=[item['event_id'] for item in records])

    def claim_next_any(self):
        """Durable round-robin across agents; FIFO and fencing within each agent."""
        with self.transaction() as c:
            heads=c.execute("SELECT e.* FROM events e JOIN (SELECT agent_id,MIN(seq) seq FROM events WHERE status IN ('pending','processing') GROUP BY agent_id) h ON e.seq=h.seq LEFT JOIN agent_service_turns t ON e.agent_id=t.agent_id ORDER BY COALESCE(t.last_turn,0),e.seq").fetchall()
            for row in heads:
                if (row['status']=='processing' and row['lease_until'] is not None and row['lease_until']>self.lease_clock.wall()
                    and not lease_expired(json.loads(row['lease_stamp']) if row['lease_stamp'] else None,self.lease_clock)):continue
                token=str(uuid.uuid4())
                c.execute("UPDATE events SET status='processing',lease_token=?,lease_until=?,lease_stamp=? WHERE seq=?",
                    (token,self.lease_clock.wall()+120,canonical(lease_stamp(self.lease_clock)),row['seq']))
                turn=c.execute('SELECT COALESCE(MAX(last_turn),0)+1 FROM agent_service_turns').fetchone()[0]
                c.execute('INSERT INTO agent_service_turns VALUES(?,?) ON CONFLICT(agent_id) DO UPDATE SET last_turn=excluded.last_turn',(row['agent_id'],turn))
                result=dict(row);result['lease_token']=token;return result
        return None

    def _state(self, c, agent_id, now):
        row = c.execute("SELECT state FROM agent_states WHERE agent_id=?", (agent_id,)).fetchone()
        if not row:
            if (c.execute("SELECT 1 FROM events WHERE agent_id=? AND status='applied' LIMIT 1", (agent_id,)).fetchone()
                    or any(c.execute("SELECT 1 FROM " + table + " WHERE agent_id=? LIMIT 1", (agent_id,)).fetchone()
                           for table in ("state_changes", "initial_states", "legacy_archives", "relationships", "expectations"))):
                raise DomainError("state_missing_restore_required", 503)
            return initial_state(agent_id, now, self.config)
        try:
            state = json.loads(row[0])
            validate_state(state, self.config)
            return state
        except DomainError as exc:
            if exc.status >= 500:
                raise
            raise DomainError("state_corrupt_restore_required", 503) from None
        except (ValueError, TypeError, KeyError, AttributeError):
            raise DomainError("state_corrupt_restore_required", 503) from None

    def load_state(self, agent_id, now):
        with self.connect() as c:
            c.execute("BEGIN")
            return self._state(c, agent_id, now)

    def reject_claim(self, row, error):
        response = {"success": False, "error": error.code, "http_status": error.status,
                    "event_id": row["event_id"], "agent_id": row["agent_id"]}
        with self.transaction() as c:
            return c.execute("UPDATE events SET status='rejected',response=?,error_code=?,lease_token=NULL,lease_until=NULL "
                             "WHERE seq=? AND lease_token=? AND status='processing'",
                             (canonical(response), error.code, row["seq"], row["lease_token"])).rowcount == 1

    def finish(self, row, assessment, before_commit=None):
        with self.transaction() as c:
            live = c.execute("SELECT * FROM events WHERE seq=?", (row["seq"],)).fetchone()
            if live["status"] != "processing" or live["lease_token"] != row["lease_token"]:
                return None
            e = Event.parse(json.loads(row["body"]))
            state = self._state(c, e.agent_id, row["received_at"])
            try:
                after, trace = apply(state,e,assessment,row.get("effective_at") or row["received_at"],row["seq"],self.config,wall_at=row["received_at"])
            except DomainError as exc:
                # Rejected commands are durable too; retries have the same error.
                if exc.status >= 500:
                    raise
                response = {"success": False, "error": exc.code, "http_status": exc.status, "event_id": e.event_id, "agent_id": e.agent_id}
                c.execute("UPDATE events SET status='rejected',response=?,error_code=?,lease_token=NULL,lease_until=NULL WHERE seq=?",
                          (canonical(response), exc.code, row["seq"]))
                return response
            view = snapshot(after, after["state_time"], self.config)
            response = {"success": True, "event_id": e.event_id, "revision": after["revision"],
                        "classify_source": assessment.classifier_status, "stimulus": assessment.kind,
                        "all_stimuli": [f.kind for f in assessment.facts] or [assessment.kind], "snapshot": view, **view}
            c.execute("INSERT INTO agent_states VALUES(?,?) ON CONFLICT(agent_id) DO UPDATE SET state=excluded.state",
                      (e.agent_id, canonical(after)))
            c.execute("INSERT OR IGNORE INTO initial_states VALUES(?,?)",(e.agent_id,canonical(state)))
            c.execute("INSERT INTO state_changes VALUES(?,?,?,?)", (e.agent_id, e.event_id, after["revision"], canonical(trace)))
            for person, relation in after["relationships"].items():
                c.execute("INSERT INTO relationships VALUES(?,?,?) ON CONFLICT(agent_id,person_id) DO UPDATE SET data=excluded.data", (e.agent_id, person, canonical(relation)))
            for eid, expectation in after["expectations"].items():
                c.execute("INSERT INTO expectations VALUES(?,?,?,?) ON CONFLICT(agent_id,expect_id) DO UPDATE SET status=excluded.status,data=excluded.data", (e.agent_id, eid, expectation["status"], canonical(expectation)))
            c.execute("UPDATE events SET status='applied',response=?,lease_token=NULL,lease_until=NULL WHERE seq=?", (canonical(response), row["seq"]))
            c.execute("INSERT INTO state_history VALUES(?,?,?,?)",(e.agent_id,after.get("generation",0),e.event_id,canonical(trace)))
            self._index_event(c,e,assessment)
            if assessment.classifier_status in RETRY_CLASSIFICATION:
                c.execute("INSERT OR IGNORE INTO classification_jobs(agent_id,event_id,next_wall,scheduled_stamp,last_error) VALUES(?,?,?,?,?)",
                          (e.agent_id,e.event_id,self.lease_clock.wall()+30,canonical(lease_stamp(self.lease_clock)),assessment.classifier_status))
                response["classification_pending"]=True
                c.execute("UPDATE events SET response=? WHERE seq=?",(canonical(response),row["seq"]))
            if e.event_type=="semantic_resolution":
                original_id=e.payload["original_event_id"]
                original=c.execute("SELECT response FROM events WHERE agent_id=? AND event_id=?",(e.agent_id,original_id)).fetchone()
                if original and original[0]:
                    old_response=json.loads(original[0])
                    corrected={**response,"event_id":original_id,"revision":old_response["revision"],
                               "classification_pending":False,"resolution_event_id":e.event_id}
                    c.execute("UPDATE events SET response=? WHERE agent_id=? AND event_id=?",(canonical(corrected),e.agent_id,original_id))
                c.execute("UPDATE classification_jobs SET status='completed',claim_token=NULL,claim_stamp=NULL WHERE agent_id=? AND event_id=?",(e.agent_id,original_id))
                indexed=c.execute("SELECT body FROM events WHERE agent_id=? AND event_id=?",(e.agent_id,original_id)).fetchone()
                if indexed:self._index_event(c,Event.parse(json.loads(indexed[0])),assessment)
            if before_commit:
                before_commit(c)
            return response

    def export(self):
        with self.connect() as c:
            c.execute("BEGIN")
            result = {"schema_version": 3, "config_signature": self.config.signature,
                    "states": [json.loads(r[0]) for r in c.execute("SELECT state FROM agent_states ORDER BY agent_id")],
                    "initial_states": [json.loads(r[0]) for r in c.execute("SELECT state FROM initial_states ORDER BY agent_id")],
                    "legacy_archives": [dict(r) for r in c.execute("SELECT * FROM legacy_archives ORDER BY agent_id")],
                    "events": [dict(r) for r in c.execute("SELECT * FROM events ORDER BY seq")],
                    "state_changes": [dict(r) for r in c.execute("SELECT * FROM state_changes ORDER BY revision")],
                    "outbox": [dict(r) for r in c.execute("SELECT * FROM outbox ORDER BY operation_id")],
                    **{name:[dict(r) for r in c.execute("SELECT * FROM "+name)] for name in
                       ("memory_entries","classification_jobs","assessment_history","state_history","delivery_log","delivery_schedule","agent_service_turns")}}
            have = {s["agent_id"] for s in result["states"]}
            required = ({r["agent_id"] for r in result["events"] if r["status"] == "applied"}
                        | {r["agent_id"] for r in result["initial_states"]}
                        | {r["agent_id"] for r in result["state_changes"]}
                        | {r["agent_id"] for r in result["legacy_archives"]})
            if not required <= have:
                raise DomainError("state_missing_restore_required", 503)
            # A raw archive of an older configuration keeps its actual signature.
            signatures = {s["config_signature"] for s in result["states"]}
            if len(signatures) > 1:
                raise DomainError("mixed_state_configurations", 503)
            if signatures:
                result["config_signature"] = signatures.pop()
            return result

    def import_state(self, state, legacy_graph=None):
        validate_state(state, self.config)
        with self.transaction() as c:
            if c.execute("SELECT 1 FROM agent_states WHERE agent_id=?", (state["agent_id"],)).fetchone():
                raise DomainError("import_target_not_empty", 409)
            c.execute("INSERT INTO agent_states VALUES(?,?)", (state["agent_id"], canonical(state)))
            c.execute("INSERT INTO initial_states VALUES(?,?)", (state["agent_id"], canonical(state)))
            if legacy_graph is not None:
                c.execute("INSERT INTO legacy_archives VALUES(?,?)", (state["agent_id"], canonical(legacy_graph)))
                from .memory import legacy_memory_records
                for memory in legacy_memory_records(legacy_graph,state):
                    self._insert_memory(c,memory)

    def recent_memories(self, agent_id, actor_id=None, limit=5):
        return self.search_memories(agent_id,actor_id=actor_id,limit=limit)["items"]

    def _insert_memory(self,c,m):
        existing=c.execute('SELECT text_hash FROM memory_entries WHERE agent_id=? AND event_id=?',(m['agent_id'],m['event_id'])).fetchone()
        if existing and existing['text_hash']!=digest(m['text']):raise DomainError('memory_event_id_content_conflict',503)
        c.execute("INSERT INTO memory_entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(agent_id,event_id) DO UPDATE SET assessment=excluded.assessment,salience=excluded.salience",
                  tuple(m[k] for k in ("agent_id","event_id","origin","actor_id","conversation_id","occurred_at","text","assessment","salience","provenance","original_ref"))+(digest(m["text"]),))

    def _index_event(self,c,event,assessment):
        if event.source not in {"user_message","external_event","agent_message"} or event.event_type not in {"utterance","external_experience"}:
            return
        self._insert_memory(c,{"agent_id":event.agent_id,"event_id":event.event_id,"origin":"v5_event",
            "actor_id":event.actor_id,"conversation_id":event.conversation_id,"occurred_at":timestamp(event.occurred_at),
            "text":event.text,"assessment":canonical(assessment.to_dict()),"salience":assessment.relevance*assessment.intensity,
            "provenance":event.provenance,"original_ref":"events:"+event.event_id})

    def find_memory(self,agent_id,event_id):
        with self.connect() as c:
            row=c.execute("SELECT * FROM memory_entries WHERE agent_id=? AND event_id=?",(agent_id,event_id)).fetchone()
            return dict(row) if row else None

    def search_memories(self,agent_id,actor_id=None,conversation_id=None,query="",limit=20,cursor=None,important=False):
        if type(limit) is not int or not 1<=limit<=100 or not isinstance(query,str) or len(query)>240:
            raise DomainError("invalid_memory_query")
        where=["agent_id=?"];params=[agent_id]
        for field,value in (("actor_id",actor_id),("conversation_id",conversation_id)):
            if value is not None:
                where.append(field+"=?");params.append(value)
        if query:
            raise DomainError("semantic_query_requires_service")
        order="salience DESC,COALESCE(occurred_at,-1) DESC,event_id DESC" if important else "COALESCE(occurred_at,-1) DESC,event_id DESC"
        with self.connect() as c:
            c.execute("BEGIN")
            if cursor:
                row=c.execute("SELECT * FROM memory_entries WHERE agent_id=? AND event_id=?",(agent_id,cursor)).fetchone()
                if not row:
                    raise DomainError("invalid_memory_cursor")
                if important:
                    where.append("(salience,COALESCE(occurred_at,-1),event_id)<(?,?,?)");params.extend([row["salience"],row["occurred_at"] if row["occurred_at"] is not None else -1,cursor])
                else:
                    where.append("(COALESCE(occurred_at,-1),event_id)<(?,?)");params.extend([row["occurred_at"] if row["occurred_at"] is not None else -1,cursor])
            rows=c.execute("SELECT * FROM memory_entries WHERE "+" AND ".join(where)+" ORDER BY "+order+" LIMIT ?",params+[limit+1]).fetchall()
        items=[dict(r) for r in rows[:limit]]
        for m in items:
            m["assessment"]=json.loads(m["assessment"]) if m["assessment"] else None
            m["occurred_at"]=iso(m["occurred_at"]) if m["occurred_at"] is not None else None
        return {"items":items,"next_cursor":items[-1]["event_id"] if len(rows)>limit else None}

    def backup(self, destination):
        if Path(destination).resolve() == Path(self.path).resolve() or Path(destination).exists():
            raise DomainError("backup_destination_must_be_new")
        with Path(destination).open("xb"):
            pass
        with self.connect(readonly=True) as c, sqlite3.connect(str(destination), factory=ClosingConnection) as dest:
            c.backup(dest)

    def projection_time(self,agent_id,wall):
        with self.connect() as c:
            row=c.execute("SELECT received_at,effective_at,received_stamp FROM events WHERE agent_id=? AND status='applied' ORDER BY seq DESC LIMIT 1",(agent_id,)).fetchone()
        if not row:return wall
        elapsed=max(0,wall-row["received_at"])
        old=json.loads(row["received_stamp"]) if row["received_stamp"] else None
        if old and self.lease_clock.monotonic()>=old["mono"]:elapsed=max(elapsed,self.lease_clock.monotonic()-old["mono"])
        return (row["effective_at"] or row["received_at"])+elapsed

    def classification_context(self,row,limit=8,event_ids=None):
        e=Event.parse(json.loads(row["body"]))
        if not e.conversation_id:return []
        bound='';params=[e.agent_id,row['seq'],e.conversation_id]
        if event_ids is not None:
            if not event_ids:return []
            if len(event_ids)>12 or len(set(event_ids))!=len(event_ids):raise DomainError('invalid_context_ids',503)
            bound=' AND event_id IN ('+','.join('?' for _ in event_ids)+')';params.extend(event_ids);limit=len(event_ids)
        with self.connect() as c:
            rows=c.execute("SELECT seq,event_id,body,received_at FROM events WHERE agent_id=? AND seq<? AND status='applied' AND json_extract(body,'$.conversation_id')=? AND json_extract(body,'$.source') IN ('user_message','agent_message','external_event') AND json_extract(body,'$.event_type') IN ('utterance','external_experience')"+bound+" ORDER BY seq DESC LIMIT ?",params+[limit]).fetchall()
        if event_ids is not None and len(rows)!=len(event_ids):raise DomainError('context_source_missing_restore_required',503)
        result=[]
        for r in rows:
            previous=json.loads(r["body"])
            if previous.get("conversation_id")!=e.conversation_id or previous["source"] not in {"user_message","agent_message","external_event"}:continue
            if previous["event_type"] not in {"utterance","external_experience"}:continue
            result.append({"event_id":r["event_id"],"actor_id":previous.get("actor_id"),
                'occurred_at':previous['occurred_at'],'observed_at':previous.get('observed_at') or
                    (previous['occurred_at'] if previous.get('ingestion_mode')=='historical_replay' else iso(r['received_at'])),
                'source':previous['source'],'seq':r['seq'],
                "role":"assistant" if previous["source"]=="agent_message" else "user", "text":previous["text"]})
            if len(result)>=limit:break
        return list(reversed(result))

    def claim_classification(self,force=False):
        with self.transaction() as c:
            rows=c.execute("SELECT * FROM classification_jobs WHERE status IN ('pending','processing') ORDER BY next_wall,event_id").fetchall()
            for row in rows:
                if row["status"]=="processing":
                    if not lease_expired(json.loads(row["claim_stamp"]) if row["claim_stamp"] else None,self.lease_clock):
                        continue
                elif not force and not lease_expired(json.loads(row["scheduled_stamp"]),self.lease_clock,
                    max(0,row["next_wall"]-json.loads(row["scheduled_stamp"])["wall"])):
                    continue
                token=str(uuid.uuid4())
                c.execute("UPDATE classification_jobs SET status='processing',claim_token=?,claim_stamp=? WHERE agent_id=? AND event_id=?",
                          (token,canonical(lease_stamp(self.lease_clock)),row["agent_id"],row["event_id"]))
                result=dict(row);result["claim_token"]=token
                return result
        return None

    def complete_classification(self,job,assessment,context,resolved_at=None):
        with self.transaction() as c:
            live=c.execute("SELECT * FROM classification_jobs WHERE agent_id=? AND event_id=?",(job["agent_id"],job["event_id"])).fetchone()
            if not live or live["status"]!="processing" or live["claim_token"]!=job["claim_token"]:return False
            if assessment.classifier_status in RETRY_CLASSIFICATION:
                c.execute("INSERT INTO assessment_history(agent_id,event_id,assessment,context,recorded_at) VALUES(?,?,?,?,?)",
                    (job["agent_id"],job["event_id"],canonical(assessment.to_dict()),canonical(context),self.lease_clock.wall()))
                attempts=live["attempts"]+1;delay=min(3600,30*2**min(attempts-1,7))
                c.execute("UPDATE classification_jobs SET status='pending',attempts=?,next_wall=?,scheduled_stamp=?,claim_token=NULL,claim_stamp=NULL,last_error=? WHERE agent_id=? AND event_id=?",
                    (attempts,self.lease_clock.wall()+delay,canonical(lease_stamp(self.lease_clock)),assessment.classifier_status,job["agent_id"],job["event_id"]))
                return False
            original=c.execute("SELECT * FROM events WHERE agent_id=? AND event_id=?",(job["agent_id"],job["event_id"])).fetchone()
            if not original or original["status"]!="applied":raise DomainError("classification_target_not_applied",409)
            old=Event.parse(json.loads(original["body"]));when=self.lease_clock.wall() if resolved_at is None else resolved_at
            new_id="semantic-resolution:"+digest([old.agent_id,old.event_id])
            raw=old.to_dict();raw.update(event_id=new_id,source="system",provenance="system",event_type="semantic_resolution",
                occurred_at=old.occurred_at,observed_at=iso(when),ingestion_mode="live",causation_id=old.event_id,
                payload={"original_event_id":old.event_id,"original_source":old.source,"original_provenance":old.provenance,
                    "context_event_ids":[item['event_id'] for item in context]})
            new=Event.parse(raw)
            previous=c.execute("SELECT received_at,effective_at,received_stamp FROM events WHERE agent_id=? ORDER BY seq DESC LIMIT 1",(old.agent_id,)).fetchone()
            stamp={"wall":when,"mono":self.lease_clock.monotonic()};elapsed=max(0,when-previous["received_at"])
            if previous["received_stamp"]:
                before=json.loads(previous["received_stamp"])
                if stamp["mono"]>=before["mono"]:elapsed=max(elapsed,stamp["mono"]-before["mono"])
            effective=(previous["effective_at"] or previous["received_at"])+elapsed
            inserted=c.execute("INSERT OR IGNORE INTO events(agent_id,event_id,fingerprint,body,received_at,effective_at,received_stamp,assessment) VALUES(?,?,?,?,?,?,?,?)",
                (new.agent_id,new.event_id,digest(new.to_dict()),canonical(new.to_dict()),when,effective,canonical(stamp),canonical(assessment.to_dict()))).rowcount
            if not inserted:raise DomainError('internal_event_id_conflict',503)
            c.execute("UPDATE classification_jobs SET status='resolving',attempts=attempts+1,claim_token=NULL,claim_stamp=NULL,last_error=NULL WHERE agent_id=? AND event_id=?",(old.agent_id,old.event_id))
            return True

    def queue_action(self, operation_id, payload, now):
        with self.transaction() as c:
            old = c.execute("SELECT * FROM outbox WHERE operation_id=?", (operation_id,)).fetchone()
            if old:
                if old["fingerprint"] != digest(payload):
                    raise DomainError("operation_id_content_conflict", 409)
                return dict(old)
            c.execute("INSERT INTO outbox(operation_id,fingerprint,payload,status,claim_token,updated_at,receipt) VALUES(?,?,?,'ready',NULL,?,NULL)", (operation_id, digest(payload), canonical(payload), now))
            return dict(c.execute("SELECT * FROM outbox WHERE operation_id=?", (operation_id,)).fetchone())

    def claim_action(self, operation_id, now):
        with self.transaction() as c:
            token = str(uuid.uuid4())
            changed = c.execute("UPDATE outbox SET status='sending',claim_token=?,updated_at=?,lease_stamp=? WHERE operation_id=? AND status='ready'",
                                (token,now,canonical(lease_stamp(self.lease_clock)),operation_id)).rowcount
            return token if changed else None

    def settle_action(self, operation_id, token, status, receipt, now):
        if status not in {"delivered", "unknown_delivery"}:
            raise DomainError("invalid_delivery_status")
        if status=="delivered":
            if not isinstance(receipt,dict) or receipt.get("operation_id")!=operation_id:
                raise DomainError("delivery_receipt_identity_mismatch",409)
            identity(receipt.get("message_id"),"delivery_message_id")
        with self.transaction() as c:
            if c.execute("UPDATE outbox SET status=?,receipt=?,updated_at=? WHERE operation_id=? AND claim_token=? AND status='sending'",
                         (status, canonical(receipt), now, operation_id, token)).rowcount != 1:
                raise DomainError("delivery_claim_mismatch", 409)

    def mark_interrupted_actions_unknown(self, before):
        with self.transaction() as c:
            return c.execute("UPDATE outbox SET status='unknown_delivery' WHERE status='sending' AND updated_at<?", (before,)).rowcount


RETRY_CLASSIFICATION={"provider_unavailable","invalid_provider_output","invalid_classifier_output","unconfigured","missing_key","provider_output_truncated","empty_provider_output","provider_contract_mismatch","context_retrieval_unavailable","semantic_audit_incomplete"}
