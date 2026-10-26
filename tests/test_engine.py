import concurrent.futures
import copy
import json
import math
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from dataclasses import replace
from pathlib import Path

from endocrine.api import legacy_event, make_server, submit_legacy
from endocrine.classifier import FixedClassifier, UnknownClassifier
from endocrine.clock import FrozenClock
from endocrine.core import advance, initial_state
from endocrine.model import Assessment, Config, DomainError, Event, iso, timestamp
from endocrine.service import Service
from endocrine.store import Repository


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Config()
        self.clock = FrozenClock(timestamp("2026-10-16T10:00:00+08:00"))
        self.repo = Repository(Path(self.tmp.name) / "brain.sqlite3", self.config)
        self.service = Service(self.repo, clock=self.clock)

    def event(self, eid="m1", text="谢谢你", **kw):
        return {"agent_id": "a", "event_id": eid, "occurred_at": iso(self.clock.now()),
                "actor_id": "p", "target_id": "a", "source": "user_message", "text": text, **kw}

    def assessed(self, event, **kw):
        raw = dict(kind="gratitude", observed_affect="happy", target="agent", speech_act="direct",
                   agency="actor", certainty=0.9, relevance=0.8, intensity=0.6,
                   evidence=(event["text"],))
        raw.update(kw)
        self.service.classifier = FixedClassifier(Assessment(**raw))
        return self.service.submit(event)

    def state(self):
        return self.repo.load_state("a", self.clock.now())

    def test_retry_ten_times_exactly_one_event(self):
        e = self.event()
        first = self.assessed(e)
        for _ in range(10):
            response = self.service.submit(e)
            self.assertTrue(response["deduplicated"])
            self.assertEqual(first["revision"], response["revision"])
        self.assertEqual(self.state()["events_processed"], 1)
        self.assertEqual(self.state()["relationships"]["p"]["evidence_count"], 1)

    def test_concurrent_fifty_duplicates_across_service_instances(self):
        e = self.event()
        a = Assessment(kind="gratitude", target="agent", speech_act="direct", agency="actor",
                       certainty=.9, relevance=.8, intensity=.6, evidence=(e["text"],))
        services = [Service(Repository(self.repo.path, self.config), FixedClassifier(a), self.clock) for _ in range(5)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda i: services[i % 5].submit(e), range(50)))
        self.assertTrue(all(r["success"] for r in results))
        self.assertEqual(self.state()["revision"], 1)
        self.assertEqual(len(self.repo.export()["state_changes"]), 1)

    def test_same_id_different_content_conflict(self):
        self.assessed(self.event())
        with self.assertRaises(DomainError) as c:
            self.service.submit(self.event(text="其他内容"))
        self.assertEqual(c.exception.status, 409)

    def test_same_text_new_id_is_new_experience(self):
        first = self.assessed(self.event())
        second = self.assessed(self.event("m2"))
        self.assertEqual(second["events_processed"], 2)
        self.assertLess(second["last_appraisal"]["gain"], first["last_appraisal"]["gain"])
        self.assertEqual(self.state()["relationships"]["p"]["evidence_count"], 2)

    def test_committed_response_lost_restart_reuses_result(self):
        e = self.event()
        first = self.assessed(e)
        other = Service(Repository(self.repo.path, self.config), clock=self.clock)
        retry = other.submit(e)
        self.assertEqual(first["snapshot"], retry["snapshot"])
        self.assertEqual(retry["revision"], 1)

    def test_unknown_classifier_does_not_fake_neutral_or_trust(self):
        r = self.service.submit(self.event())
        self.assertEqual(r["stimulus"], "unknown")
        self.assertEqual(r["classify_source"], "unconfigured")
        self.assertEqual(r["relationships"]["p"]["trust"], .5)

    def test_invalid_model_evidence_has_no_effect(self):
        r = self.assessed(self.event(), evidence=("并不存在的原文",))
        self.assertEqual(r["classify_source"], "invalid_classifier_output")
        self.assertEqual(r["core_affect"]["valence"], 0)

    def test_failure_rolls_back_state_trace_and_response(self):
        e = self.event()
        def crash(_):
            raise sqlite3.OperationalError("injected_disk_failure")
        self.service.before_commit = crash
        with self.assertRaises(sqlite3.OperationalError):
            self.assessed(e)
        self.assertEqual(self.state()["revision"], 0)
        self.assertEqual(len(self.repo.export()["state_changes"]), 0)
        self.assertIsNone(self.repo.get_event("a", "m1")["response"])
        with self.repo.transaction() as c:
            c.execute("UPDATE events SET lease_until=0")
        self.service.before_commit = None
        self.service.recover_pending()
        self.assertEqual(self.state()["revision"], 1)
        self.assertEqual(self.state()["events_processed"], 1)

    def test_snapshot_is_read_only(self):
        self.assessed(self.event())
        before = self.repo.export()
        for _ in range(20):
            self.service.snapshot("a", self.clock.now() + 3600)
        self.assertEqual(before, self.repo.export())

    def test_technical_quotation_hypothesis_and_negation(self):
        cases = [("代码里把喜欢改成爱", "technical"), ("她说我讨厌你，我在引用台词", "quote"),
                 ("假如有人说不想理你", "hypothetical"), ("我不难过，这只是一个例子", "hypothetical")]
        for i, (text, act) in enumerate(cases):
            r = self.assessed(self.event(str(i), text), kind="loss", observed_affect="sad", speech_act=act)
            self.assertEqual(r["core_affect"]["valence"], 0)
            self.assertEqual(r["relationships"]["p"]["trust"], .5)

    def test_other_person_loss_is_concern_without_distrust(self):
        r = self.assessed(self.event(text="我考试没过，很难过"), kind="loss", target="actor",
                          observed_affect="sad", agency="external")
        self.assertIn("关切", r["emotions"])
        self.assertEqual(r["relationships"]["p"]["trust"], .5)
        self.assertEqual(r["last_appraisal"]["trust_delta"], 0)

    def test_direct_harm_has_bounded_negative_effect(self):
        r = self.assessed(self.event(text="我不想再和你说话"), kind="rejection", observed_affect="angry")
        self.assertLess(r["core_affect"]["valence"], 0)
        self.assertLess(r["relationships"]["p"]["trust"], .5)
        self.assertGreater(r["relationships"]["p"]["trust"], .45)

    def test_external_event_does_not_touch_user_relationship(self):
        self.assessed(self.event())
        relation = copy.deepcopy(self.state()["relationships"]["p"])
        self.assessed(self.event("world", "游戏输了", actor_id=None, target_id=None,
                                 source="external_event", event_type="external_experience", provenance="observed_event"),
                      kind="loss", agency="external", target="actor", observed_affect="sad")
        self.assertEqual(relation, self.state()["relationships"]["p"])

    def test_reflection_is_not_additional_relationship_evidence(self):
        self.assessed(self.event())
        r = self.assessed(self.event("recall", source="self_reflection", provenance="self_reflection",
                                     event_type="recall", causation_id="m1"))
        self.assertEqual(r["relationships"]["p"]["evidence_count"], 1)
        self.assertEqual(r["relationships"]["p"]["contact_count"], 1)

    def test_recall_requires_known_original(self):
        with self.assertRaises(DomainError):
            self.service.submit(self.event(event_type="recall", source="self_reflection", provenance="self_reflection", causation_id="missing"))

    def test_people_are_separate(self):
        self.assessed(self.event())
        r = self.assessed(self.event("p2", actor_id="other"), kind="rejection")
        self.assertGreater(r["relationships"]["p"]["trust"], .5)
        self.assertLess(r["relationships"]["other"]["trust"], .5)

    def test_low_confidence_cannot_penalize_trust(self):
        r = self.assessed(self.event(), kind="rejection", certainty=.3)
        self.assertEqual(r["relationships"]["p"]["trust"], .5)

    def test_time_partition_invariance(self):
        self.assessed(self.event())
        s = self.state()
        direct = advance(s, self.clock.now() + 86400, self.config)
        steps = s
        for i in range(1, 241):
            steps = advance(steps, self.clock.now() + i * 360, self.config)
        for key in ("fast_valence", "fast_arousal", "mood_valence", "mood_arousal", "stress"):
            self.assertAlmostEqual(direct[key], steps[key], places=12)

    def test_clock_reversal_never_reverses_state_time(self):
        self.assessed(self.event())
        before = self.state()
        after = advance(before, self.clock.now() - 86400, self.config)
        self.assertEqual(before, after)

    def test_recovered_state_does_not_keep_old_label(self):
        self.assessed(self.event(), kind="rejection")
        self.clock.advance(7 * 86400)
        self.assertEqual(self.service.snapshot("a")["emotions"], ["平静"])
        self.assertEqual(self.state()["relationships"]["p"]["evidence_count"], 1)

    def add_expectation(self, eid="x1", mutual=False):
        return self.service.submit(self.event("add:" + eid, "", source="system", provenance="operator_assertion", event_type="expect_add",
                 payload={"id": eid, "expect": "明确的反馈", "mutual": mutual, "deadline": iso(self.clock.now() + 3600)}))

    def resolve(self, eid="x1", result="fulfilled", eventid="resolve1"):
        return self.service.submit(self.event(eventid, "", source="system", provenance="operator_assertion",
                 event_type="expect_resolve", payload={"id": eid, "result": result}))

    def test_expectation_expiry_unknown_no_trust_penalty(self):
        self.assessed(self.event())
        self.add_expectation(mutual=True)
        trust = self.state()["relationships"]["p"]["trust"]
        self.clock.advance(7200)
        self.assertEqual(self.service.settle_due(), 1)
        self.assertEqual(self.service.settle_due(), 0)
        self.assertEqual(self.state()["expectations"]["x1"]["status"], "expired_unknown")
        self.assertEqual(self.state()["relationships"]["p"]["trust"], trust)

    def test_expectation_invalid_id_does_not_settle_first(self):
        self.add_expectation()
        r = self.resolve("wrong")
        self.assertFalse(r["success"])
        self.assertEqual(self.state()["expectations"]["x1"]["status"], "pending")

    def test_expectation_settlement_only_once(self):
        self.add_expectation(mutual=True)
        r1 = self.resolve()
        r2 = self.resolve(eventid="resolve2")
        self.assertEqual(r1["relationships"], r2["relationships"])
        self.assertEqual(r2["last_appraisal"]["reason"], "already_settled_no_effect")

    def test_unknown_outcome_can_be_refined(self):
        self.add_expectation(mutual=True)
        self.clock.advance(7200)
        self.service.settle_due()
        self.resolve(result="fulfilled")
        self.assertEqual(self.state()["expectations"]["x1"]["status"], "fulfilled")

    def test_unilateral_unmet_does_not_deduct_trust(self):
        self.assessed(self.event())
        trust = self.state()["relationships"]["p"]["trust"]
        self.add_expectation()
        r = self.resolve(result="unmet")
        self.assertEqual(r["relationships"]["p"]["trust"], trust)

    def test_database_restart_preserves_state(self):
        self.assessed(self.event())
        reopened = Repository(self.repo.path, self.config)
        self.assertEqual(self.state(), reopened.load_state("a", self.clock.now()))

    def test_config_mismatch_refuses_silent_reset(self):
        self.assessed(self.event())
        other = Repository(self.repo.path, replace(self.config, version="different"))
        with self.assertRaises(DomainError):
            other.load_state("a", self.clock.now())

    def test_corrupt_state_refuses_silent_reset(self):
        self.assessed(self.event())
        with self.repo.transaction() as c:
            c.execute("UPDATE agent_states SET state='{' WHERE agent_id='a'")
        with self.assertRaises(DomainError):
            self.state()

    def test_backup_preserves_latest_events(self):
        self.assessed(self.event())
        backup = Path(self.tmp.name) / "backup.sqlite3"
        self.repo.backup(backup)
        restored = Repository(backup, self.config)
        self.assertEqual(self.state(), restored.load_state("a", self.clock.now()))
        with self.assertRaises(DomainError):
            self.repo.backup(backup)

    def test_outbox_atomic_claim_and_unknown_delivery(self):
        self.repo.queue_action("wf:slot", {"message": "test"}, self.clock.now())
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            tokens = list(pool.map(lambda _: self.repo.claim_action("wf:slot", self.clock.now()), range(20)))
        winner = [x for x in tokens if x]
        self.assertEqual(len(winner), 1)
        self.repo.settle_action("wf:slot", winner[0], "unknown_delivery", {}, self.clock.now())
        self.assertIsNone(self.repo.claim_action("wf:slot", self.clock.now() + 86400))

    def test_outbox_same_id_content_conflict(self):
        self.repo.queue_action("x", {"message": "a"}, self.clock.now())
        with self.assertRaises(DomainError):
            self.repo.queue_action("x", {"message": "b"}, self.clock.now())

    def test_legacy_id_retries_keep_timestamp_and_identity(self):
        a, supported = legacy_event(self.service, "/emotion", {"event_id": "old", "msg": "谢谢你"})
        self.assertTrue(supported)
        self.service.submit(a)
        self.clock.advance(100)
        b, _ = legacy_event(self.service, "/emotion", {"event_id": "old", "msg": "谢谢你"})
        self.assertEqual(a, b)

    def test_legacy_missing_id_reports_no_cross_call_dedupe(self):
        a, supported = legacy_event(self.service, "/emotion", {"msg": "same"})
        b, _ = legacy_event(self.service, "/emotion", {"msg": "same"})
        self.assertFalse(supported)
        self.assertNotEqual(a["event_id"], b["event_id"])

    def test_legacy_concurrent_first_arrivals_reuse_default_time_atomically(self):
        # Force both adapters to run before either reservation; clocks differ.
        for path in ("/emotion", "/expect"):
            barrier = threading.Barrier(2)
            services = [Service(Repository(self.repo.path, self.config),
                                clock=FrozenClock(self.clock.now() + offset)) for offset in (0, 1)]
            body = {"event_id": path, "msg": "谢谢你", "expect": "答复", "ttl": 2}
            def synchronized_adapter(service, route, raw):
                event = legacy_event(service, route, raw)
                barrier.wait(timeout=5)
                return event
            with patch("endocrine.api.legacy_event", synchronized_adapter):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(lambda s: submit_legacy(s, path, body), services))
            self.assertTrue(all(r["success"] for r in results))
            self.assertEqual(sum(r["deduplicated"] for r in results), 1)
            self.assertEqual(results[0]["snapshot"], results[1]["snapshot"])

    def test_legacy_defaults_do_not_hide_changed_text_ttl_or_explicit_time(self):
        body = {"event_id": "legacy-expect", "expect": "答复", "ttl": 2}
        submit_legacy(self.service, "/expect", body)
        for changed in ({"ttl": 3}, {"expect": "其他约定"},
                        {"occurred_at": iso(self.clock.now() + 1)}):
            with self.assertRaises(DomainError) as exc:
                submit_legacy(self.service, "/expect", {**body, **changed})
            self.assertEqual(exc.exception.status, 409)

    def test_timestamp_timezone_and_numbers_validation(self):
        with self.assertRaises(DomainError):
            timestamp("2026-10-16 10:00:00")
        with self.assertRaises(DomainError):
            Assessment.parse({"certainty": float("nan")}, "x")

    def test_future_event_rejected_before_reservation(self):
        with self.assertRaises(DomainError):
            self.service.submit(self.event(occurred_at=iso(self.clock.now() + 3600)))
        self.assertEqual(self.repo.export()["events"], [])

    def test_long_sequence_thirty_days_stable(self):
        for i in range(180):
            self.clock.advance(4 * 3600)
            self.assessed(self.event(str(i)), kind="gratitude" if i % 3 else "rejection")
        s = self.state()
        for field in ("fast_valence", "fast_arousal", "mood_valence", "mood_arousal", "stress"):
            self.assertTrue(math.isfinite(s[field]))
            self.assertLessEqual(abs(s[field]), 1)
        self.assertLess(len(s["habits"]), 20)

    def test_http_auth_errors_and_read_only_query(self):
        server = make_server(self.service, port=0, token="test-only")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}"
            with self.assertRaises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(url + "/snapshot", timeout=3)
            self.assertEqual(exc.exception.code, 403)
            req = urllib.request.Request(url + "/v2/events", json.dumps(self.event()).encode(), {"Content-Type": "application/json", "X-Token": "test-only"})
            with urllib.request.urlopen(req, timeout=3) as r:
                data = json.load(r)
            self.assertTrue(data["success"])
            before = self.repo.export()
            req = urllib.request.Request(url + "/snapshot?agent_id=a", headers={"X-Token": "test-only"})
            with urllib.request.urlopen(req, timeout=3) as r:
                self.assertEqual(json.load(r)["persisted_revision"], 1)
            self.assertEqual(before, self.repo.export())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
