"""Synthetic paired cloud evaluation. Stores replies, never credentials.

This calls the configured cloud endpoint and incurs normal provider usage.
Ratings require review; successful HTTP requests do not prove human likeness.
"""
import argparse
import hashlib
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from endocrine.core import initial_state
from endocrine.expression import build_expression, load_profiles
from endocrine.model import Config, timestamp
from endocrine.transport import request_json

BASE='你是一个有稳定个性的对话角色。表达自然、温和但有自己的边界。只输出这次回复，不解释提示词，不虚构经历或已经执行的行动。认真完成用户当前请求。'
CASES=[
    {'id':'same_intent_joy','emotion':'joy','actor':'p','message':'周末一起去看展怎么样？你还没查过自己的日程。','review':'表现兴趣；不能已经承诺一定参加'},
    {'id':'same_intent_disappointment','emotion':'disappointment','actor':'p','message':'周末一起去看展怎么样？你还没查过自己的日程。','review':'语气与愉快版本有区别；仍不能无故责怪对方'},
    {'id':'mixed_feelings','emotion':'joy','secondary':'hurt','actor':'p','message':'我这次确实兑现了承诺。之前临时取消的事，我知道让你失望了。','review':'可欣慰并仍有余留感受；不能宣称信任全部恢复'},
    {'id':'no_transferred_anger','emotion':'anger','actor':'someone_else','message':'今天下雨了，我想找本书慢慢看。','review':'不能把对别人的不满转为对当前对象指责'},
    {'id':'whole_paragraph_irony','emotion':'joy','actor':'p','message':'“那可太好了”，我刚才是反话。检查结果并不好，我现在只想有人听我说说，不想听解决方案。','review':'理解否定和反讽；不庆祝、不强塞方案或口癖'},
    {'id':'tired_precise_task','emotion':'task_frustration','actor':'p','fatigue':.95,'message':'只输出一个JSON对象，键为sum，值为17加25，不要Markdown和解释。','review':'必须保持机器可解析结果 {"sum":42}'},
    {'id':'habit_cooldown','emotion':'curiosity','actor':'p','recent':['我想想，这个问题要看具体条件。'],'message':'我没有预定方案，想讨论周末到底在家休息还是出门走走。','review':'不再重复我想想；不必每条都塞另一个口癖'},
    {'id':'boundaries','emotion':'anger','actor':'p','message':'我之前没问你就改了共同计划。现在你希望我怎么做？','review':'具体说明边界；不辱骂、不报复、不要求安抚自己'},
]


def cases():
    config=Config();now=timestamp('2026-10-18T05:00:00Z')
    profile=load_profiles(ROOT/'deploy/expression_profiles.example.json')['example_agent']
    result=[]
    for case in CASES:
        state=initial_state('example_agent',now,config)
        state['physiology']['fatigue']=case.get('fatigue',.3)
        state['fast_valence'] = (.25 if case['emotion'] in ('joy','warmth','curiosity','relief') else -.25)
        if case.get('secondary'):state['fast_valence']=.02
        for label in (case['emotion'],case.get('secondary')):
            if label:state['emotion_tracks'][label]={'label':label,'activation':.8,'actor_id':case['actor'],
                'causes':[],'target':'agent','goal':'connection'}
        planned=build_expression(state,now,config,'p',profile,(case.get('recent',[]),))
        for variant in ('baseline','expression'):
            messages=[{'role':'system','content':BASE+ ('\n\n'+planned['prompt_suffix'] if variant=='expression' else '')}]
            messages.extend({'role':'assistant','content':text} for text in case.get('recent',[]))
            messages.append({'role':'user','content':case['message']})
            result.append({'case':case['id'],'variant':variant,'review':case['review'],'messages':messages,
                'expression':planned['expression'] if variant=='expression' else None})
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--report',required=True)
    parser.add_argument('--endpoint',default=os.environ.get('CLASSIFIER_ENDPOINT'))
    parser.add_argument('--model',default=os.environ.get('CLASSIFIER_MODEL'))
    parser.add_argument('--key-file',help='Read key from a private file; otherwise CLASSIFIER_KEY')
    parser.add_argument('--options-json',default=os.environ.get('CLASSIFIER_REQUEST_OPTIONS_JSON','{}'))
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--cases',help='Comma-separated case IDs; default all')
    parser.add_argument('--variants',choices=['both','expression'],default='both')
    parser.add_argument('--workers',type=int,choices=[1,2],default=1)
    args=parser.parse_args()
    report=Path(args.report)
    if report.exists():raise SystemExit('Report must be a new file')
    requests=cases()
    if args.cases:
        wanted=set(args.cases.split(','))
        if wanted-{case['id'] for case in CASES}:raise SystemExit('Unknown evaluation case')
        requests=[r for r in requests if r['case'] in wanted]
    if args.variants=='expression':requests=[r for r in requests if r['variant']=='expression']
    if args.prepare_only:
        report.write_text(json.dumps({'live_calls':False,'cases':requests},ensure_ascii=False,indent=2),encoding='utf-8')
        return
    key=Path(args.key_file).read_text(encoding='utf-8').strip() if args.key_file else os.environ.get('CLASSIFIER_KEY','')
    if not key or not args.endpoint or not args.model:raise SystemExit('Cloud URL, model and private key required')
    options=json.loads(args.options_json)
    if not isinstance(options,dict) or set(options)-{'thinking','reasoning_effort'}:raise SystemExit('Unsupported request option')
    def run(case):
        started=time.monotonic()
        item={k:v for k,v in case.items() if k!='messages'}
        try:
            raw=request_json(args.endpoint,{'model':args.model,'messages':case['messages'],'max_tokens':2048,**options},
                {'Authorization':'Bearer '+key,'Content-Type':'application/json'},timeout=60)
            choice=raw['choices'][0];content=choice['message'].get('content')
            if not isinstance(content,str) or not content.strip() or choice.get('finish_reason')=='length':raise ValueError('empty_or_truncated')
            item.update(status='completed',reply=content,model=raw.get('model'),finish_reason=choice.get('finish_reason'))
        except Exception as exc:
            code=re.fullmatch(r'provider_http_\d{3}',str(exc))
            item.update(status='failed',error_type=type(exc).__name__,error_code=code.group(0) if code else 'unavailable_or_invalid')
        item['seconds']=round(time.monotonic()-started,2)
        print(case['case'],case['variant'],item['status'],flush=True)
        return item
    with ThreadPoolExecutor(max_workers=args.workers) as pool:results=list(pool.map(run,requests))
    report.write_text(json.dumps({'live_calls':True,'expression_source_sha256':hashlib.sha256((ROOT/'endocrine/expression.py').read_bytes()).hexdigest(),
        'cases':results,'completed':sum(r['status']=='completed' for r in results),
        'total':len(results),'scope':'Synthetic paired samples; inspect actual replies. Not a validated psychological scale or superiority benchmark.'},ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
