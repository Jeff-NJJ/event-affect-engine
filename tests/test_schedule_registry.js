'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const path=require('node:path'),os=require('node:os'),{spawn}=require('node:child_process');
const src=fs.readFileSync(path.join(__dirname,'../operit/daily_life.guarded.js'),'utf8');
const begin=src.indexOf('    async function schedule_one_time_task(params) {');
const end=src.indexOf('    async function set_alarm(params)',begin);
const source='('+src.slice(begin,end)+')';
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'operit-schedule-tests-'));
const python=process.env.OPERIT_TEST_PYTHON||'python';let passed=0;
function fixture(name){
 const store=new Map();let creates=0,updates=0,fault=null;
 const db=path.join(temp,name+'.sqlite3').replaceAll('\\','/');
 async function hiddenExec(command,options){
  assert.equal(options.executorKey,'operit_schedule_v5');
  const delimiter='__OPERIT_SCHEDULE_V5_EOF__';
  assert(command.startsWith("( python3 - <<'"+delimiter+"'\n"));
  let script=command.slice(command.indexOf('\n')+1,command.lastIndexOf('\n'+delimiter));
  if(process.argv[2]&&!fs.existsSync(process.argv[2]))fs.writeFileSync(process.argv[2],script);
  script=script.replace('"/sdcard/Download/Operit/.schedule_registry_v5.sqlite3"',JSON.stringify(db));
  return new Promise((resolve,reject)=>{
   const child=spawn(python,['-B','-'],{env:{...process.env,PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8'},windowsHide:true});
   let out='',err='';const timer=setTimeout(()=>child.kill(),12000);
   child.stdout.on('data',c=>out+=c.toString('utf8'));child.stderr.on('data',c=>err+=c.toString('utf8'));
   child.on('error',reject);child.on('close',code=>{clearTimeout(timer);resolve({exitCode:code,output:out,stderr:err});});
   child.stdin.on('error',()=>{});child.stdin.end(script);
  });
 }
 const Tools={System:{terminal:{hiddenExec}},Workflow:{
  create:async(name,description)=>{
   creates++;if(fault==='reject-create'){fault=null;throw new Error('native unavailable');}
   const row={id:'workflow_fixture_'+creates,name,description};store.set(row.id,row);
   if(fault==='lost-create-receipt'){fault=null;throw new Error('receipt lost after create commit');}
   return {id:row.id};
  },
  update:async(id,data)=>{
   updates++;assert(store.has(id));
   if(fault==='update-unavailable'){fault=null;throw new Error('update unavailable');}
   store.set(id,{id,...JSON.parse(JSON.stringify(data))});
   if(fault==='lost-update-receipt'){fault=null;throw new Error('receipt lost after update commit');}
  },
  getAll:async()=>({workflows:[...store.values()].map(r=>({...r}))})
 }};
 return {store,db,creates:()=>creates,updates:()=>updates,setFault:f=>fault=f,
  call:p=>vm.runInNewContext(source,{setTimeout,clearTimeout,getChatId:()=> 'chat_test',getCallerCardId:()=> 'card_test',
   getCallerName:()=> '角色',getLang:()=> 'zh',Tools})(p)};
}
const params={trigger_time:'2026-10-19 12:00:00',message:'测试提醒'};
async function check(fn){await fn();passed++;}
(async()=>{
 await check(async()=>{
  const f=fixture('repeat'),a=await f.call(params),b=await f.call(params);
  assert(a.success&&b.success);assert.equal(f.creates(),1);assert.equal(b.data.reused,true);
  assert.equal(a.data.workflow_id,b.data.workflow_id);assert.equal(a.data.task_id,b.data.task_id);
  const row=f.store.get(a.data.workflow_id);assert.equal(row.nodes.length,7);
  const trigger=row.nodes.find(n=>n.type==='trigger'),start=row.nodes.find(n=>n.actionType==='start_chat_service');
  const guard=row.nodes.find(n=>n.actionType==='super_admin:terminal'),condition=row.nodes.find(n=>n.type==='condition');
  assert.equal(row.nodes.find(n=>n.type==='extract').defaultValue,'SKIP');
  assert.equal(condition.operator,'EQ');assert.equal(condition.right,'GO');
  assert(row.connections.some(c=>c.sourceNodeId===trigger.id&&c.targetNodeId===guard.id));
  assert(row.connections.some(c=>c.sourceNodeId===condition.id&&c.targetNodeId===start.id&&c.condition==='true'));
  assert(!row.connections.some(c=>c.sourceNodeId===trigger.id&&c.targetNodeId===start.id));
  assert(guard.actionConfig.command.includes('task_'+a.data.task_id));
  assert(!guard.actionConfig.command.includes('WF_GUARD_GO'));
  assert(!guard.actionConfig.command.includes(a.data.workflow_id));
 });
 await check(async()=>{
  const f=fixture('concurrent');await Promise.all(Array.from({length:8},()=>f.call(params)));
  assert((await f.call(params)).success);assert.equal(f.creates(),1);assert.equal(f.store.size,1);
 });
 for(const fault of ['lost-create-receipt','update-unavailable','lost-update-receipt'])await check(async()=>{
  const f=fixture(fault);f.setFault(fault);await assert.rejects(f.call(params));
  const recovered=await f.call(params);assert(recovered.success);assert.equal(f.creates(),1);
  assert.equal(f.store.get(recovered.data.workflow_id).nodes.length,7);
  assert((await f.call(params)).data.reused);
 });
 await check(async()=>{
  const f=fixture('unknown');f.setFault('reject-create');await assert.rejects(f.call(params));
  const b=await f.call(params);assert.equal(b.success,false);
  assert.equal(b.error,'schedule_creation_pending_reconciliation');assert.equal(f.creates(),1);
 });
 await check(async()=>{
  const f=fixture('request-id');await f.call({...params,request_id:'logical-1'});
  await assert.rejects(f.call({...params,message:'变更内容',request_id:'logical-1'}),/schedule_request_conflict/);
  assert((await f.call({...params,request_id:'logical-2'})).success);assert.equal(f.creates(),2);
 });
 await check(async()=>{
  const f=fixture('hostile'),p={...params,message:'引号"\n` $(no_exec)\n__OPERIT_SCHEDULE_V5_EOF__🙂'};
  assert((await f.call(p)).success);assert((await f.call(p)).data.reused);assert.equal(f.creates(),1);
 });
 await check(async()=>{
  const f=fixture('validation');await assert.rejects(f.call({...params,request_id:''}));
  await assert.rejects(f.call({...params,message:12}));assert.equal(f.creates(),0);
 });
 await check(async()=>{
  let creates=0;
  const stalled=vm.runInNewContext(source,{getChatId:()=> 'chat_test',getCallerCardId:()=> 'card_test',
   getCallerName:()=> '角色',getLang:()=> 'zh',setTimeout:(fn,ms)=>setTimeout(fn,Math.min(ms,25)),clearTimeout,
   Tools:{System:{terminal:{hiddenExec:()=>new Promise(()=>{})}},Workflow:{create:()=>{creates++;}}}});
  await assert.rejects(stalled(params),/schedule_registry_delivery_unknown/);assert.equal(creates,0);
 });
 console.log(JSON.stringify({schedule_regressions:passed,real_python_sqlite:true}));
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>fs.rmSync(temp,{recursive:true,force:true}));
