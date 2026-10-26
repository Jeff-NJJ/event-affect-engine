"""Readable indexes over preserved v3/v4 archives; never reapply old stimuli."""
import json
from .model import canonical


def legacy_memory_records(archive,state):
    agent=state["agent_id"]
    graph=archive.get("graph",archive)
    mapping=state.get("legacy",{}).get("person_mapping",{})
    if graph.get("format")=="legacy-inert-graph-v1":
        objects=graph["objects"]
        def decode(value,depth=0):
            if depth>12:
                return None
            if not isinstance(value,dict) or "$ref" not in value:
                return value
            node=objects[value["$ref"]];kind=node.get("kind")
            if kind in {"list","tuple","set"}:
                return [decode(v,depth+1) for v in node["items"]]
            if kind=="dict":
                return {str(decode(k,depth+1)):decode(v,depth+1) for k,v in node["items"]}
            return None
        for ref,node in objects.items():
            if node.get("type")!="databrain.memory.Event":
                continue
            fields={k:decode(v) for k,v in node.get("fields",{}).items()}
            serialized=fields.get("serialized_state")
            if isinstance(serialized,list):
                for slot in serialized:
                    if isinstance(slot,dict):
                        fields.update(slot)
            text=fields.get("text")
            has_text=isinstance(text,str) and bool(text)
            text=text if isinstance(text,str) else ""
            subject=fields.get("subject")
            salience=fields.get("salience",0)
            salience=float(salience) if isinstance(salience,(int,float)) else 0
            # Graph references are unique even if legacy IDs repeat or are absent.
            yield {"agent_id":agent,"event_id":"legacy:v3:"+ref,"origin":"legacy_v3",
                "actor_id":mapping.get(subject),"conversation_id":None,"occurred_at":None,"text":text,
                "assessment":None,"salience":max(0,min(1,salience)),"provenance":"legacy_observed_unverified_time" if has_text else "legacy_without_text_unverified_time",
                "original_ref":"legacy_graph:"+ref}
    old=archive.get("v4_export")
    if old:
        from .model import timestamp
        for row in old.get("events",[]):
            if row["status"]!="applied":
                continue
            e=json.loads(row["body"])
            if e["source"] not in {"user_message","external_event"} or e["event_type"] not in {"utterance","external_experience"}:
                continue
            a=json.loads(row["assessment"]) if row["assessment"] else {}
            yield {"agent_id":agent,"event_id":"legacy:v4:"+e["event_id"],"origin":"legacy_v4","actor_id":e.get("actor_id"),
                "conversation_id":e.get("conversation_id"),"occurred_at":timestamp(e["occurred_at"]),"text":e["text"],
                "assessment":canonical(a),"salience":a.get("relevance",0)*a.get("intensity",0),
                "provenance":e["provenance"],"original_ref":"v4_export:events:"+e["event_id"]}
        for row in old.get("legacy_archives",[]):
            nested=json.loads(row["graph"])
            if nested.get("format")=="legacy-inert-graph-v1":
                yield from legacy_memory_records(nested,state)
