'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),crypto=require('node:crypto');
const source=fs.readFileSync(__dirname+'/../operit/expression_hook/main.js','utf8');
const schedule=fs.readFileSync(__dirname+'/../operit/native_schedule_primitives.js','utf8');
function JavaString(text){this.getBytes=()=>Buffer.from(text,'utf8');}
function BigInteger(sign,bytes){
 const value=BigInt('0x'+Buffer.from(bytes).toString('hex'));
 // Operit's JS proxy toString ignores extra arguments. Explicit call is needed.
 this.toString=()=>value.toString(10);
 this.call=(method,radix)=>{assert.equal(method,'toString');return value.toString(radix);};
}
const Java={type:name=>{
 if(name==='java.lang.String')return JavaString;
 if(name==='java.math.BigInteger')return BigInteger;
 if(name==='java.security.MessageDigest')return {getInstance:()=>({digest:b=>crypto.createHash('sha256').update(b).digest()})};
 throw new Error('Unexpected native access '+name);
}};
const s={exports:{},Java,setTimeout,clearTimeout,console};vm.runInNewContext(source+'\n'+schedule,s);
const body={agent_id:'a',conversation_id:'会话🙂',role:'user',message_id:'user:123'};
const expected='operit-native:'+crypto.createHash('sha256').update(JSON.stringify(['a','会话🙂','user','user:123'])).digest('hex');
assert.equal(s.exports.endocrineEventId(body),expected);
const a=s.endocrineNativeRegistry;
assert.equal(s.nativeScheduleHash({b:'后',a:'前'}),s.nativeScheduleHash({a:'前',b:'后'}));
assert.match(s.nativeScheduleHash({a:'test'}),/^[a-f0-9]{64}$/);
assert.equal(s.expressionQuery({processedInput:'完整输入，后半句有否定',preparedHistory:[]}), '完整输入，后半句有否定');
assert.equal(s.exports.endocrineMessageBody({sender:'ai',content:'partial',timestamp:123,completedAt:0},{agent_id:'a',actor_id:'u'},1000),null);
assert.equal(s.exports.endocrineMessageBody({sender:'user',content:'消息',chatId:'c',timestamp:123},{agent_id:'a',actor_id:'u'},1000).message_id,'user:123');
console.log('Native client contracts passed: exact UTF-8 event ID, Java proxy radix, stable task hash, full query and incomplete-turn exclusion');
