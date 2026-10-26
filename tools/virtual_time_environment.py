"""Offline virtual-time environment test for the delivered affect engine.

Uses synthetic semantic fixtures, real SQLite and actual child-process exits.
It never changes the OS clock, reaches providers, or executes phone workflows.
"""
import argparse
import concurrent.futures
import contextlib
import csv
import hashlib
import html
import json
import logging
import math
import os
import platform
import random
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ARGS = argparse.ArgumentParser(description=__doc__)
ARGS.add_argument("--project", type=Path, required=True)
ARGS.add_argument("--output", type=Path)
ARGS.add_argument("--scratch", type=Path)
ARGS.add_argument("--child-crash", choices=["before_commit", "after_commit"])
ARGS.add_argument("--db", type=Path)
ARGS.add_argument("--now", type=float)
OPTIONS = ARGS.parse_args()
PROJECT = OPTIONS.project.resolve()
sys.path.insert(0, str(PROJECT))
from endocrine import __version__
from endocrine.api import make_server
from endocrine.clock import FrozenClock
from endocrine.core import advance
from endocrine.model import Assessment, Config, DomainError, Event, canonical, iso, timestamp
from endocrine.service import Service
from endocrine.store import Repository
from tools.replay_events import replay

TZ = timezone(timedelta(hours=8))
T0 = timestamp("2026-10-17T00:00:00+08:00")
SEED = 20261017

FACTS = {
    "support": ("谢谢你一直支持我", "agent", "happy", "actor", "direct"),
    "gratitude": ("谢谢你认真帮我", "agent", "happy", "actor", "direct"),
    "rejection": ("我不想再和你说话", "agent", "angry", "actor", "direct"),
    "concern": ("我考试没过，很难过", "actor", "sad", "external", "direct"),
    "world": ("游戏输了，我有点失落", "actor", "sad", "external", "direct"),
    "curiosity": ("我想弄清楚星星为什么发光", "actor", "neutral", "external", "direct"),
    "technical": ("这段代码有缺陷，需要修改", "agent", "neutral", "actor", "technical"),
    "quote": ("我在引用台词：我讨厌你", "agent", "angry", "actor", "quote"),
    "neutral": ("桌上有一本书", "unknown", "neutral", "unknown", "direct"),
}


class ScriptedClassifier:
    def classify(self, event):
        key = event.payload.get("fixture", "neutral")
        _, target, affect, agency, speech = FACTS[key]
        kind = {"concern":"loss", "world":"loss", "technical":"criticism", "quote":"rejection"}.get(key,key)
        return Assessment(kind=kind,target=target,observed_affect=affect,agency=agency,speech_act=speech,
            certainty=.92,relevance=.85,intensity=.72,evidence=(event.text[:240],),
            classifier_status="environment_fixture",model_version="synthetic-facts-v1")


def event(clock, eid, fixture="support", **extra):
    return {"agent_id":"example_agent", "event_id":eid,"occurred_at":iso(clock.now()),
            "actor_id":"example_user", "text":FACTS[fixture][0],"payload":{"fixture":fixture}, **extra}


def command(clock,eid,kind,payload,**extra):
    return {"agent_id":"example_agent","event_id":eid,"occurred_at":iso(clock.now()),
            "source":"system","provenance":"operator_assertion","actor_id":"example_user",
            "event_type":kind,"text":"","payload":payload,**extra}


def child():
    clock = FrozenClock(OPTIONS.now)
    with patch("endocrine.store.time.time", clock.now):
        service = Service(Repository(OPTIONS.db,Config()),ScriptedClassifier(),clock)
        if OPTIONS.child_crash == "before_commit":
            service.before_commit = lambda _connection: os._exit(91)
        service.submit(event(clock,"crash-event","rejection"))
        os._exit(92)


class Environment:
    def __init__(self,path):
        self.path = path
        self.clock = FrozenClock(T0)
        self.repo = Repository(path,Config())
        self.service = Service(self.repo,ScriptedClassifier(),self.clock)

    def restart(self):
        self.repo = Repository(self.path,Config())
        self.service = Service(self.repo,ScriptedClassifier(),self.clock)


class Failure(Exception):
    pass


