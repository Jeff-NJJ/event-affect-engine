import asyncio
import copy
from datetime import datetime,timedelta,timezone
import hashlib
import hmac
import importlib.util
from pathlib import Path
import tempfile
import unittest

from endocrine.model import canonical

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('ob_affect_extension',ROOT/'ob_extension/affect_bridge_tools.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class ExtensionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.key=b'synthetic-only-signing-key-not-production';kp=Path(self.tmp.name)/'test.key';kp.write_bytes(self.key)
        class Manager:
            def __init__(self):
                self.data={'m':{'content':'旧摘要内容','metadata':{'type':'dynamic','created':'2026-10-01T10:00:00'}}}
            async def get(self,bid):return copy.deepcopy(self.data.get(bid))
        self.manager=Manager();self.config={'archive_enabled':True,'archive_db':str(Path(self.tmp.name)/'ob_owned.sqlite'),
            'archive_agents':['a'],'archive_signing_key_file':str(kp),
            'scopes':{'ap':{'bucket_ids':['m'],'source_utc_offset_minutes':480}}}
        self.ext=module.AffectTools(self.manager,self.config)
        self.now=datetime.now(timezone.utc).isoformat()

    async def test_explicit_allowlist_and_private_type_exclusion(self):
        self.manager.data['private']={'content':'PRIVATE','metadata':{'type':'letter','created':self.now}}
        self.config['scopes']['ap']['bucket_ids'].append('private');self.ext=module.AffectTools(self.manager,self.config)
        result=await self.ext.context('ap',self.now)
        self.assertEqual(len(result['items']),1);self.assertNotIn('PRIVATE',str(result))

    async def test_read_does_not_touch_metadata_and_retains_original_time(self):
        before=copy.deepcopy(self.manager.data)
        r=(await self.ext.context('ap',self.now))['items'][0]
        self.assertEqual(before,self.manager.data);self.assertEqual(r['temporal_basis'],'retrieval_time_mutable_source')
        self.assertEqual(r['original_timestamps']['created'],'2026-10-01T10:00:00')
        self.assertEqual(r['known_at'],module.aware(self.now))

    async def test_historical_time_travel_is_rejected(self):
        old=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
        with self.assertRaisesRegex(ValueError,'historical snapshots'):await self.ext.context('ap',old)

    async def test_missing_timezone_not_guessed(self):
        del self.config['scopes']['ap']['source_utc_offset_minutes'];self.ext=module.AffectTools(self.manager,self.config)
        with self.assertRaises(ValueError):await self.ext.context('ap',self.now)

    async def test_pagination_no_truncation_and_version_changes(self):
        self.manager.data={str(i):{'content':'原文'+str(i),'metadata':{'type':'dynamic','created':self.now}} for i in range(20)}
        self.config['scopes']['ap']['bucket_ids']=list(self.manager.data);self.ext=module.AffectTools(self.manager,self.config)
        first=await self.ext.context('ap',self.now);second=await self.ext.context('ap',self.now,first['next_cursor'])
        self.assertEqual(len(first['items'])+len(second['items']),20);self.assertIsNone(second['next_cursor'])
        self.assertEqual(first['scope_version'],second['scope_version'])
        self.manager.data['0']['content']='修改过的内容'
        self.assertNotEqual((await self.ext.context('ap',self.now))['scope_version'],first['scope_version'])

    async def test_no_archive_without_valid_signature(self):
        from test_ob_bridge import item
        with self.assertRaises(ValueError):await self.ext.archive(item(),'0'*64)

    async def test_duplicate_archive_lookup_and_conflicting_payload(self):
        from test_ob_bridge import item
        a=item();sig=hmac.new(self.key,canonical(a).encode(),hashlib.sha256).hexdigest()
        await self.ext.archive(a,sig);r=await self.ext.archive(a,sig)
        self.assertTrue(r['deduplicated']);self.assertEqual(r['ordinary_buckets_written'],0)
        self.assertEqual((await self.ext.lookup(a['archive_id']))['status'],'stored')
        a['conclusion']['trust_delta']=.2;sig=hmac.new(self.key,canonical(a).encode(),hashlib.sha256).hexdigest()
        with self.assertRaisesRegex(ValueError,'fingerprint conflict'):await self.ext.archive(a,sig)

    async def test_wrong_agent_and_raw_text_rejected_even_with_signature(self):
        from test_ob_bridge import item
        for a in ({**item(),'agent_id':'other'},{**item(),'text':'PRIVATE'}):
            with self.subTest(a=a),self.assertRaises(ValueError):self.ext.validate(a)
