"""HTTP calls isolated in a bounded child process with a hard total deadline."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

SLOTS=threading.BoundedSemaphore(4)


class TransportError(OSError):
    pass


def request_json(url, data=None, headers=None, *, timeout=30.0, max_bytes=1048576):
    start=time.monotonic()
    if not SLOTS.acquire(timeout=max(0,timeout)):
        raise TimeoutError('transport_capacity_deadline')
    process=None
    try:
        remaining=timeout-(time.monotonic()-start)
        if remaining<=0:raise TimeoutError('transport_total_deadline')
        command=[sys.executable,'-B',str(Path(__file__).with_name('transport_worker.py'))]
        payload=json.dumps({'url':url,'data':data,'headers':headers or {},'timeout':remaining,'max_bytes':max_bytes},allow_nan=False).encode()
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        output,_=process.communicate(payload,timeout=max(.001,timeout-(time.monotonic()-start)))
        if process.returncode==124:raise TimeoutError('transport_total_deadline')
        if process.returncode!=0:raise TransportError('transport_worker_failed')
        result=json.loads(output)
        if not result.get('ok'):
            if result.get('kind')=='timeout':raise TimeoutError('transport_total_deadline')
            raise TransportError('provider_http_'+str(result.get('code','unavailable')))
        return result['value']
    except subprocess.TimeoutExpired:
        raise TimeoutError('transport_total_deadline') from None
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        SLOTS.release()
