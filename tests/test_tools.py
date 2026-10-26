import copy
import json
import pickle
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch as mock_patch

from endocrine.clock import FrozenClock
from endocrine.model import Config, DomainError, Event, iso
from endocrine.service import Service
from endocrine.store import Repository
from tools.migrate_state import inert_load, migrate
from tools.patch_workflows import guard_command, patch
from tools.replay_events import replay


class ToolTests(unittest.TestCase):
    def test_replay_uses_saved_assessments_not_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = FrozenClock(1792141200)
            repo = Repository(Path(tmp) / "test.db", Config())
            service = Service(repo, clock=clock)
            for i in range(5):
                service.submit({"agent_id": "a", "event_id": str(i), "occurred_at": iso(clock.now()), "text": "x"})
                clock.advance(60)
            data = repo.export()
            self.assertEqual(data["states"], replay(data))

    def test_replay_rejects_config_change(self):
        with self.assertRaises(ValueError):
            replay({"config_signature": "different"})

    def test_pickle_global_execution_is_rejected(self):
        class Unsafe:
            def __reduce__(self):
                return eval, ("1+1",)
        with self.assertRaises(ValueError):
            inert_load(pickle.dumps(Unsafe()))

    def test_migration_preserves_graph_and_replays_new_events(self):
        package = types.ModuleType("databrain")
        engine, human = types.ModuleType("databrain.engine"), types.ModuleType("databrain.human")
        Brain, Human = type("DataBrain", (), {}), type("HumanLayer", (), {})
        Brain.__module__, Human.__module__ = engine.__name__, human.__name__
        engine.DataBrain, human.HumanLayer = Brain, Human
        package.engine, package.human = engine, human
        with mock_patch.dict(sys.modules, {"databrain": package, engine.__name__: engine, human.__name__: human}):
            b = Brain()
            b.human, b.person_buckets, b.events_processed = Human(), {}, 7
            b.human.emo_v, b.human.emo_a = .7, .3
            b.valuable_unmapped_memory = "keep this"
            raw = pickle.dumps(b)
        state, graph, report = migrate(raw, 1792141200, agent_id="a", ledger={"items": [
            {"id": 9, "expect": "待确认", "status": "pending", "t": 1, "ttl": 72}]})
        self.assertEqual(report["legacy_events"], 7)
        self.assertEqual(state["expectations"]["9"]["status"], "needs_review")
        self.assertIn("keep this", json.dumps(graph))
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(Path(tmp) / "new.db", Config())
            repo.import_state(state, graph)
            Service(repo, clock=FrozenClock(1792141200)).submit({"agent_id": "a", "event_id": "new", "occurred_at": iso(1792141200), "text": "hello"})
            self.assertEqual(repo.export()["states"], replay(repo.export()))
            self.assertEqual(len(repo.export()["legacy_archives"]), 1)
            backup = Path(tmp) / "rollback.db"
            repo.backup(backup)
            self.assertEqual(repo.export()["events"], Repository(backup, Config()).export()["events"])

    def template(self):
        return {"id": "wf", "nodes": [{"id": "trigger", "triggerConfig": {"schedule_type": "specific_time", "specific_time": "2026-10-16 17:55"}},
                    {"id": "send", "actionType": "send_message_to_ai"}],
                "connections": [{"id": "edge", "sourceNodeId": "trigger", "targetNodeId": "send", "condition": "on_success"}]}

    def test_guard_dominates_send_and_requires_positive_match(self):
        original = self.template()
        changed = patch(original)
        self.assertEqual(len(original["nodes"]), 2)
        condition = next(n for n in changed["nodes"] if n["id"] == "guard_v4_condition")
        self.assertEqual(condition["operator"], "EQ")
        self.assertEqual(condition["right"]["value"], "GO")
        incoming = [c for c in changed["connections"] if c["targetNodeId"] == "send"]
        self.assertEqual([(c["sourceNodeId"], c["condition"]) for c in incoming], [("guard_v4_condition", "true")])
        with self.assertRaises(ValueError):
            patch(changed)

    def test_guard_uses_stable_occurrence_and_avoids_echo_false_match(self):
        a, b = patch(self.template()), patch(self.template())
        command = next(n for n in a["nodes"] if n["id"] == "guard_v4_exec")["actionConfig"]["command"]["value"]
        self.assertNotIn("WF_GUARD_GO", command)
        self.assertIn("mkdir", command)
        self.assertNotIn("rm -", command)
        self.assertEqual(a, b)
        other = self.template()
        other["nodes"][0]["triggerConfig"]["specific_time"] = "2026-10-17 17:55"
        self.assertNotEqual(patch(other), a)

    def test_untrusted_types_have_validation_errors(self):
        raw = {"agent_id": "a", "event_id": "b", "occurred_at": iso(1792141200), "source": []}
        with self.assertRaises(DomainError):
            Event.parse(raw)
        raw["source"] = "user_message"
        raw["payload"] = {"nan": float("nan")}
        with self.assertRaises(DomainError):
            Event.parse(raw)


if __name__ == "__main__":
    unittest.main()
