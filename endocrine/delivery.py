"""Durable delivery with explicit acceptance, receipt and uncertainty reconciliation."""
import json
import urllib.request
from urllib.parse import quote

from .clock import lease_expired,lease_stamp
from .transport import request_json
from .model import DomainError, canonical, identity


class HTTPDeliveryAdapter:
    """Gateway contract documented in docs/delivery_protocol.md. No assumed native API."""
    def __init__(self, base, token):
        if not base.startswith("https://"):
            raise ValueError("HTTPS delivery gateway required")
        if not isinstance(token,str) or not token:
            raise ValueError("delivery gateway token required")
        self.base,self.token=base.rstrip('/'),token

    def request(self,path,body=None):
        return request_json(self.base+path,body,headers={"X-Token":self.token,"Content-Type":"application/json"},timeout=30,max_bytes=65536)

    def send(self,operation_id,payload):
        return self.request('/deliveries',{"operation_id":operation_id,"payload":payload})

    def lookup(self,operation_id):
        return self.request('/deliveries/'+quote(operation_id,safe=''))


class DeliveryService:
    def __init__(self,repo,adapter=None):
        self.repo,self.adapter=repo,adapter

    def queue(self,operation_id,payload):
        identity(operation_id,'operation_id')
        if not isinstance(payload,dict) or set(payload)!={"agent_id","conversation_id","text"}:
            raise DomainError("invalid_delivery_payload")
        identity(payload['agent_id'],'agent_id');identity(payload['conversation_id'],'conversation_id')
        if not isinstance(payload['text'],str) or not payload['text'].strip() or len(payload['text'])>16000:
            raise DomainError("invalid_delivery_text")
        return self.repo.queue_action(operation_id,payload,self.repo.lease_clock.wall())

    def get(self,operation_id):
        identity(operation_id,'operation_id')
        with self.repo.connect() as c:
            row=c.execute('SELECT * FROM outbox WHERE operation_id=?',(operation_id,)).fetchone()
        if not row:
            raise DomainError('delivery_not_found',404)
        return dict(row)

    def view(self,operation_id):
        row=self.get(operation_id)
        return {"success":True,"operation_id":operation_id,"status":row['status'],
                "receipt":json.loads(row['receipt']) if row['receipt'] else None}

    def _record(self,c,operation_id,status,evidence):
        c.execute('INSERT INTO delivery_log(operation_id,status,occurred_at,evidence) VALUES(?,?,?,?)',
            (operation_id,status,self.repo.lease_clock.wall(),canonical(evidence)))

    def _receipt(self,operation_id,receipt):
        if not isinstance(receipt,dict) or receipt.get('operation_id')!=operation_id:
            raise DomainError('delivery_receipt_identity_mismatch',409)
        status=receipt.get('status')
        if status not in ('accepted','delivered','not_found'):
            raise DomainError('invalid_delivery_receipt')
        if status=='delivered':
            identity(receipt.get('message_id'),'delivery_message_id')
        return status

    def dispatch(self,operation_id):
        if self.adapter is None:
            raise DomainError('delivery_gateway_not_configured',503)
        row=self.get(operation_id)
        token=self.repo.claim_action(operation_id,self.repo.lease_clock.wall())
        if token is None:
            return self.view(operation_id)
        with self.repo.transaction() as c:
            self._record(c,operation_id,'sending',{'claim_token':token})
        try:
            receipt=self.adapter.send(operation_id,json.loads(row['payload']))
            status=self._receipt(operation_id,receipt)
            if status=='not_found':
                raise DomainError('invalid_send_receipt')
        except Exception as exc:
            status='unknown_delivery';receipt={'operation_id':operation_id,'error_type':type(exc).__name__}
        with self.repo.transaction() as c:
            changed=c.execute("UPDATE outbox SET status=?,receipt=?,updated_at=?,claim_token=NULL WHERE operation_id=? AND status='sending' AND claim_token=?",
                (status,canonical(receipt),self.repo.lease_clock.wall(),operation_id,token)).rowcount
            if changed:
                self._record(c,operation_id,status,receipt)
        return self.view(operation_id)

    def reconcile(self,operation_id):
        if self.adapter is None:
            raise DomainError('delivery_gateway_not_configured',503)
        row=self.get(operation_id)
        if row['status'] in ('ready','delivered'):
            return self.view(operation_id)
        if row['status']=='sending':
            if not lease_expired(json.loads(row['lease_stamp']) if row['lease_stamp'] else None,self.repo.lease_clock):
                return self.view(operation_id)
            with self.repo.transaction() as c:
                c.execute("UPDATE outbox SET status='unknown_delivery',claim_token=NULL WHERE operation_id=? AND status='sending' AND claim_token=?",(operation_id,row['claim_token']))
                self._record(c,operation_id,'unknown_delivery',{'reason':'claim_expired'})
            row=self.get(operation_id)
        try:
            receipt=self.adapter.lookup(operation_id)
            status=self._receipt(operation_id,receipt)
        except Exception:
            return self.view(operation_id)
        if status=='not_found':
            # Only authoritative negative acknowledgement AND persistent gateway
            # idempotency permit another send with the exact same operation ID.
            if receipt.get('authoritative') is not True or receipt.get('dedupe_supported') is not True:
                return self.view(operation_id)
            status='ready'
        with self.repo.transaction() as c:
            changed=c.execute("UPDATE outbox SET status=?,receipt=?,updated_at=? WHERE operation_id=? AND status IN ('accepted','unknown_delivery') AND updated_at=?",
                (status,canonical(receipt),self.repo.lease_clock.wall(),operation_id,row['updated_at'])).rowcount
            if changed:
                self._record(c,operation_id,status,receipt)
        return self.view(operation_id)

    def maintain(self,limit=20):
        if self.adapter is None:return 0
        with self.repo.connect() as c:
            rows=c.execute("SELECT o.operation_id,o.status,s.next_wall,s.stamp,s.failures FROM outbox o LEFT JOIN delivery_schedule s ON o.operation_id=s.operation_id WHERE o.status!='delivered' ORDER BY COALESCE(s.next_wall,0),o.updated_at,o.operation_id").fetchall()
        attempts=0
        for row in rows:
            if attempts>=limit:break
            if row['stamp']:
                stamp=json.loads(row['stamp'])
                if not lease_expired(stamp,self.repo.lease_clock,max(0,row['next_wall']-stamp['wall'])):continue
            operation_id=row['operation_id']
            result=self.dispatch(operation_id) if row['status']=='ready' else self.reconcile(operation_id)
            failures=(row['failures'] or 0)+1 if result['status']=='unknown_delivery' else 0
            delay=min(3600,2**min(10,failures)) if failures else 5
            with self.repo.transaction() as c:
                c.execute("INSERT INTO delivery_schedule VALUES(?,?,?,?) ON CONFLICT(operation_id) DO UPDATE SET next_wall=excluded.next_wall,stamp=excluded.stamp,failures=excluded.failures",
                    (operation_id,self.repo.lease_clock.wall()+delay,canonical(lease_stamp(self.repo.lease_clock)),failures))
            attempts+=1
        return attempts