class Run:
    def __init__(self,folder):
        self.folder = folder
        self.cases = []
        self.details = {}
        self.assertions = 0
        self.timeline = []

    def check(self,condition,message):
        self.assertions += 1
        if not condition:
            raise Failure(message)

    @contextlib.contextmanager
    def env(self,name):
        env = Environment(self.folder/(name+".db"))
        with patch("endocrine.store.time.time", env.clock.now):
            yield env

    def case(self,key,title,operation,observation=False):
        started = time.perf_counter()
        self.details = {}
        count = self.assertions
        try:
            operation()
            status = "observation" if observation else "passed"
            error = None
        except Exception as exc:
            status = "failed"
            error = type(exc).__name__+": "+str(exc)
        row = {"id":key,"title":title,"status":status,"assertions":self.assertions-count,
               "wall_seconds":round(time.perf_counter()-started,4),"details":self.details}
        if error:
            row["error"] = error
        self.cases.append(row)
        print(key,status,error or "",flush=True)

    def circadian(self):
        with self.env("circadian") as e:
            samples = []
            for hour in range(25):
                e.clock.value = T0+hour*3600
                s = e.service.snapshot()
                samples.append({"hour":hour,"arousal":s["arousal"],"emotions":s["emotions"]})
                self.check(s["core_affect"]["valence"]==0,"No stimulus must not create valence")
                self.check(s["events_processed"]==0 and s["behaviors"]==[],"Time cannot invent events or actions")
            self.check(samples[13]["arousal"] > samples[1]["arousal"],"Circadian phase must affect activation")
            self.check(e.repo.export()["events"]==[],"Snapshots must leave the event log empty")
            self.details.update(samples=samples)

    def recovery(self):
        with self.env("recovery") as e:
            first = e.service.submit(event(e.clock,"hurt","rejection"))
            trust = first["relationships"]["example_user"]["trust"]
            samples = []
            for hours in (0,.75,3,12,24,168):
                e.clock.value = T0+hours*3600
                snap = e.service.snapshot()
                samples.append({"hours":hours,"valence":snap["core_affect"]["valence"],"stress":snap["social_stress"],"label":snap["emotions"]})
                self.check(snap["relationships"]["example_user"]["trust"]==trust,"Silence cannot alter trust")
            self.check(abs(samples[-1]["valence"])<.001,"Seven quiet days should recover short-term affect")
            self.check(samples[-1]["label"]==["平静"],"Recovered state must not retain stale hurt label")
            self.details.update(samples=samples,trust_after_hurt=trust)

    def partition(self):
        with self.env("partition") as e:
            e.service.submit(event(e.clock,"pulse","rejection"))
            state = e.repo.load_state("example_agent",T0)
            direct = advance(state,T0+7*86400,e.repo.config)
            split = state
            for i in range(1,2017):
                split = advance(split,T0+i*300,e.repo.config)
            error = max(abs(direct[k]-split[k]) for k in ("fast_valence","fast_arousal","mood_valence","mood_arousal","stress"))
            self.details.update(intervals=2016,step_seconds=300,max_absolute_error=error)
            self.check(error<1e-12,"Time partition changed the trajectory")

    def readonly(self):
        with self.env("readonly") as e:
            e.service.submit(event(e.clock,"contact"))
            before = e.repo.export()
            for i in range(500):
                e.service.snapshot(as_of=T0+i*173)
                e.repo.recent_memories("example_agent")
            self.check(before==e.repo.export(),"Observation frequency changed persisted state")
            self.details.update(snapshot_reads=500,memory_reads=500,persisted_revision=1)

    def attribution(self):
        with self.env("attribution") as e:
            a = e.service.submit(event(e.clock,"sad","concern"))
            self.check(a["relationships"]["example_user"]["trust"]==.5,"Other person's sadness must not become distrust")
            self.check("关切" in a["emotions"],"Expected concern for another person's sadness")
            e.clock.advance(3600)
            b = e.service.submit(event(e.clock,"harm-b","rejection",actor_id="person_b"))
            self.check(b["relationships"]["example_user"]["trust"]==.5,"Person B cannot lower person A's trust")
            self.check(b["relationships"]["person_b"]["trust"]<.5,"Direct harm should retain bounded relational evidence")
            prior = b["relationships"]
            e.clock.advance(3600)
            world = e.service.submit(event(e.clock,"world","world",actor_id=None,source="external_event",provenance="observed_event",event_type="external_experience"))
            self.check(world["relationships"]==prior,"World events cannot add interpersonal evidence")
            self.details.update(concern_valence=a["core_affect"]["valence"],primary_trust=.5,b_trust=b["relationships"]["person_b"]["trust"])

    def reflection(self):
        with self.env("reflection") as e:
            e.service.submit(event(e.clock,"original","rejection"))
            before = e.service.snapshot()["relationships"]
            e.clock.advance(3600)
            recalled = e.service.submit(event(e.clock,"recall","rejection",source="self_reflection",provenance="self_reflection",event_type="recall",causation_id="original"))
            self.check(recalled["relationships"]==before,"Recall cannot add new external evidence")
            self.check(len(e.repo.recent_memories("example_agent"))==1,"Recall must not masquerade as another real message")
            self.details.update(recall_gain=recalled["last_appraisal"]["gain"],relationship_evidence=before["example_user"]["evidence_count"])

    def habituation(self):
        with self.env("habituation") as e:
            gains = []
            for i in range(24):
                e.clock.value = T0+i*300
                result = e.service.submit(event(e.clock,"repeat:"+str(i),"rejection"))
                gains.append(result["last_appraisal"]["gain"])
            relation = result["relationships"]["example_user"]
            self.check(gains[-1]<gains[0],"New repeated real events should habituate short-term response")
            self.check(relation["evidence_count"]==24,"Habituation must not erase independent relational evidence")
            self.details.update(first_gain=gains[0],last_gain=gains[-1],evidence_count=24,final_trust=relation["trust"])

    def expiry(self):
        with self.env("expiry") as e:
            e.service.submit(event(e.clock,"contact"))
            trust=e.service.snapshot()["relationships"]["example_user"]["trust"]
            deadline=T0+7200
            e.service.submit(command(e.clock,"expect","expect_add",{"id":"e","expect":"a mutually agreed reply","mutual":True,"deadline":iso(deadline)}))
            e.clock.value=deadline+299
            self.check(e.service.settle_due()==0,"Expectation expired before the grace period")
            e.clock.advance(1)
            self.check(e.service.settle_due()==1,"Expectation did not expire at deadline plus grace")
            after=e.service.snapshot()
            self.check(after["expectations"][0]["status"]=="expired_unknown","No receipt must mean unknown")
            self.check(after["relationships"]["example_user"]["trust"]==trust,"Unknown expiry must not punish trust")
            self.check(e.service.settle_due()==0,"Repeated ticks duplicated expiry")
            self.details.update(deadline=iso(deadline),settled_at=iso(e.clock.now()),trust_unchanged=True)

    def offline_gap(self):
        with self.env("offline_gap") as e:
            e.service.submit(event(e.clock,"contact"))
            e.service.submit(command(e.clock,"expect","expect_add",{"id":"offline","expect":"a reply","mutual":True,"deadline":iso(T0+3600)}))
            trust=e.service.snapshot()["relationships"]["example_user"]["trust"]
            e.clock.advance(7*86400)
            e.restart()
            self.check(e.service.settle_due()==1,"A due expectation was lost across an offline gap")
            after=e.service.snapshot()
            self.check(after["relationships"]["example_user"]["trust"]==trust,"Offline silence must not manufacture rejection")
            self.check(after["expectations"][0]["status"]=="expired_unknown","Unknown offline result was not preserved")
            resolved=e.service.submit(command(e.clock,"late-receipt","expect_resolve",{"id":"offline","result":"fulfilled"}))
            self.check(resolved["expectations"][0]["status"]=="fulfilled","A later real result must refine unknown")
            self.details.update(offline_days=7,expiry="expired_unknown",late_receipt="fulfilled")

    def retries(self):
        with self.env("retries") as e:
            raw=event(e.clock,"lost-response")
            first=e.service.submit(raw)
            e.clock.advance(86400)
            e.restart()
            answers=[e.service.submit(raw) for _ in range(10)]
            self.check(all(a["deduplicated"] and a["revision"]==first["revision"] for a in answers),"Response-loss retry changed the event")
            self.check(e.service.snapshot()["events_processed"]==1,"Retries counted as fresh experiences")
            self.details.update(retries=10,unique_applied=1)

    def concurrent(self):
        with self.env("concurrent") as e:
            raw=event(e.clock,"parallel")
            services=[Service(Repository(e.path,Config()),ScriptedClassifier(),e.clock) for _ in range(5)]
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                rows=list(pool.map(lambda i:services[i%5].submit(raw),range(50)))
            self.check(all(r["success"] for r in rows),"A concurrent retry failed")
            self.check(e.service.snapshot()["events_processed"]==1,"Concurrent duplicate counted more than once")
            self.details.update(requests=50,service_instances=5,unique_applied=1)

    def crash(self,mode):
        with self.env("crash_"+mode) as e:
            baseline=e.service.submit(event(e.clock,"baseline"))
            child_raw=event(e.clock,"crash-event","rejection")
            process=subprocess.run([sys.executable,"-B",str(Path(__file__).resolve()),"--project",str(PROJECT),
                "--child-crash",mode,"--db",str(e.path),"--now",str(e.clock.now())],capture_output=True,timeout=20)
            self.check(process.returncode==(91 if mode=="before_commit" else 92),"Child did not reach the intended crash point")
            e.restart()
            if mode=="before_commit":
                self.check(e.service.snapshot()["persisted_revision"]==baseline["revision"],"Uncommitted state survived process termination")
                e.clock.advance(121)
                e.service.recover_pending()
            response=e.service.submit(child_raw)
            self.check(response["deduplicated"],"Persisted event identity was lost on restart")
            self.check(e.service.snapshot()["events_processed"]==2,"Crash/recovery did not apply exactly one new event")
            self.check(replay(e.repo.export())==e.repo.export()["states"],"Crash recovery lost replay consistency")
            self.details.update(child_exit=process.returncode,applied_events_after_recovery=2,replay_matches=True)

    def lease_takeover(self):
        with self.env("lease_takeover") as e:
            entered,release=threading.Event(),threading.Event()
            class Blocked(ScriptedClassifier):
                def classify(self,event):
                    entered.set()
                    if not release.wait(8):
                        raise RuntimeError("controlled classifier was not released")
                    return super().classify(event)
            first=Service(e.repo,Blocked(),e.clock)
            raw=event(e.clock,"inflight")
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pending=pool.submit(first.submit,raw)
                self.check(entered.wait(5),"Classifier did not enter")
                e.clock.advance(121)
                try:
                    takeover=e.service.submit(raw)
                finally:
                    release.set()
                original=pending.result(timeout=8)
            self.check(takeover["success"] and original["success"],"Lease takeover failed")
            self.check(e.service.snapshot()["events_processed"]==1,"Stale claimant committed a second application")
            self.details.update(virtual_lease_seconds=121,committed_applications=1)

    def clock_back_state(self):
        with self.env("clock_back_state") as e:
            e.service.submit(event(e.clock,"first"))
            state=e.repo.load_state("example_agent",e.clock.now())
            e.clock.advance(-7200)
            e.service.submit(event(e.clock,"after-clock-back","neutral"))
            after=e.repo.load_state("example_agent",e.clock.now())
            self.check(after["state_time"]>=state["state_time"],"Wall-clock rollback reversed persisted affect time")
            self.check(after["events_processed"]==2,"Rollback lost a legitimate new event")
            self.details.update(wall_clock_rollback_hours=2,state_time_never_decreased=True)

    def clock_back_lease(self):
        with self.env("clock_back_lease") as e:
            raw=event(e.clock,"abandoned")
            e.repo.reserve(Event.parse(raw),e.clock.now())
            claim=e.repo.claim_next("example_agent")
            self.check(claim is not None,"Could not establish abandoned lease")
            e.clock.advance(-7200)
            e.clock.advance(121)
            recovered=e.repo.claim_next("example_agent")
            remaining=e.repo.get_event("example_agent","abandoned")["lease_until"]-e.clock.now()
            self.details.update(rollback_seconds=7200,elapsed_after_rollback_seconds=121,
                reclaimed_after_121_seconds=recovered is not None,remaining_wall_lease_seconds=remaining,
                expected="An abandoned 120-second lease should not require hours of wall-clock catch-up")
            e.clock.value=T0+121
            final=recovered or e.repo.claim_next("example_agent")
            if final:
                e.service._process(final)
            self.details["eventually_recovers_when_wall_clock_catches_up"]=e.repo.get_event("example_agent","abandoned")["status"]=="applied"
            self.check(recovered is not None,"System clock rollback extends an abandoned lease by nearly two hours")

    def long_jump(self):
        with self.env("long_jump") as e:
            first=e.service.submit(event(e.clock,"start","rejection"))
            trust=first["relationships"]["example_user"]["trust"]
            e.clock.advance(365*86400)
            after=e.service.snapshot()
            self.check(abs(after["core_affect"]["valence"])<1e-9,"One year of silence did not decay short affect")
            self.check(after["relationships"]["example_user"]["trust"]==trust,"Long silence invented relationship evidence")
            self.check(after["events_processed"]==1 and after["behaviors"]==[],"Time jump invented experiences or actions")
            self.details.update(jump_days=365,valence=after["core_affect"]["valence"],trust=trust)

    def invalid_then_valid(self):
        with self.env("invalid_then_valid") as e:
            e.service.submit(command(e.clock,"add","expect_add",{"id":"e","expect":"reply"}))
            try:
                e.service.submit(command(e.clock,"bad","expect_resolve",{"id":"e","result":[]}))
            except DomainError as exc:
                self.check(exc.status==400,"Bad input did not return validation error")
            else:
                self.check(False,"Bad input was accepted")
            e.clock.advance(3600)
            result=e.service.submit(event(e.clock,"valid"))
            self.check(result["success"],"Bad input blocked a later valid event")
            self.details.update(bad_event_reserved=e.repo.get_event("example_agent","bad") is not None,valid_event_applied=True)

    def localhost_http(self):
        with self.env("http") as e:
            server=make_server(e.service,port=0,token="virtual-test-only")
            worker=threading.Thread(target=server.serve_forever,daemon=True)
            worker.start()
            base="http://127.0.0.1:"+str(server.server_port)
            try:
                raw=event(e.clock,"http-contact")
                def request(route,body=None,token="virtual-test-only"):
                    req=urllib.request.Request(base+route,data=json.dumps(body).encode() if body else None,
                        headers={"X-Token":token,"Content-Type":"application/json"})
                    with urllib.request.urlopen(req,timeout=5) as response:
                        return response.status,json.load(response)
                _,first=request("/v2/events",raw)
                e.clock.advance(24*3600)
                _,second=request("/v2/events",raw)
                self.check(second["deduplicated"] and first["revision"]==second["revision"],"HTTP retry was not idempotent across virtual days")
                _,snap=request("/snapshot")
                self.check(snap["events_processed"]==1,"HTTP read changed event count")
                try:
                    request("/snapshot",token="wrong")
                except urllib.error.HTTPError as exc:
                    self.check(exc.code==403,"Wrong token did not receive 403")
                else:
                    self.check(False,"Wrong token was accepted")
                self.details.update(localhost_only=True,virtual_jump_hours=24,duplicate_applied_once=True)
            finally:
                server.shutdown();server.server_close();worker.join(timeout=5)

    def late_event_observation(self):
        with self.env("late_fresh") as fresh, self.env("late_old") as old:
            a=fresh.service.submit(event(fresh.clock,"fresh","rejection"))
            b=old.service.submit(event(old.clock,"old","rejection",occurred_at=iso(T0-7*86400),ingestion_mode='historical_replay'))
            self.check(b["last_appraisal"]["late_by_seconds"]==7*86400,"Lateness was not audited")
            self.details.update(fresh_valence=a["core_affect"]["valence"],seven_day_late_valence=b["core_affect"]["valence"],
                late_by_seconds=b["last_appraisal"]["late_by_seconds"],
                interpretation="Explicit historical replay inserts an aged residual; live first knowledge is tested independently and remains fresh.")
            self.check(abs(b["core_affect"]["valence"])<abs(a["core_affect"]["valence"])*.01,"A seven-day-old event became fresh hurt")

    def thirty_days(self):
        with self.env("thirty_days") as e:
            rng=random.Random(SEED)
            scheduled={}
            for day in range(21):
                for hour,fixture in ((9,"support"),(11,"curiosity"),(14,"technical"),(18,"concern"),(21,"gratitude")):
                    if day in (5,12) and hour==21:
                        fixture="rejection"
                    if day%4==0 and hour==18:
                        fixture="world"
                    scheduled.setdefault(day*24+hour,[]).append((fixture,"person_b" if day%3==0 and hour==11 else "example_user"))
            duplicates=0;restarts=0;maintained=0;quiet_relationship=None
            for h in range(30*24+1):
                e.clock.value=T0+h*3600
                if h%24==3:
                    e.restart();restarts+=1
                for j,(fixture,person) in enumerate(scheduled.get(h,[])):
                    eid="day-event:"+str(h)+":"+str(j)
                    extra={"actor_id":person}
                    if fixture=="world":
                        extra.update(actor_id=None,source="external_event",provenance="observed_event",event_type="external_experience")
                    raw=event(e.clock,eid,fixture,**extra)
                    result=e.service.submit(raw)
                    self.check(result["success"],"Timeline event failed")
                    if rng.random()<.25:
                        for _ in range(3):
                            retry=e.service.submit(raw);duplicates+=1
                            self.check(retry["deduplicated"] and retry["revision"]==result["revision"],"Timeline retry added state")
                if h<8*24 and h%24==23:
                    e.service.submit(command(e.clock,"plan:"+str(h),"expect_add",{"id":"planned:"+str(h),"expect":"synthetic overnight reply","mutual":True,"deadline":iso(e.clock.now()+6*3600)}))
                maintained+=e.service.settle_due()
                before=e.repo.load_state("example_agent",e.clock.now())
                snap=e.service.snapshot()
                self.check(before==e.repo.load_state("example_agent",e.clock.now()),"Timeline query mutated state")
                values=(snap["core_affect"]["valence"],snap["arousal"],snap["mood"]["valence"],snap["social_stress"])
                self.check(all(math.isfinite(v) and -1<=v<=1 for v in values),"Non-finite or unbounded state in long run")
                self.check(snap["behaviors"]==[],"Simulator produced an unauthorized action")
                for relation in snap["relationships"].values():
                    self.check(all(0<=relation[k]<=1 for k in ("trust","closeness","familiarity","confidence")),"Relationship exceeded bounds")
                if h==21*24:
                    quiet_relationship=snap["relationships"]
                if h>21*24:
                    self.check(quiet_relationship==snap["relationships"],"Nine silent days changed relationships")
                self.timeline.append({"hours":h,"local_time":datetime.fromtimestamp(e.clock.now(),TZ).isoformat(),
                    "valence":values[0],"arousal":values[1],"mood_valence":values[2],"stress":values[3],
                    "primary_trust":snap["relationships"].get("example_user",{}).get("trust",.5),
                    "person_b_trust":snap["relationships"].get("person_b",{}).get("trust",.5),
                    "events_processed":snap["events_processed"],"revision":snap["persisted_revision"],
                    "pending_expectations":len(snap["expects_pending"]),"emotions":"/".join(snap["emotions"])})
            data=e.repo.export()
            self.check(replay(data)==data["states"],"Thirty-day event stream cannot replay to persisted state")
            self.check(len({(r["agent_id"],r["event_id"]) for r in data["events"]})==len(data["events"]),"Duplicate IDs in SQLite")
            self.check(all(r["status"] in {"applied","rejected"} for r in data["events"]),"Long run left a stuck queue")
            self.check(abs(self.timeline[-1]["valence"])<.001,"Silent tail failed to recover affect")
            self.details.update(virtual_days=30,hourly_samples=len(self.timeline),distinct_event_rows=len(data["events"]),
                duplicate_network_attempts=duplicates,recreated_service_instances=restarts,unknown_expiries=maintained,
                sqlite_bytes=e.path.stat().st_size,final=self.timeline[-1],replay_matches=True,
                schedule="21 days of scripted interactions followed by 9 days of silence")


