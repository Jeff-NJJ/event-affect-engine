"""Mechanism comparison only. No claimed human fit or automatic model selection."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from endocrine.core import initial_state,apply,advance,snapshot
from endocrine.facts import Fact
from endocrine.model import Config,Event,Assessment,iso,timestamp


def main():
    p=argparse.ArgumentParser();p.add_argument('--report',required=True);args=p.parse_args()
    path=Path(args.report)
    if path.exists():raise SystemExit('Use a new report')
    baseline=ROOT/'research/legacy_databrain'
    hashes=json.loads((ROOT/'research/legacy_source_hashes.json').read_text())
    for name,expected in hashes.items():
        if hashlib.sha256((baseline/name).read_bytes()).hexdigest()!=expected:raise SystemExit('Original baseline changed')
    spec=importlib.util.spec_from_file_location('preserved_databrain',baseline/'__init__.py',submodule_search_locations=[str(baseline)])
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    config=Config();now=timestamp('2026-10-18T00:00:00Z')
    legacy=module.DataBrain(seed=42);state=initial_state('synthetic',now,config)
    schedule=[(0,'neutral',0),(1,'affection',.6),(2,'affection',.6),(6,'criticism',-.6),(24,'apology',.1),(48,'neutral',0)]
    samples=[]
    for n,(hours,stim,effect) in enumerate(schedule):
        text='合成已标注情境 '+str(n)
        legacy.experience(hours,'synthetic-person',stim,text,intensity=.6,hour=(8+hours)%24)
        event=Event.parse({'agent_id':'synthetic','actor_id':'synthetic-person','event_id':'synthetic-'+str(n),
            'source':'user_message','occurred_at':iso(now+hours*3600),'text':text,'conversation_id':'synthetic'})
        fact=Fact.parse({'fact_id':'f','kind':stim,'evidence':[text],'target':'agent','agency':'actor',
            'speech_act':'direct','certainty':1,'relevance':1,'intensity':.6,
            'goal_effects':{'connection':effect}},text)
        state,_=apply(state,event,Assessment(facts=(fact,),classifier_status='synthetic_fixture'),now+hours*3600,n+1,config)
        old=legacy.snapshot(hours);new=snapshot(state,now+hours*3600,config)
        samples.append({'hours':hours,'stimulus_fixture':stim,'legacy_hormones':old['hormones'],
            'legacy_arousal':old['arousal'],'legacy_slow':old['slow'],
            'candidate_core_affect':new['core_affect'],'candidate_hormones_semantics':new['hormones_semantics']})
    result={'schema':'affect-architecture-comparison-v1','synthetic_only':True,'samples':samples,
        'original_source_hashes':hashes,'candidate_rule_version':config.version,
        'same_initialization_scale':False,'human_labels_available':False,'winner':None,
        'stimulus_mapping':'Manually authored fixtures, not inferred psychological ground truth',
        'interpretation':'Reproducible mechanism differences only. No superiority or psychological validity inference.',
        'production_model_replaced':False,'old_state_unpickled':False}
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Compared',len(samples),'synthetic points; no winning model inferred')


if __name__=='__main__':main()
