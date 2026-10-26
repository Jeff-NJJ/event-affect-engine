"""Correct-behavior regressions for F01-F11 from the adversarial review."""
import contextlib
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path

from endocrine.api import submit_legacy
from endocrine.classifier import FixedClassifier, UnknownClassifier
from endocrine.clock import FrozenClock
from endocrine.core import apply, initial_state
from endocrine.model import Assessment, Config, DomainError, Event, canonical, digest, iso, timestamp
from endocrine.service import Service
from endocrine.store import Repository
from tools.patch_workflows import guard_command
from tools.replay_events import replay

PROJECT = Path(__file__).resolve().parents[1]


class AdversarialRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.clock = FrozenClock(timestamp("2026-10-16T10:00:00+08:00"))
        self.config = Config()
        self.repo = Repository(self.folder / "brain.db", self.config)
        self.service = Service(self.repo, clock=self.clock)

    def event(self, eid, **values):
        return {"agent_id":"agent", "event_id":eid, "occurred_at":iso(self.clock.now()),
                "text":"synthetic", "actor_id":"person", **values}

    def command(self, eid, kind, payload, **extra):
        return self.event(eid, text="", source="system", provenance="operator_assertion",
                          event_type=kind, payload=payload, **extra)

    def add(self, eid="e", **payload):
        return self.service.submit(self.command("add:" + digest(eid), "expect_add",
            {"id":eid, "expect":"confirmed promise", "deadline":iso(self.clock.now()+10), **payload}))

    def seed_old_row(self, raw, status="pending"):
        # A prior engine accepted this record. Exercise upgrade-time recovery,
        # without weakening current input validation to insert it.
        with self.repo.transaction() as c:
            c.execute("INSERT INTO events(agent_id,event_id,fingerprint,body,received_at,status,lease_token,lease_until) VALUES(?,?,?,?,?,?,?,?)",
                      (raw["agent_id"], raw["event_id"], digest(raw), canonical(raw), self.clock.now(),
                       status, "old-lease" if status == "processing" else None, 0))

    def test_F01_invalid_business_payload_never_enters_queue(self):
        self.add()
        before = self.repo.export()
        for value in ([], {}, None, True, 0, "wrong"):
            with self.subTest(value=value), self.assertRaises(DomainError) as error:
                self.service.submit(self.command("bad", "expect_resolve", {"id":"e", "result":value}))
            self.assertEqual(error.exception.status, 400)
        self.assertEqual(before, self.repo.export())
        self.assertTrue(self.service.submit(self.event("normal"))["success"])

    def test_F01_legacy_invalid_result_returns_validation_error(self):
        for value in ([], {}, None):
            with self.assertRaises(DomainError) as error:
                submit_legacy(self.service, "/expect_resolve", {"event_id":"bad", "id":"e", "result":value})
            self.assertEqual(error.exception.code, "invalid_expectation_result")

    def test_F01_prior_poison_row_is_rejected_and_queue_advances(self):
        self.add()
        self.seed_old_row(self.command("poison", "expect_resolve", {"id":"e", "result":[]}), "processing")
        self.repo.reserve(Event.parse(self.event("later")), self.clock.now())
        self.repo.reserve(Event.parse(self.event("other", agent_id="other")), self.clock.now())
        self.service.recover_pending()
        bad = self.repo.get_event("agent", "poison")
        self.assertEqual(bad["status"], "rejected")
        self.assertEqual(json.loads(bad["response"])["error"], "invalid_expectation_result")
        self.assertEqual(self.repo.get_event("agent", "later")["status"], "applied")
        self.assertEqual(self.repo.get_event("other", "other")["status"], "applied")
        self.assertEqual(self.service.snapshot("agent")["expectations"][0]["status"], "pending")

    def test_F01_unexpected_failure_is_isolated_without_false_success(self):
        class BrokenForOne(UnknownClassifier):
            def classify(self, event):
                if event.agent_id == "bad":
                    raise RuntimeError("synthetic internal failure")
                return super().classify(event)
        self.service.classifier = BrokenForOne()
        for agent in ("bad", "good"):
            self.repo.reserve(Event.parse(self.event(agent, agent_id=agent)), self.clock.now())
        with self.assertLogs("endocrine.service", level="ERROR"):
            self.service.recover_pending()
        self.assertIsNone(self.repo.get_event("bad", "bad")["response"])
        self.assertEqual(self.repo.get_event("good", "good")["status"], "applied")

    def test_F02_reflection_cannot_assert_promises_or_outcomes(self):
        self.add(mutual=True)
        before = self.repo.export()
        for kind, payload in (("expect_add", {"id":"imaginary", "expect":"imagined", "mutual":True}),
                              ("expect_resolve", {"id":"e", "result":"unmet"})):
            raw = self.event("reflection", source="self_reflection", provenance="self_reflection", event_type=kind, payload=payload)
            with self.assertRaises(DomainError) as error:
                self.service.submit(raw)
            self.assertEqual(error.exception.status, 403)
        self.assertEqual(before, self.repo.export())

    def test_F02_source_provenance_contradictions_are_rejected(self):
        for source, provenance in (("self_reflection", "observed_message"), ("user_message", "self_reflection"),
                                   ("external_event", "observed_message"), ("time", "operator_assertion")):
            with self.assertRaises(DomainError):
                self.service.submit(self.event("mismatch", source=source, provenance=provenance))
        self.assertEqual(self.repo.export()["events"], [])

    def test_F02_valid_reflection_has_reduced_gain_and_is_not_real_memory(self):
        assessment = Assessment(kind="rejection", target="agent", agency="actor", speech_act="direct",
                                certainty=1., relevance=1., intensity=1., evidence=("rejected",))
        self.service.classifier = FixedClassifier(assessment)
        result = self.service.submit(self.event("reflection", text="rejected", source="self_reflection", provenance="self_reflection"))
        self.assertAlmostEqual(result["last_appraisal"]["gain"], .075)
        self.assertEqual(result["relationships"], {})
        self.assertEqual(self.repo.recent_memories("agent"), [])

    def test_F02_operator_outcome_audit_matches_actual_relation_delta(self):
        self.add(mutual=True, importance=1.)
        response = self.service.submit(self.command("resolve", "expect_resolve", {"id":"e", "result":"unmet"}))
        relation = response["relationships"]["person"]
        self.assertAlmostEqual(response["last_appraisal"]["trust_delta"], relation["trust"] - .5)
        self.assertEqual(relation["negative_evidence"], 1)
        self.assertEqual(response["last_appraisal"]["outcome_provenance"], "operator_assertion")
        self.assertGreater(response["last_appraisal"]["gain"], 0)

    def test_F02_wrong_person_and_timer_cannot_assert_unmet(self):
        self.add(mutual=True)
        result = self.service.submit(self.command("other", "expect_resolve", {"id":"e", "result":"unmet"}, actor_id="someone_else"))
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "expectation_actor_mismatch")
        with self.assertRaises(DomainError):
            self.service.submit(self.event("timer", source="time", provenance="system", event_type="expect_resolve", payload={"id":"e", "result":"unmet"}))
        self.assertEqual(self.service.snapshot("agent")["expectations"][0]["status"], "pending")

    def test_F04_missing_existing_state_requires_recovery(self):
        self.service.submit(self.event("first"))
        self.service.submit(self.event("second"))
        with self.repo.transaction() as c:
            c.execute("DELETE FROM agent_states WHERE agent_id='agent'")
        for operation in (lambda:self.service.snapshot("agent"), lambda:self.service.submit(self.event("third")), self.repo.export):
            with self.assertRaises(DomainError) as error:
                operation()
            self.assertEqual(error.exception.code, "state_missing_restore_required")
            self.assertEqual(error.exception.status, 503)
        self.assertEqual(self.repo.get_event("agent", "first")["status"], "applied")

    def test_F04_first_ever_role_can_still_initialize(self):
        self.assertEqual(self.service.snapshot("fresh")["persisted_revision"], 0)
        self.assertTrue(self.service.submit(self.event("new", agent_id="fresh"))["success"])

    def test_F05_explicit_third_person_prevents_model_override(self):
        assessment = Assessment(kind="rejection", target="agent", agency="actor", speech_act="direct",
                                certainty=1., relevance=1., intensity=1., evidence=("rejected",))
        raw = self.event("third-party", text="rejected", target_id="someone_else")
        self.service.classifier = FixedClassifier(assessment)
        result = self.service.submit(raw)
        self.assertEqual(result["relationships"]["person"]["trust"], .5)
        self.assertEqual(result["relationships"]["person"]["evidence_count"], 0)
        self.assertEqual(result["core_affect"]["valence"], 0)
        self.assertEqual(result["classify_source"], "explicit_target_conflict")
        direct, _ = apply(initial_state("agent", self.clock.now(), self.config), Event.parse(raw), assessment, self.clock.now(), 1, self.config)
        self.assertEqual(direct["fast_valence"], 0)

    def test_F06_any_emotion_fact_requires_evidence(self):
        for kind in ("unknown", "neutral"):
            self.service.classifier = FixedClassifier(Assessment(kind=kind, observed_affect="sad", target="actor",
                speech_act="direct", certainty=1., relevance=1., intensity=1.))
            result = self.service.submit(self.event(kind, text="a book is on the table"))
            self.assertEqual(result["classify_source"], "invalid_classifier_output")
            self.assertEqual(result["core_affect"]["valence"], 0)
            self.assertEqual(result["social_stress"], 0)

    def test_F07_maximum_length_expectation_ids_expire_independently(self):
        self.add("x" * 240)
        self.add("normal")
        self.clock.advance(3600)
        self.assertEqual(self.service.settle_due(), 2)
        self.assertEqual(self.service.settle_due(), 0)
        self.assertTrue(all(e["status"] == "expired_unknown" for e in self.service.snapshot("agent")["expectations"]))
        expiry = [r for r in self.repo.export()["events"] if r["event_id"].startswith("expect-expiry:")]
        self.assertEqual(len(expiry), 2)
        self.assertTrue(all(len(r["event_id"]) <= 240 for r in expiry))
        self.assertNotEqual(expiry[0]["event_id"], expiry[1]["event_id"])

    def test_F08_wrong_or_unsupported_sources_are_never_modified(self):
        for kind in ("unrelated", "unsupported"):
            source = self.folder / (kind + ".db")
            if kind == "unsupported":
                other = Repository(source, self.config)
                with other.transaction() as c:
                    c.execute("UPDATE metadata SET value='999' WHERE key='schema_version'")
            else:
                with contextlib.closing(sqlite3.connect(source)) as c:
                    c.execute("CREATE TABLE contacts(label TEXT)")
                    c.commit()
            before = source.read_bytes()
            target = self.folder / (kind + ".json")
            result = subprocess.run([sys.executable,"-B",str(PROJECT/"tools/manage_state.py"),"export",str(source),str(target)],
                                    capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(source.read_bytes(), before)
            self.assertFalse(target.exists())

    def test_F08_readonly_export_and_restore_preserve_source(self):
        self.service.submit(self.event("event"))
        before = Path(self.repo.path).read_bytes()
        readonly = Repository(self.repo.path, self.config, readonly=True, create=False)
        self.assertEqual(self.repo.export(), readonly.export())
        with self.assertRaises(DomainError):
            readonly.reserve(Event.parse(self.event("cannot_write")), self.clock.now())
        backup = self.folder / "restored.db"
        result = subprocess.run([sys.executable,"-B",str(PROJECT/"tools/manage_state.py"),"restore-copy",self.repo.path,str(backup)],capture_output=True,timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, Path(self.repo.path).read_bytes())
        self.assertEqual(readonly.export(), Repository(backup,self.config,readonly=True).export())

    def test_version_change_archives_old_config_but_does_not_start_over_it(self):
        source = self.folder / "previous-version.db"
        old = replace(self.config, version="affect-v4.0-default")
        repository = Repository(source, old)
        repository.import_state(initial_state("legacy", self.clock.now(), old))
        before = source.read_bytes()
        archive = self.folder / "previous-version.json"
        saved = subprocess.run([sys.executable,"-B",str(PROJECT/"tools/manage_state.py"),"export",str(source),str(archive)],capture_output=True,timeout=10)
        self.assertEqual(saved.returncode,0,saved.stderr)
        self.assertEqual(json.loads(archive.read_text(encoding="utf-8"))["config_signature"],old.signature)
        started = subprocess.run([sys.executable,"-B","-m","endocrine","--db",str(source),"--port","0"],cwd=PROJECT,capture_output=True,timeout=10)
        self.assertNotEqual(started.returncode,0)
        self.assertIn(b"state_schema_or_config_mismatch",started.stderr)
        self.assertEqual(before,source.read_bytes())
        self.assertEqual(Repository(source,old,readonly=True).export()["events"],[])

    def test_F09_export_is_one_snapshot_during_a_real_commit(self):
        entered, finished = threading.Event(), threading.Event()
        class Interleaved(Repository):
            export_thread = None
            paused = False
            def connect(inner, **kw):
                c = super(Interleaved, inner).connect(**kw)
                if threading.get_ident() == inner.export_thread:
                    def trace(sql):
                        if not inner.paused and sql.startswith("SELECT state FROM initial_states"):
                            inner.paused = True
                            entered.set()
                            finished.wait(10)
                    c.set_trace_callback(trace)
                return c
        repo = Interleaved(self.repo.path, self.config)
        service = Service(repo, clock=self.clock)
        service.submit(self.event("first"))
        errors = []
        def write():
            try:
                if not entered.wait(10):
                    raise RuntimeError("export did not reach interleaving point")
                service.submit(self.event("second"))
            except Exception as error:
                errors.append(repr(error))
            finally:
                finished.set()
        thread = threading.Thread(target=write)
        thread.start()
        repo.export_thread = threading.get_ident()
        data = repo.export()
        repo.export_thread = None
        thread.join(timeout=12)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(data["states"], replay(data))
        self.assertEqual(data["states"][0]["revision"], 1)
        self.assertEqual(service.snapshot("agent")["persisted_revision"], 2)

    def test_F10_budget_stops_claiming_backlog_and_recovers_later(self):
        class Slow(UnknownClassifier):
            count = 0
            def classify(inner, event):
                inner.count += 1
                time.sleep(.04)
                return super(Slow,inner).classify(event)
        classifier = Slow()
        self.service.classifier = classifier
        for i in range(3):
            self.repo.reserve(Event.parse(self.event("backlog:"+str(i))), self.clock.now())
        result = self.service.submit(self.event("target"), wait_seconds=.001)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["http_status"], 202)
        self.assertLessEqual(classifier.count, 1)
        self.service.recover_pending()
        self.assertEqual(self.repo.get_event("agent", "target")["status"], "applied")
        self.assertEqual(self.service.snapshot("agent")["events_processed"], 4)

    def test_F11_commands_are_isolated_and_periodic_lock_cleanup_is_scoped(self):
        for cooldown in (None, 19800):
            command = guard_command("safe_key", "/tmp/review_only", cooldown)
            self.assertTrue(command.startswith("( "))
            self.assertTrue(command.endswith(" )"))
            self.assertNotIn("WF_GUARD_GO", command)
            if cooldown:
                self.assertIn("trap 'rmdir", command)
                self.assertIn("' 0;", command)
                self.assertIn("trap 'exit 1' HUP INT TERM", command)


if __name__ == "__main__":
    unittest.main()
