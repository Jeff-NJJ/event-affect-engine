"""Export the prior Shanghai calendar day's same-ID derived archive material."""
import argparse
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ob_bridge.store import ArchiveStore
from ob_bridge.service import BridgeService


def interval(now):
    local=now.astimezone(ZoneInfo('Asia/Shanghai'))
    end=local.replace(hour=0,minute=0,second=0,microsecond=0)
    return (end-timedelta(days=1)).astimezone(timezone.utc),end.astimezone(timezone.utc)


def main():
    p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--output-dir',required=True);args=p.parse_args()
    os.umask(0o077)
    start,end=interval(datetime.now(timezone.utc))
    result=BridgeService(None,ArchiveStore(args.db)).consolidation(start.isoformat(),end.isoformat())
    root=Path(args.output_dir);root.mkdir(mode=0o700,parents=True,exist_ok=True)
    target=root/(start.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()+'.json')
    temp=target.with_name(target.name+'.tmp-'+str(os.getpid()))
    with temp.open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
    temp.replace(target)
    print(json.dumps({'success':True,'date':target.stem,'items':len(result['items']),
        'material_id':result['material_id'],'memory_writes_performed':0,'messages_sent':0}))

if __name__=='__main__':main()
