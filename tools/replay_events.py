import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from endocrine.core import apply, initial_state
from endocrine.model import Assessment, Config, Event, canonical


def replay(export, config=None):
    config = config or Config()
    if export["config_signature"] != config.signature:
        raise ValueError("Configuration mismatch: do not replay under different parameters")
    states = {s["agent_id"]: s for s in export.get("initial_states", [])}
    import copy
    states=copy.deepcopy(states)
    for row in sorted(export["events"], key=lambda e: e["seq"]):
        if row["status"] != "applied":
            continue
        event = Event.parse(json.loads(row["body"]))
        assessment = Assessment.parse(json.loads(row["assessment"]), event.text)
        state = states.get(event.agent_id) or initial_state(event.agent_id, row["received_at"], config)
        states[event.agent_id], _ = apply(state,event,assessment,row.get("effective_at") or row["received_at"],row["seq"],config,wall_at=row["received_at"])
    return [states[k] for k in sorted(states)]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("export", type=Path)
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    data = json.loads(args.export.read_text(encoding="utf-8"))
    states = replay(data)
    expected = sorted(data["states"], key=lambda s: s["agent_id"])
    if canonical(states) != canonical(expected):
        raise SystemExit("Replay mismatch")
    if args.output:
        args.output.write_text(json.dumps(states, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Replay matched", len(states), "agents; no model or network calls")


if __name__ == "__main__":
    main()
