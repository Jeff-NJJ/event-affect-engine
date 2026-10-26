'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../operit/expression_hook/main.js'),'utf8');

(async function () {
  let calls=0, fault=null, script='', registered=null;
  const sandbox={exports:{},setTimeout,clearTimeout,console:{error:()=>{}},ToolPkg:{registerPromptFinalizeHook:r=>registered=r,registerChatMessageHook:r=>assert.equal(r.id,'endocrine_message_sync_v2'),registerAppLifecycleHook:r=>assert.equal(r.event,'application_on_create')},
    Tools:{System:{terminal:{hiddenExec:()=>{assert.fail('native expression must not start terminal');}}}}};
  vm.runInNewContext(source,sandbox);
  sandbox.expressionNativeContext=async(request)=>{
      calls++;
      script=sandbox.exports.expressionPython(request);
      if(fault==='timeout')throw new Error('expression_http_deadline');
      if(fault==='json')throw new Error('invalid_json');
      if(fault==='unbound')return{success:true,status:'unbound'};
      const reply={success:true,agent_id:'a',actor_id:'p',conversation_id:'chat-1',binding_card_id:'card-1',
        persisted_revision:3,source_status:'background_only',expression:{version:'expression-v1'},prompt_suffix:'自然表达测试'};
      if(fault==='scope')reply.conversation_id='other';
      if(fault==='card')reply.binding_card_id='other';
      if(fault==='version')reply.expression.version='wrong';
      return reply;
  };
  assert.equal(sandbox.exports.registerToolPkg(),true);assert.equal(registered.id,'endocrine_expression_v1');
  const event={eventName:'before_finalize_prompt',eventPayload:{chatId:'chat-1',functionType:'CHAT',
    systemPrompt:'原角色与任务规则',metadata:{activePrompt:{type:'character_card',id:'card-1'},untouched:42},
    preparedHistory:[{kind:'USER',content:'不可当成自己的口癖'},{kind:'ASSISTANT',content:'我想想🙂\n引号" $(nothing)'}]}};
  const before=JSON.stringify(event);
  const result=await sandbox.exports.endocrineExpressionHook(event);
  assert.equal(result.systemPrompt,'原角色与任务规则\n\n自然表达测试');
  assert.equal(result.metadata.endocrineExpression.status,'injected');assert.equal(result.metadata.untouched,42);
  const firstGeneratedScript=script;
  assert.equal(JSON.stringify(event),before);assert(!script.includes('原角色与任务规则'));
  assert.equal(sandbox.expressionQuery(event.eventPayload),'不可当成自己的口癖');
  const realShape={eventName:'before_finalize_prompt',eventPayload:{...event.eventPayload,systemPrompt:null,
    preparedHistory:[{kind:'SYSTEM',content:'真实发送给模型的角色规则',metadata:{owner:'host'}},{kind:'USER',content:'完整当前话语'}]}};
  const realResult=await sandbox.exports.endocrineExpressionHook(realShape);
  assert.equal(realResult.systemPrompt,undefined);
  assert.equal(realResult.preparedHistory[0].content,'真实发送给模型的角色规则\n\n自然表达测试');
  assert.equal(realResult.preparedHistory[1].content,'完整当前话语');
  assert.equal(realResult.preparedHistory[0].metadata.owner,'host');
  assert.equal(realResult.metadata.endocrineExpression.prompt_target,'prepared_history_system');
  const realAgain=await sandbox.exports.endocrineExpressionHook({...realShape,eventPayload:{...realShape.eventPayload,...realResult}});
  assert.equal(realAgain.preparedHistory[0].content,realResult.preparedHistory[0].content);
  if(process.argv[2])fs.writeFileSync(process.argv[2],firstGeneratedScript);
  const repeated={...event,eventPayload:{...event.eventPayload,...result}};
  const again=await sandbox.exports.endocrineExpressionHook(repeated);
  assert.equal(again.systemPrompt,result.systemPrompt);
  const laterText='\n来自其他插件的规则';
  const appendedLater={...repeated,eventPayload:{...repeated.eventPayload,systemPrompt:result.systemPrompt+laterText}};
  const afterOther=await sandbox.exports.endocrineExpressionHook(appendedLater);
  assert.equal(afterOther.systemPrompt,'原角色与任务规则'+laterText+'\n\n自然表达测试');
  assert.equal(afterOther.systemPrompt.split('自然表达测试').length-1,1);
  const shifted={...repeated,eventPayload:{...repeated.eventPayload,systemPrompt:'前置插件\n'+result.systemPrompt+laterText}};
  const afterPrefix=await sandbox.exports.endocrineExpressionHook(shifted);
  assert.equal(afterPrefix.systemPrompt,'前置插件\n原角色与任务规则'+laterText+'\n\n自然表达测试');
  const legacy={...appendedLater,eventPayload:{...appendedLater.eventPayload,
    metadata:{...appendedLater.eventPayload.metadata,endocrineExpression:{appended:result.metadata.endocrineExpression.appended}}}};
  assert.equal((await sandbox.exports.endocrineExpressionHook(legacy)).systemPrompt,afterOther.systemPrompt);
  const groupAfterOther={...appendedLater,eventPayload:{...appendedLater.eventPayload,
    metadata:{...appendedLater.eventPayload.metadata,activePrompt:{type:'character_group',id:'group'}}}};
  const groupCleaned=await sandbox.exports.endocrineExpressionHook(groupAfterOther);
  assert.equal(groupCleaned.systemPrompt,'原角色与任务规则'+laterText);
  const ambiguous={...legacy,eventPayload:{...legacy.eventPayload,systemPrompt:result.systemPrompt+result.metadata.endocrineExpression.appended+laterText}};
  const noGuess=await sandbox.exports.endocrineExpressionHook(ambiguous);
  assert.equal(noGuess.metadata.endocrineExpression.status,'ambiguous_previous_block');
  assert.equal(noGuess.systemPrompt,undefined);
  const stillAmbiguous=await sandbox.exports.endocrineExpressionHook({...ambiguous,
    eventPayload:{...ambiguous.eventPayload,metadata:noGuess.metadata}});
  assert.equal(stillAmbiguous.metadata.endocrineExpression.status,'ambiguous_previous_block');
  assert.equal(stillAmbiguous.systemPrompt,undefined);
  await assert.rejects(sandbox.expressionAwait(new Promise(()=>{}),25),/expression_native_deadline/);
  const n=calls;
  for(const modified of [
    {...event,eventName:'before_send_to_model'},
    {...event,eventPayload:{...event.eventPayload,functionType:'SUMMARY'}},
    {...event,eventPayload:{...event.eventPayload,promptFunctionType:'TOOL'}},
    {...event,eventPayload:{...event.eventPayload,metadata:{activePrompt:{type:'character_group',id:'group'}}}}
  ])assert.equal(await sandbox.exports.endocrineExpressionHook(modified),null);
  assert.equal(calls,n);
  const changedToGroup={...repeated,eventPayload:{...repeated.eventPayload,metadata:{...repeated.eventPayload.metadata,
    activePrompt:{type:'character_group',id:'group'}}}};
  const cleaned=await sandbox.exports.endocrineExpressionHook(changedToGroup);
  assert.equal(cleaned.systemPrompt,'原角色与任务规则');assert.equal(calls,n);
  for(fault of ['scope','card','version','timeout','json']){
    const bad=await sandbox.exports.endocrineExpressionHook(repeated);
    assert.equal(bad.systemPrompt,'原角色与任务规则');assert.equal(bad.metadata.endocrineExpression.status,'failed');
  }
  fault='unbound';assert.equal((await sandbox.exports.endocrineExpressionHook(repeated)).metadata.endocrineExpression.status,'unbound');
  fault=null;
  const tooLong=await sandbox.exports.endocrineExpressionHook({...event,eventPayload:{...event.eventPayload,
    preparedHistory:[{kind:'ASSISTANT',content:'x'.repeat(8001)}]}});
  assert.equal(tooLong.metadata.endocrineExpression.status,'failed');
  console.log('expression hook contract: passed (registration, injection, no duplicate block, scope, history, timeout, failures)');
})().catch(e=>{console.error(e);process.exitCode=1;});
