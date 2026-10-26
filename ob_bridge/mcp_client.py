import json
from pathlib import Path
import subprocess
import sys
import threading

from endocrine.model import DomainError, canonical
from .contracts import local_or_https

SLOTS=threading.BoundedSemaphore(2)


class MCPClient:
    def __init__(self,endpoint,*,headers=None,python=None):
        self.endpoint=local_or_https(endpoint);self.headers=dict(headers or {})
        self.python=python or sys.executable

    def request(self,operation,*,timeout=8,tool=None,schema_hash=None,arguments=None):
        if not SLOTS.acquire(blocking=False):raise DomainError('ob_capacity_exhausted',503)
        process=None
        try:
            payload={'endpoint':self.endpoint,'headers':self.headers,'operation':operation,
                'timeout':timeout,'tool':tool,'schema_hash':schema_hash,'arguments':arguments}
            process=subprocess.Popen([self.python,'-B','-m','ob_bridge.mcp_worker'],
                cwd=Path(__file__).resolve().parents[1],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
            output,_=process.communicate(canonical(payload).encode(),timeout=timeout+.5)
            if len(output)>300000:raise ValueError('output_limit')
            result=json.loads(output)
            if not result.get('ok'):raise DomainError(result.get('error','ob_unavailable'),503)
            return result['value']
        except (OSError,ValueError,subprocess.TimeoutExpired):raise DomainError('ob_unavailable',503) from None
        finally:
            if process is not None and process.poll() is None:
                process.kill();process.communicate(timeout=5)
            SLOTS.release()
