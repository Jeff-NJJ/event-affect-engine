"""Restricted inert export: never imports or constructs original DataBrain classes."""
import argparse
import hashlib
import io
import json
import pickle
import pickletools
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from endocrine.core import _relationship, _timezone, initial_state
from endocrine.model import Config, digest, timestamp
from endocrine.store import Repository

ALLOWED = {
    ("databrain.engine", "DataBrain"), ("databrain.state", "HormonalSystem"),
    ("databrain.state", "SlowState"), ("databrain.state", "DispositionSystem"),
    ("databrain.memory", "Ledger"), ("databrain.memory", "Event"),
    ("databrain.memory", "MemoryBucket"), ("databrain.memory", "ReconsolidationEngine"),
    ("databrain.human", "HumanLayer"), ("databrain.agentface", "AgentFace"),
    ("databrain.agentface", "DelayedReleaser"), ("random", "Random"),
}


class Inert:
    def __init__(self, *args, **kwargs):
        self.fields = {}
        self.constructor_args = args

    def __new__(cls, *args, **kwargs):
        obj = object.__new__(cls)
        obj.fields = {}
        obj.constructor_args = args
        return obj

    def __setstate__(self, state):
        self.fields = state if isinstance(state, dict) else {"serialized_state": state}


class InertUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if (module, name) not in ALLOWED:
            raise ValueError("Unapproved serialized class: " + module + "." + name)
        return type(name, (Inert,), {"original_type": module + "." + name})

    def persistent_load(self, pid):
        raise ValueError("Persistent pickle references are not supported")


def inert_load(data):
    if len(data) > 10_000_000:
        raise ValueError("Legacy input too large")
    for count, (op, _, _) in enumerate(pickletools.genops(data)):
        if count > 1_000_000 or op.name in {"EXT1", "EXT2", "EXT4", "PERSID", "BINPERSID"}:
            raise ValueError("Unsupported pickle opcode")
    return InertUnpickler(io.BytesIO(data)).load()


def encode_graph(root):
    objects, seen = {}, {}
    def enc(value):
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        key = id(value)
        if key in seen:
            return {"$ref": seen[key]}
        ref = str(len(seen) + 1)
        seen[key] = ref
        objects[ref] = {}
        if isinstance(value, Inert):
            node = {"kind": "object", "type": value.original_type, "fields": {k: enc(v) for k, v in value.fields.items()},
                    "constructor_args": enc(value.constructor_args)}
        elif isinstance(value, dict):
            node = {"kind": "dict", "items": [[enc(k), enc(v)] for k, v in value.items()]}
        elif isinstance(value, (list, tuple, set)):
            node = {"kind": type(value).__name__, "items": [enc(v) for v in value]}
        else:
            raise ValueError("Unsupported value type: " + type(value).__name__)
        objects[ref] = node
        return {"$ref": ref}
    reference = enc(root)
    return {"format": "legacy-inert-graph-v1", "root": reference, "objects": objects}


def migrate(data, now, agent_id="example_agent", primary_person=None, ledger=None, legacy_timezone=None):
    brain = inert_load(data)
    if not isinstance(brain, Inert) or brain.original_type != "databrain.engine.DataBrain":
        raise ValueError("Expected serialized DataBrain")
    config = Config()
    state = initial_state(agent_id, now, config)
    graph = encode_graph(brain)
    root = brain.fields
    human = root.get("human")
    if isinstance(human, Inert):
        v, a = float(human.fields.get("emo_v", .5)), float(human.fields.get("emo_a", .25))
        if not 0 <= v <= 1 or not 0 <= a <= 1:
            raise ValueError("Unexpected legacy affect range")
        state["fast_valence"], state["fast_arousal"] = v * 2 - 1, max(-.2, a - .25)
    people = root.get("person_buckets", {})
    mapping = {}
    for name, bucket in people.items():
        if str(name).startswith("外部_"):
            continue
        person_id = "example_user" if name == primary_person else "legacy-person:" + hashlib.sha256(str(name).encode()).hexdigest()[:16]
        mapping[name] = person_id
        relation = _relationship(state, person_id, config)
        if isinstance(bucket, Inert):
            relation["legacy_prior_valence"] = bucket.fields.get("valence")
            relation["legacy_prior_semantics"] = "historical_appraisal_not_calibrated_trust"
    needs_review = 0
    for item in (ledger or {}).get("items", []):
        eid = str(item["id"])
        status = {"lapsed": "unmet"}.get(item.get("status"), item.get("status", "pending"))
        deadline = None
        if legacy_timezone is not None:
            base = datetime(2026, 9, 23, tzinfo=_timezone(legacy_timezone)).timestamp()
            deadline = base + (float(item["t"]) + float(item.get("ttl") or 72)) * 3600
        if status == "pending" and deadline is None:
            status = "needs_review"
            needs_review += 1
        state["expectations"][eid] = {"id": eid, "expect": item.get("expect", ""),
             "actor_id": "example_user" if primary_person else None, "mutual": False, "importance": .4,
             "deadline": deadline, "status": status, "created_at": now,
             "created_event_id": "legacy-import", "settled_event_id": None, "legacy": item}
    state["legacy"] = {"archive_sha256": digest(graph), "source_sha256": hashlib.sha256(data).hexdigest(),
                       "events_processed_before_migration": root.get("events_processed"),
                       "person_mapping": mapping, "migration_at": now,
                       "unmapped_mechanisms": ["legacy hormones", "themes", "delayed releases", "expectation disappointment"]}
    report = {"source_sha256": hashlib.sha256(data).hexdigest(), "archived_graph_objects": len(graph["objects"]),
              "people_mapped": len(mapping), "legacy_events": root.get("events_processed"),
              "expectations_preserved": len(state["expectations"]), "expectations_needing_time_review": needs_review,
              "legacy_timezone": legacy_timezone, "warning": "Legacy graph is preserved, not fully behaviourally mapped. No old events reapplied."}
    return state, graph, report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("pickle", type=Path)
    p.add_argument("new_db", type=Path)
    p.add_argument("--as-of", required=True, help="new baseline timestamp including timezone")
    p.add_argument("--agent-id", default="example_agent")
    p.add_argument("--primary-person")
    p.add_argument("--ledger", type=Path)
    p.add_argument("--legacy-timezone", help="Only set after verifying old server timezone")
    p.add_argument("--report", type=Path)
    args = p.parse_args()
    if args.new_db.exists():
        raise SystemExit("Refusing to overwrite an existing database")
    ledger = json.loads(args.ledger.read_text(encoding="utf-8")) if args.ledger else None
    state, graph, report = migrate(args.pickle.read_bytes(), timestamp(args.as_of), args.agent_id,
                                  args.primary_person, ledger, args.legacy_timezone)
    repo = Repository(args.new_db, Config())
    repo.import_state(state, graph)
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
