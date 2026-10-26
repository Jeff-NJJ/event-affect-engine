const fs=require('fs'), vm=require('vm'), assert=require('assert'), path=require('path');
(async()=>{
  const results=[],scripts=[];
  let fault=null;
  const box={exports:{},JSON,Date,Math,encodeURIComponent,setTimeout,clearTimeout,complete:x=>results.push(x),
    Tools:{System:{terminal:{hiddenExec:async(command,options)=>{
      const script=command.slice(command.indexOf('\n')+1,command.lastIndexOf('\n__ENDOCRINE_HTTP_V4_EOF__'));
      scripts.push(script);
      const line=script.split('\n').find(x=>x.startsWith('    payload = '));
      const body=JSON.parse(JSON.parse(line.slice('    payload = '.length,-".encode('utf-8')".length)));
      const r={success:true,agent_id:body.agent_id,actor_id:body.actor_id,conversation_id:body.conversation_id,
        source_event_id:body.source_event_id,state_mutated:false,messages_sent:0,evidence_fingerprint:'fixture',
        evidence:[],review:{decision:'accept',issues:[]},accepted:true,status:'model_reviewed',reply:'经过审查的回复'};
      if(script.includes('/v2/expression/respond')) {
        assert.strictEqual(options.timeoutMs,170000);
        assert(script.includes('range(1)'));assert(script.includes('timeout=160'));
      } else assert.strictEqual(options.timeoutMs,190000);
      if(fault==='identity')r.source_event_id='wrong';
      if(fault==='draft')r.status='unreviewed';
      if(fault==='mutation')r.state_mutated=true;
      if(fault==='withheld')return {exitCode:0,output:JSON.stringify({success:false,status:'withheld',error:'reply_review_failed'})};
      return {exitCode:0,output:JSON.stringify(r)};
    }}}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../operit/endocrine_bridge.js'),'utf8'),box);
  const scope={agent_id:'a',actor_id:'p',conversation_id:'c',source_event_id:'u'};
  for(const tool of ['reflect_memory','review_reply','generate_reviewed_reply']) {
    await box.exports[tool]({...scope,hypothesis:'完整语义',reply:'候选'});
    assert.strictEqual(results.at(-1).success,true);
    fault='identity';await box.exports[tool]({...scope,hypothesis:'完整语义',reply:'候选'});
    assert.strictEqual(results.at(-1).error,'server_receipt_identity_mismatch');fault=null;
  }
  for(fault of ['draft','mutation']) {
    await box.exports.generate_reviewed_reply(scope);
    assert.strictEqual(results.at(-1).success,false);
    assert.strictEqual(results.at(-1).reply,undefined);
  }
  fault='withheld';await box.exports.generate_reviewed_reply(scope);
  assert.strictEqual(results.at(-1).reply,undefined);fault=null;
  const count=scripts.length;
  await box.exports.generate_reviewed_reply({...scope,source_event_id:''});
  assert.strictEqual(scripts.length,count);
  await box.exports.generate_reviewed_reply(scope);
  if(process.argv[2])fs.writeFileSync(process.argv[2],scripts.at(-1),'utf8');
  console.log('Grounding bridge: scopes, withheld drafts, no automatic generation retry, timeout budgets passed');
})().catch(e=>{console.error(e);process.exit(1);});