def write_outputs(run,output,elapsed):
    output.mkdir(parents=True,exist_ok=True)
    with (output/"30_day_timeline.csv").open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=list(run.timeline[0]) if run.timeline else ["hours"])
        writer.writeheader();writer.writerows(run.timeline)
    passed=sum(c["status"]=="passed" for c in run.cases)
    failures=[c for c in run.cases if c["status"]=="failed"]
    manifest=PROJECT/"SOURCE_MANIFEST.sha256"
    mismatch=[]
    for line in manifest.read_text(encoding="utf-8").splitlines():
        expected,relative=line.split("  ",1)
        if hashlib.sha256((PROJECT/relative).read_bytes()).hexdigest()!=expected:
            mismatch.append(relative)
    report={"tested_version":__version__,"config_version":Config().version,"random_seed":SEED,
        "virtual_start_local":"2026-10-17T00:00:00+08:00","wall_elapsed_seconds":round(elapsed,3),
        "python":platform.python_version(),"platform":platform.system(),"scenario_count":len(run.cases),
        "passed_scenarios":passed,"failed_scenarios":len(failures),"observations":sum(c["status"]=="observation" for c in run.cases),
        "assertions_evaluated":run.assertions,"release_manifest_matches":not mismatch,"manifest_mismatches":mismatch,
        "manifest_scope":"SOURCE_MANIFEST.sha256; source and documentation, excluding generated reports",
        "verdict":"issues_found" if failures else "executed_scenarios_passed",
        "scope":["Synthetic, fixed semantic fixtures; not online classifier accuracy or human realism", "Real SQLite, real process exits and loopback HTTP only",
                 "Application and lease wall clocks are virtualized; OS time is unchanged; waiting timeouts use real monotonic time",
                 "No Android shell, phone, external provider, QQ or real outbound delivery tested"],"cases":run.cases}
    (output/"results.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    lines=["# 虚拟时间环境测试报告\n", f"测试对象：v{__version__} 情绪核心；固定语义输入，不代表线上语义准确率。表达层另见 test_expression.py。\n",
        f"运行 {len(run.cases)} 个场景：**{passed} 通过、{len(failures)} 失败、{report['observations']} 项设计观察**。实际耗时 {elapsed:.2f} 秒，执行 {run.assertions} 条检查。\n",
        "虚拟主时间线从 2026-10-17 00:00（北京时间）推进 30 天，另覆盖 365 天时间跳跃。使用固定语义事实，不调用真实模型。\n",
        "## 场景结果\n", "| 场景 | 结果 | 检查数 |\n|---|---|---|\n"]
    names={"passed":"通过","failed":"失败","observation":"设计观察"}
    for case in run.cases:
        lines.append(f"| {case['id']} {case['title']} | {names[case['status']]} | {case['assertions']} |\n")
    lines.append("\n## 发现的问题\n")
    for case in failures:
        lines.append(f"\n### {case['id']} {case['title']}\n\n{case['error']}\n\n```json\n{json.dumps(case['details'],ensure_ascii=False,indent=2)}\n```\n")
    if not failures:
        lines.append("\n本轮已执行场景未发现不满足断言的行为；不代表未测试环境已经通过。\n")
    lines.append("\n## 时间与工程边界\n\n系统时间回拨的租约用例模拟的是宿主机 wall clock 回拨，不是只调整角色时区。若该用例失败，不应把状态时间防倒退等同于所有计时机制都已安全。\n\n进程异常退出用例真实调用 os._exit，覆盖提交前事务回滚和提交后回执丢失；它不等同于硬件断电或磁盘缓存断电耐久性验证。\n\n日夜波动是当前模型的数值节律，不代表已实现生物睡眠或已验证像真人。主时间线最后九天没有新互动，用于观察恢复及关系证据是否被凭空改变。\n\n本轮未执行 Android/POSIX 守卫、手机工具输出包装、远端发送回执或真实模型分类。\n")
    lines.append("\n## 设计观察\n")
    for case in run.cases:
        if case["status"]=="observation":
            lines.append(f"\n{case['title']}：\n\n```json\n{json.dumps(case['details'],ensure_ascii=False,indent=2)}\n```\n")
    long=next((c["details"] for c in run.cases if c["id"]=="T20"),{})
    lines.append("\n## 30 天结果摘要\n\n```json\n"+json.dumps(long,ensure_ascii=False,indent=2)+"\n```\n")
    lines.append("\n完整证据见 results.json，逐小时数据见 30_day_timeline.csv，离线图表见 timeline.html。源码清单比对结果："+("与交付一致。" if not mismatch else "存在不匹配，详见 results.json。")+"\n")
    (output/"环境测试报告.md").write_text("".join(lines),encoding="utf-8")
    data=json.dumps(run.timeline,ensure_ascii=False)
    page='''<!doctype html><html lang="zh"><meta charset="utf-8"><title>30天虚拟时间轨迹</title>
<style>body{font:16px system-ui;background:#f4f6fa;color:#183047;margin:30px auto;max-width:1100px;padding:0 20px}section{background:white;padding:20px;border-radius:12px;margin:18px 0}svg{width:100%;height:auto} .muted{color:#526679}label{display:inline-block;margin-right:16px}table{border-collapse:collapse;width:100%}td,th{padding:7px;border-bottom:1px solid #ddd;text-align:left}</style>
<h1>30天虚拟环境轨迹</h1><p>21天合成互动，随后9天静默。固定语义输入；不代表真实模型准确率或人类生理标定。</p>
<section><label>查看至第 <input id="day" type="range" min="1" max="30" value="30"> <b id="n">30</b> 天</label><p class="muted">效价 −1…1；其他显示量 0…1。浅色背景为静默期。</p><svg id="chart" viewBox="0 0 1040 430"></svg><p id="legend"></p></section>
<section><h2>时间点</h2><p id="selected"></p><table><thead><tr><th>指标</th><th>值</th></tr></thead><tbody id="values"></tbody></table></section>
<script>const rows=__DATA__;const colors={valence:'#2358ce',arousal:'#e09510',mood_valence:'#8755aa',stress:'#d83b54',primary_trust:'#12876c',person_b_trust:'#64748b'};let end=720;const chart=document.querySelector('#chart');function draw(){end=+document.querySelector('#day').value*24;document.querySelector('#n').textContent=end/24;let s='<rect x="55" y="20" width="950" height="350" fill="#fff"/>';if(end>504)s+=`<rect x="${55+504/end*950}" y="20" width="${950*(end-504)/end}" height="350" fill="#edf5f1"/>`;for(let v=-1;v<=1.001;v+=.5){let y=195-v*175;s+=`<path d="M55 ${y}H1005" stroke="#dce2e9"/><text x="5" y="${y+5}" font-size="13">${v.toFixed(1)}</text>`;}for(let k in colors){let points=rows.filter(r=>r.hours<=end).map(r=>`${55+r.hours/end*950},${195-r[k]*175}`).join(' ');s+=`<polyline points="${points}" fill="none" stroke="${colors[k]}" stroke-width="1.7"/>`;}for(let i=0;i<=6;i++)s+=`<text x="${55+i*950/6}" y="400" font-size="13">${(end*i/6/24).toFixed(0)}天</text>`;chart.innerHTML=s;document.querySelector('#legend').innerHTML=Object.keys(colors).map(k=>`<span style="color:${colors[k]};margin-right:15px">● ${k}</span>`).join('');show(end);}function show(h){let r=rows[Math.max(0,Math.min(rows.length-1,Math.round(h)))];document.querySelector('#selected').textContent=r.local_time+' · '+r.emotions;document.querySelector('#values').innerHTML=Object.keys(colors).concat(['events_processed','revision','pending_expectations']).map(k=>`<tr><td>${k}</td><td>${typeof r[k]==='number'?r[k].toFixed(5):r[k]}</td></tr>`).join('');}document.querySelector('#day').oninput=draw;chart.onmousemove=e=>{let box=chart.getBoundingClientRect();show(((e.clientX-box.left)/box.width*1040-55)/950*end);};draw();</script></html>'''
    (output/"timeline.html").write_text(page.replace("__DATA__",data),encoding="utf-8")
    return report


