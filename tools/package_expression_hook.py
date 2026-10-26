"""Build a deterministic ToolPkg; no APK build or deployment."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def build(destination):
    source=(ROOT/'operit/expression_hook/main.js').read_text(encoding='utf-8')
    manifest=json.loads((ROOT/'operit/expression_hook/manifest.json').read_text(encoding='utf-8'))
    manifest['subpackages']=[{'id':'endocrine_fullchain_probe_v2','entry':'packages/expression_probe.js',
        'enabled_by_default':False,'display_name':{'zh':'情绪表达只读验收'}}]
    manifest['subpackages'].append({'id':'endocrine_native_v2','entry':'packages/native_endocrine.js',
        'enabled_by_default':True,'display_name':{'zh':'情绪同步与可靠提醒'}})
    meta={'name':'endocrine_fullchain_probe_v2','display_name':'情绪表达只读验收',
        'description':'调用同一提示钩子检查本地配置、原生 HTTP 与作用域回执；不生成或发送回复。',
        'tools':[{'name':'probe','description':'对明确绑定的现有角色和聊天进行一次只读接入验收。',
            'parameters':[{'name':k,'type':'string','required':True,'description':v} for k,v in
                (('chat_id','真实 Operit 聊天ID'),('card_id','真实角色卡ID'))]}]}
    probe='/* METADATA\n'+json.dumps(meta,ensure_ascii=False,indent=2)+'\n*/\n'+source+'''
exports.probe = async function (params) {
  if (!params || typeof params.chat_id !== 'string' || !params.chat_id || typeof params.card_id !== 'string' || !params.card_id) {
    complete({success:false,error:'real_chat_and_card_id_required'}); return;
  }
  var event = {eventName:'before_finalize_prompt',eventPayload:{functionType:'CHAT',chatId:params.chat_id,
    systemPrompt:'验收占位提示：保持原角色规则',metadata:{activePrompt:{type:'character_card',id:params.card_id}},
    preparedHistory:[]}};
  var result = await endocrineExpressionHook(event);
  var status = result && result.metadata && result.metadata.endocrineExpression;
  complete({success:!!status && status.status==='injected',probe_revision:'expression-v1',
    status:status ? status.status : 'not_invoked',agent_id:status && status.agent_id,
    actor_id:status && status.actor_id,conversation_id:status && status.conversation_id,
    original_prompt_preserved:!!result && result.systemPrompt.indexOf(event.eventPayload.systemPrompt)===0,
    injection_characters:result ? result.systemPrompt.length-event.eventPayload.systemPrompt.length : 0,
    executed_at_utc:new Date().toISOString(),external_messages_sent:0,state_events_created:0,
    reason:status && status.reason,transport:'android_native_http',
    scope:'native_hook_function_transport_only; actual chat injection needs model-request inspection'});
};
'''
    native_meta={'name':'endocrine_native_v2','display_name':'情绪同步与可靠提醒',
        'description':'同步已保存消息，检查同步状态，创建不重复执行的提醒。',
        'tools':[
            {'name':'flush_messages','description':'重试待同步消息，不发送聊天回复。','parameters':[]},
            {'name':'sync_status','description':'查看待同步、已接收和冲突数量，不读取聊天正文。','parameters':[]},
            {'name':'guard_task','description':'工作流内部防重复执行检查。','parameters':[
                {'name':'task_id','type':'string','required':True,'description':'稳定的逻辑任务ID'},
                {'name':'cooldown_seconds','type':'number','required':False,'description':'周期任务最小间隔；不填则只领取一次'}]},
            {'name':'schedule_one_time_task','description':'创建一次性提醒，同一个请求不会重复创建或触发。','parameters':[
                {'name':'trigger_time','type':'string','required':True,'description':'手机本地触发时间，YYYY-MM-DD HH:mm:ss'},
                {'name':'message','type':'string','required':False,'description':'提醒内容'},
                {'name':'request_id','type':'string','required':False,'description':'同一提醒意图重试时保持相同ID'}]}]}
    native='/* METADATA\n'+json.dumps(native_meta,ensure_ascii=False,indent=2)+'\n*/\n'+source+'\n'+(ROOT/'operit/native_schedule.js').read_text(encoding='utf8')+'''
exports.sync_status=async function(){
 var db=endocrineOutbox(),cursor;
 try{cursor=db.rawQuery('SELECT status,COUNT(*) FROM messages GROUP BY status',null);var counts={};
 if(cursor.moveToFirst())do{counts[cursor.getString(0)]=cursor.getLong(1);}while(cursor.moveToNext());
 complete({success:true,counts:counts,messages_sent:0,chat_text_returned:false});
 }finally{if(cursor)cursor.close();db.close();}
};
'''
    files={'manifest.json':json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf-8'),
           'main.js':source.encode('utf-8'),'packages/expression_probe.js':probe.encode('utf-8'),
           'packages/native_endocrine.js':native.encode('utf8')}
    with zipfile.ZipFile(destination,'x',zipfile.ZIP_DEFLATED) as archive:
        for name,data in sorted(files.items()):
            info=zipfile.ZipInfo(name,(2026,10,18,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,data)
    sha=hashlib.sha256(Path(destination).read_bytes()).hexdigest()
    Path(str(destination)+'.sha256').write_bytes((sha+'  '+Path(destination).name+'\n').encode())
    print(json.dumps({'path':str(destination),'sha256':sha,'files':len(files)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('destination');args=parser.parse_args();build(args.destination)
