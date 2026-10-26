"""Offline mechanism comparison, not a claim of human realism or model accuracy."""
import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from endocrine.core import apply, initial_state, snapshot
from endocrine.model import Assessment, Config, Event, iso


def run(config):
    t0 = 1792141200.0
    state = initial_state("a", t0, config)
    trace = []
    seq = 0
    examples = {0:("loss", "actor", "sad", "external"), 4:("gratitude", "agent", "happy", "actor"),
                8:("rejection", "agent", "angry", "actor"), 12:("support", "agent", "happy", "actor")}
    for h in range(73):
        if h in examples:
            kind, target, affect, agency = examples[h]
            text = "离线场景" + str(h)
            event = Event.parse({"agent_id":"a","event_id":str(h),"occurred_at":iso(t0+h*3600),"text":text,
                                 "actor_id":"p","source":"user_message"})
            assessment = Assessment(kind=kind,target=target,observed_affect=affect,agency=agency,speech_act="direct",
                                    certainty=.9,relevance=.8,intensity=.6,evidence=(text,),classifier_status="fixture")
            seq += 1
            state, _ = apply(state,event,assessment,t0+h*3600,seq,config)
        view = snapshot(state,t0+h*3600,config)
        trace.append({"hour":h,"valence":view["core_affect"]["valence"],"arousal":view["core_affect"]["arousal"],
                      "mood":view["mood"]["valence"],"trust":view["relationships"].get("p",{}).get("trust",.5)})
    return trace


def main():
    p=argparse.ArgumentParser()
    p.add_argument("output",type=Path)
    args=p.parse_args()
    normal=Config()
    report={"semantics":"Deterministic fixture ablation; no online classifier accuracy or human realism score.",
            "with_mood":run(normal),"without_mood":run(replace(normal,mood_coupling_h=0,arousal_coupling_h=0))}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print("Offline comparison written: two mechanisms, 73 hourly projections each")


if __name__ == "__main__":main()
