const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const path = require('path');

(async function () {
  const source = fs.readFileSync(path.join(__dirname, '../operit/endocrine_bridge.js'), 'utf8');
  const scripts = [], results = [], requests = [];
  let fault = null;
  const sandbox = {exports:{}, Date, Math, JSON, encodeURIComponent,setTimeout,clearTimeout,
    toolCall: () => { throw new Error('Shared run_python transport must never be used'); },
    Tools:{System:{terminal:{hiddenExec:async (command, options) => {
      assert(command.startsWith("( python3 - <<'__ENDOCRINE_HTTP_V4_EOF__'\n"));
      assert(command.endsWith('\n__ENDOCRINE_HTTP_V4_EOF__\n)'));
      assert(!command.includes('/tmp/temp_script.py'));
      assert.strictEqual(options.executorKey, 'endocrine_http_v4');
      const script = command.slice(command.indexOf('\n') + 1, command.lastIndexOf('\n__ENDOCRINE_HTTP_V4_EOF__'));
      scripts.push(script);
      if (fault === 'exec') return {exitCode:1, output:'not a receipt'};
      if (fault === 'timeout') return {exitCode:0, timedOut:true, output:'{}'};
      if (fault === 'never') return new Promise(()=>{});
      if (fault === 'json') return {exitCode:0, output:'command echo'};
      const line = script.split('\n').find(x => x.startsWith('    payload = '));
      const literal = line.slice('    payload = '.length);
      if (literal === 'None') {
        const agent = script.includes('other_agent') ? 'other_agent' : 'example_agent';
        return {exitCode:0, output:JSON.stringify({success:true,engine:'event_affect_v4',agent_id:agent})};
      }
      const body = JSON.parse(JSON.parse(literal.slice(0, -".encode('utf-8')".length)));
      requests.push(body);
      if (script.includes('"/v2/memory/search"')) return {exitCode:0,output:JSON.stringify({success:true,items:[],retrieval_mode:'cloud_full_paragraph'})};
      if (script.includes('"/v2/expression/context"')) {
        const reply={success:true,agent_id:body.agent_id,actor_id:body.actor_id,conversation_id:body.conversation_id,
          source_event_id:body.source_event_id || null,expression:{version:'expression-v1'},prompt_suffix:'test'};
        if(fault==='expression_actor')reply.actor_id='wrong';
        if(fault==='expression_source')reply.source_event_id='wrong';
        return {exitCode:0,output:JSON.stringify(reply)};
      }
      await Promise.resolve();
      const reply = {success:true,engine:'event_affect_v4',event_id:body.event_id,agent_id:body.agent_id,revision:1,applied_message:body.msg};
      if (fault === 'event') reply.event_id = 'wrong-event';
      if (fault === 'agent') reply.agent_id = 'wrong-agent';
      if (fault === 'missing') delete reply.event_id;
      return {exitCode:0, output:JSON.stringify(reply)};
    }}}}, complete:value => results.push(value)};
  vm.runInNewContext(source, sandbox);
  const params = {message:"我说：can't；\"引号\"\n第二行\\路径🙂 $(no_exec)",source_message_id:'18542',conversation_id:'c'};
  await sandbox.exports.analyze(params);
  await sandbox.exports.analyze(params);
  assert.strictEqual(results[0].event_id, 'operit:c:18542');
  assert.strictEqual(results[0].event_id, results[1].event_id);
  assert.strictEqual(scripts[0], scripts[1]);
  assert(scripts[0].includes('for attempt in range(2)'));
  assert.strictEqual(results[0].client_dedupe_scope, 'logical_event');
  assert.strictEqual(requests[0].msg, params.message);
  await sandbox.exports.analyze({message:'同样内容'});
  await sandbox.exports.analyze({message:'同样内容'});
  assert.notStrictEqual(results[2].event_id, results[3].event_id);
  assert.strictEqual(results[2].client_dedupe_scope, 'this_call_only');
  const at = results.length;
  await Promise.all(Array.from({length:20}, (_,i) => sandbox.exports.analyze({event_id:'parallel_'+i,message:'Message '+i})));
  for (let i=0; i<20; i++) {
    const row = results.slice(at).find(r=>r.event_id==='parallel_'+i);
    assert(row && row.success);
    assert.strictEqual(row.applied_message, 'Message '+i);
  }
  for (fault of ['event','agent','missing']) {
    await sandbox.exports.analyze({event_id:'expected',message:'test'});
    const row = results[results.length-1];
    assert.strictEqual(row.success, false);
    assert.strictEqual(row.error, 'server_receipt_identity_mismatch');
    assert.strictEqual(row.requested_event_id, 'expected');
    assert.strictEqual(row.event_id, undefined);
  }
  for (fault of ['exec','timeout','json']) {
    await sandbox.exports.analyze({event_id:'failed',message:'test'});
    assert.strictEqual(results[results.length-1].success, false);
  }
  fault = null;
  sandbox.setTimeout=(fn,ms)=>setTimeout(fn,Math.min(ms,25));
  fault='never';
  await sandbox.exports.analyze({event_id:'native_init_stalled',message:'synthetic'});
  assert.strictEqual(results.at(-1).success,false);
  assert.strictEqual(results.at(-1).error,'transport_executor_unavailable_delivery_unknown');
  fault=null;sandbox.setTimeout=setTimeout;
  await sandbox.exports.analyze({event_id:'other',agent_id:'other_agent',message:'hello'});
  assert.strictEqual(results[results.length-1].agent_id, 'other_agent');
  await sandbox.exports.snapshot({agent_id:'other_agent'});
  assert.strictEqual(results[results.length-1].agent_id, 'other_agent');
  const count = scripts.length;
  await sandbox.exports.record_response({message:'真实回复',source_message_id:'reply42',conversation_id:'c'});
  assert.strictEqual(requests[requests.length-1].role,'assistant');
  assert.strictEqual(requests[requests.length-1].text,'真实回复');
  assert.strictEqual(requests[requests.length-1].event_id,'operit-assistant:c:reply42');
  await sandbox.exports.semantic_memory({query:'这是一整段查询，不要求它含有原来的词。',actor_id:'p'});
  assert.strictEqual(results[results.length-1].success,true);
  assert.strictEqual(requests[requests.length-1].query,'这是一整段查询，不要求它含有原来的词。');
  assert.strictEqual(requests[requests.length-1].actor_id,'p');
  await sandbox.exports.prepare_expression({agent_id:'a',actor_id:'p',conversation_id:'c',source_event_id:'u'});
  assert.equal(results.at(-1).success,true);
  for(fault of ['expression_actor','expression_source']) {
    await sandbox.exports.prepare_expression({agent_id:'a',actor_id:'p',conversation_id:'c',source_event_id:'u'});
    assert.equal(results.at(-1).error,'server_receipt_identity_mismatch');
  }
  const beforeInvalidExpression=scripts.length;
  await sandbox.exports.prepare_expression({agent_id:'a'});
  assert.equal(results.at(-1).error,'expression_scope_required');assert.equal(scripts.length,beforeInvalidExpression);
  fault=null;
  const afterNewTools=scripts.length;
  await sandbox.exports.reset();
  assert.strictEqual(scripts.length, afterNewTools);
  console.log('Bridge passed: isolated stdin transport, 20 concurrent identities, receipt mismatch rejection, Unicode, retry identity, per-agent snapshots');
  if (process.argv[2]) fs.writeFileSync(process.argv[2], scripts[0]);
})().catch(e => { console.error(e); process.exitCode = 1; });