def main():
    if OPTIONS.child_crash:
        child();return
    if not OPTIONS.output:
        ARGS.error("--output is required for a complete run")
    logging.getLogger("endocrine.api").setLevel(logging.CRITICAL)
    started=time.perf_counter()
    if OPTIONS.scratch:
        OPTIONS.scratch.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="virtual-affect-",dir=OPTIONS.scratch) as temp:
        run=Run(Path(temp))
        cases=[("T01","24小时日夜基线",run.circadian), ("T02","负面事件后的7天恢复",run.recovery),
            ("T03","时间分段一致性",run.partition),("T04","查询频率不改变状态",run.readonly),
            ("T05","他人悲伤、人物及外部事件隔离",run.attribution),("T06","回忆不新增外部证据",run.reflection),
            ("T07","重复真实事件习惯化与独立关系证据",run.habituation),("T08","期限、宽限期与未知结果",run.expiry),
            ("T09","离线7天重启与迟到回执",run.offline_gap),("T10","跨天响应丢失重试",run.retries),
            ("T11","50并发同ID提交",run.concurrent),("T12","提交前真实进程退出",lambda:run.crash("before_commit")),
            ("T13","提交后真实进程退出",lambda:run.crash("after_commit")),("T14","租约超时与旧处理者返回",run.lease_takeover),
            ("T15","系统时间回拨时状态防倒退",run.clock_back_state),("T16","系统时间回拨时租约恢复预算",run.clock_back_lease),
            ("T17","365天无事件时间跳跃",run.long_jump),("T18","非法命令后正常事件可继续",run.invalid_then_valid),
            ("T19","本地HTTP跨天与鉴权",run.localhost_http),("T20","30天连续时间线及9天静默",run.thirty_days)]
        for key,title,operation in cases:
            run.case(key,title,operation)
        run.case("T21","明确的历史重放只保留已衰减情绪",run.late_event_observation)
        report=write_outputs(run,OPTIONS.output.resolve(),time.perf_counter()-started)
    print(json.dumps({k:report[k] for k in ("scenario_count","passed_scenarios","failed_scenarios","observations","assertions_evaluated","wall_elapsed_seconds","verdict")},ensure_ascii=False),flush=True)
    if report["failed_scenarios"]:
        raise SystemExit(1)


if __name__=="__main__":
    main()
