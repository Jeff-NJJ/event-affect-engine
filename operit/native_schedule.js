'use strict';
function nativeScheduleDatabase(){
 var path=Java.getApplicationContext().getFilesDir().getAbsolutePath()+'/endocrine-native-schedule-v2.sqlite3';
 var db=Java.type('android.database.sqlite.SQLiteDatabase').openOrCreateDatabase(path,null);
 db.execSQL('CREATE TABLE IF NOT EXISTS tasks(task_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,status TEXT NOT NULL,token TEXT NOT NULL,workflow_id TEXT)');
 db.execSQL('CREATE TABLE IF NOT EXISTS guards(task_id TEXT PRIMARY KEY,claimed_at INTEGER NOT NULL)');
 return db;
}
function nativeScheduleHash(value){
 var sorted={};Object.keys(value).sort().forEach(function(k){sorted[k]=value[k];});
 var bytes=new (Java.type('java.lang.String'))(JSON.stringify(sorted)).getBytes('UTF-8');
 var result=Java.type('java.security.MessageDigest').getInstance('SHA-256').digest(bytes);
 return new (Java.type('java.math.BigInteger'))(1,result).call('toString',16).padStart(64,'0');
}
function endocrineNativeRegistry(operation,p){
 var scope={chat_id:p.chat_id,card_id:p.card_id,trigger_time:p.trigger_time,message:p.message};
 var fingerprint=nativeScheduleHash(scope);
 var identity=p.request_id?{chat_id:p.chat_id,card_id:p.card_id,request_id:p.request_id}:scope;
 var task_id=nativeScheduleHash(identity),db=nativeScheduleDatabase(),cursor;
 try{
  db.beginTransaction();cursor=db.rawQuery('SELECT fingerprint,status,token,workflow_id FROM tasks WHERE task_id=?',[task_id]);
  var old=null;
  if(cursor.moveToFirst())old={fingerprint:cursor.getString(0),status:cursor.getString(1),token:cursor.getString(2),workflow_id:cursor.isNull(3)?null:cursor.getString(3)};
  if(!old){
   var legacyPath='/sdcard/Download/Operit/.schedule_registry_v5.sqlite3';
   if(new (Java.type('java.io.File'))(legacyPath).exists()){
    var legacy=Java.type('android.database.sqlite.SQLiteDatabase').openDatabase(legacyPath,null,1),lc;
    try{lc=legacy.rawQuery('SELECT fingerprint,status,token,workflow_id FROM tasks WHERE task_id=?',[task_id]);
      if(lc.moveToFirst()){
       old={fingerprint:lc.getString(0),status:lc.getString(1),token:lc.getString(2),workflow_id:lc.isNull(3)?null:lc.getString(3)};
       db.execSQL('INSERT INTO tasks VALUES(?,?,?,?,?)',[task_id,old.fingerprint,old.status,old.token,old.workflow_id]);
      }
    }finally{if(lc)lc.close();legacy.close();}
   }
  }
  if(old && old.fingerprint!==fingerprint)throw new Error('schedule_request_conflict');
  var result;
  if(operation==='claim'){
   if(!old){var token=Java.type('java.util.UUID').randomUUID().toString();db.execSQL("INSERT INTO tasks VALUES(?,?,'claimed',?,NULL)",[task_id,fingerprint,token]);result={action:'create',token:token};}
   else result={action:old.status,token:old.token,workflow_id:old.workflow_id};
  }else{
   if(!old || old.token!==p.token)throw new Error('schedule_claim_lost');
   if(old.workflow_id && old.workflow_id!==p.workflow_id)throw new Error('schedule_workflow_conflict');
   if(operation!=='created' && operation!=='ready')throw new Error('invalid_schedule_operation');
   var status=old.status==='ready'?'ready':operation;
   db.execSQL('UPDATE tasks SET status=?,workflow_id=? WHERE task_id=?',[status,p.workflow_id,task_id]);
   result={action:status,workflow_id:p.workflow_id};
  }
  db.setTransactionSuccessful();return Object.assign({success:true,task_id:task_id,fingerprint:fingerprint},result);
 }finally{if(cursor)cursor.close();if(db.inTransaction())db.endTransaction();db.close();}
}
function nativeClaimTask(p){
 if(!p || typeof p.task_id!=='string' || !/^[a-f0-9]{64}$/.test(p.task_id))throw new Error('invalid_task_id');
 // Preserve claims made by earlier project versions; migration cannot reopen delivery.
 var legacy=new (Java.type('java.io.File'))('/sdcard/Download/Operit/.delivery_guard_v5/task_'+p.task_id);
 if(legacy.exists())return 'WF_GUARD_SKIP';
 var cooldown=p.cooldown_seconds===undefined?null:Number(p.cooldown_seconds);
 if(cooldown!==null && (!Number.isFinite(cooldown) || cooldown<1 || cooldown>31536000))throw new Error('invalid_guard_cooldown');
 var db=nativeScheduleDatabase(),cursor,now=Date.now();
 try{
  db.beginTransaction();cursor=db.rawQuery('SELECT claimed_at FROM guards WHERE task_id=?',[p.task_id]);
  if(cursor.moveToFirst()){
   var previous=cursor.getLong(0);
   if(cooldown===null || now<previous || now-previous<cooldown*1000)return 'WF_GUARD_SKIP';
   db.execSQL('UPDATE guards SET claimed_at=? WHERE task_id=?',[now,p.task_id]);
  }else db.execSQL('INSERT INTO guards VALUES(?,?)',[p.task_id,now]);
  db.setTransactionSuccessful();return 'WF_GUARD_GO';
 }finally{if(cursor)cursor.close();if(db.inTransaction())db.endTransaction();db.close();}
}
exports.guard_task=async function(p){try{complete(nativeClaimTask(p));}catch(e){console.error('native_workflow_guard_failed');complete('WF_GUARD_SKIP');}};
exports.nativeClaimTask=nativeClaimTask;
exports.endocrineNativeRegistry=endocrineNativeRegistry;

async function native_schedule_one_time_task(params,verifiedContext) {
        async function registry(operation,body){return endocrineNativeRegistry(operation,body);}
        if (!params.trigger_time) {
            throw new Error("trigger_time is required");
        }
        const resolvedChatId = verifiedContext ? verifiedContext.chat_id : getChatId();
        if (!resolvedChatId) {
            throw new Error("chat_id is required (call from a chat context)");
        }
        const resolvedRoleCardId = verifiedContext ? verifiedContext.card_id : getCallerCardId();
        const resolvedSenderName = verifiedContext ? verifiedContext.sender_name : getCallerName();
        const resolvedLang = verifiedContext ? verifiedContext.lang : getLang();
        if (params.request_id !== undefined && (typeof params.request_id !== 'string' || !params.request_id.trim() || params.request_id.length > 160))
            throw new Error('invalid_schedule_request_id');
        if (typeof params.trigger_time !== 'string' || params.trigger_time.length > 80 ||
            (params.message !== undefined && (typeof params.message !== 'string' || params.message.length > 16000)))
            throw new Error('invalid_schedule_parameters');
        const taskBody = {chat_id:resolvedChatId,card_id:resolvedRoleCardId || '',
            trigger_time:params.trigger_time,message:params.message || '',request_id:params.request_id || ''};
        const claim = await registry('claim', taskBody);
        if (claim.action === 'ready') return {success:true,message:'该提醒已经创建，无需重复创建',
            data:{workflow_id:claim.workflow_id,task_id:claim.task_id,reused:true}};
        const workflowName = resolvedLang?.toLowerCase()?.startsWith("zh")
            ? `一次性定时任务 ${params.trigger_time}`
            : `One-time scheduled task ${params.trigger_time}`;
        const description = resolvedLang?.toLowerCase()?.startsWith("zh")
            ? `一次性定时任务，触发时间 ${params.trigger_time}`
            : `One-time scheduled task at ${params.trigger_time}`;
        const timedTriggerTag = `[定时触发:${params.trigger_time}]\n[Scheduled Trigger:${params.trigger_time}]`;
        const defaultMessage = `[自动任务] 时间到了，开始执行一次性定时任务。现在是 ${params.trigger_time}\n[Automated Task] Time reached, start executing the one-time scheduled task. Current time is ${params.trigger_time}`;
        const messageContent = params.message
            ? `${params.message}\n${timedTriggerTag}`
            : `${defaultMessage}\n${timedTriggerTag}`;
        const taggedDescription = description + '\n[operit-task-v5:' + claim.task_id + ']';
        let workflowId = claim.workflow_id;
        if (claim.action === 'create') {
            const created = await Tools.Workflow.create(workflowName, taggedDescription, null, null, true);
            workflowId = created?.id;
        } else if (claim.action === 'claimed') {
            // Reconcile only an authoritative positive native receipt. Absence
            // is not proof a timed-out create will never complete.
            const listing = await Tools.Workflow.getAll();
            if (!listing || !Array.isArray(listing.workflows)) throw new Error('invalid_schedule_listing');
            const matches = listing.workflows.filter(w => w.description === taggedDescription);
            if (matches.length > 1) throw new Error('schedule_reconciliation_conflict');
            if (!matches.length) return {success:false,message:'该提醒正在处理或结果待核对，未重复创建',
                error:'schedule_creation_pending_reconciliation',data:{task_id:claim.task_id,workflow_id:null}};
            workflowId = matches[0].id;
        } else if (claim.action !== 'created') throw new Error('invalid_schedule_claim_state');
        if (!workflowId) {
            throw new Error("Failed to create workflow: missing workflow id");
        }
        if (claim.action !== 'created') await registry('created', {...taskBody,token:claim.token,workflow_id:workflowId});
        const uniqueId = claim.task_id.slice(0, 24);
        const triggerNodeId = `trigger_${uniqueId}`;
        const startNodeId = `start_${uniqueId}`;
        const sendNodeId = `send_${uniqueId}`;
        const deleteNodeId = `delete_${uniqueId}`;
        const sendMessageOptions = {
            hide_user_message: true,
            notify_reply: true,
            persist_turn: true,
            disable_warning: false,
        };
        const sendMessageArgs = [
            messageContent,
            resolvedChatId,
            resolvedRoleCardId || undefined,
            resolvedSenderName || undefined,
            sendMessageOptions
        ];
        const [typedMessage, typedChatId, typedRoleCardId, typedSenderName, typedOptions] = sendMessageArgs;
        const actionConfig = {
            message: typedMessage,
            chat_id: typedChatId,
            hide_user_message: true,
            notify_reply: true,
            persist_turn: true,
            disable_warning: false,
        };
        if (typedRoleCardId) {
            actionConfig.role_card_id = typedRoleCardId;
        }
        if (typedSenderName) {
            actionConfig.sender_name = typedSenderName;
        }
        const nodeTexts = resolvedLang?.toLowerCase()?.startsWith("zh")
            ? {
                triggerName: "定时触发",
                triggerDesc: "一次性定时触发",
                startName: "启动对话服务",
                startDesc: "启动对话服务以便发送消息",
                sendName: "唤醒AI",
                sendDesc: "向对话发送唤醒消息，触发AI执行任务",
                deleteName: "删除工作流",
                deleteDesc: "执行后删除自己"
            }
            : {
                triggerName: "Scheduled Trigger",
                triggerDesc: "One-time scheduled trigger",
                startName: "Start Chat Service",
                startDesc: "Start chat service to enable messaging",
                sendName: "Wake AI",
                sendDesc: "Send a wake message to trigger AI task execution",
                deleteName: "Delete Workflow",
                deleteDesc: "Delete itself after execution"
            };
        const nodes = [
            {
                id: triggerNodeId,
                type: "trigger",
                name: nodeTexts.triggerName,
                description: nodeTexts.triggerDesc,
                position: { x: 0, y: 0 },
                triggerType: "schedule",
                triggerConfig: {
                    schedule_type: "specific_time",
                    specific_time: params.trigger_time,
                    repeat: "false",
                    enabled: "true"
                }
            },
            {
                id: startNodeId,
                type: "execute",
                name: nodeTexts.startName,
                description: nodeTexts.startDesc,
                position: { x: 260, y: 0 },
                actionType: "start_chat_service",
                actionConfig: {
                    initial_mode: "BALL",
                    keep_if_exists: "true"
                }
            },
            {
                id: sendNodeId,
                type: "execute",
                name: nodeTexts.sendName,
                description: nodeTexts.sendDesc,
                position: { x: 520, y: 0 },
                actionType: "send_message_to_ai",
                actionConfig
            },
            {
                id: deleteNodeId,
                type: "execute",
                name: nodeTexts.deleteName,
                description: nodeTexts.deleteDesc,
                position: { x: 1040, y: 0 },
                actionType: "delete_workflow",
                actionConfig: {
                    workflow_id: workflowId
                }
            }
        ];
        const connections = [
            {
                sourceNodeId: triggerNodeId,
                targetNodeId: startNodeId,
                condition: "on_success"
            },
            {
                sourceNodeId: startNodeId,
                targetNodeId: sendNodeId,
                condition: "on_success"
            },
            {
                sourceNodeId: sendNodeId,
                targetNodeId: deleteNodeId,
                condition: "on_success"
            }
        ];
        // v4: claim before waking AI; repeated Worker attempts must not enqueue a new turn.
        // A claimed-but-interrupted delivery is deliberately not retried without reconciliation.
        const guardExecId = `guard_exec_${uniqueId}`;
        const guardExtractId = `guard_extract_${uniqueId}`;
        const guardConditionId = `guard_condition_${uniqueId}`;
        if (!/^[a-zA-Z0-9_-]+$/.test(workflowId)) throw new Error("Invalid workflow id");
        nodes.push(
            {id:guardExecId,type:"execute",name:"防重·原子领取",position:{x:80,y:180},actionType:"endocrine_native_v2:guard_task",actionConfig:{task_id:claim.task_id}},
            {id:guardExtractId,type:"extract",name:"提取防重结果",position:{x:260,y:180},source:{nodeId:guardExecId},expression:"WF_GUARD_(GO|SKIP)",group:1,defaultValue:"SKIP"},
            {id:guardConditionId,type:"condition",name:"仅领取成功放行",position:{x:440,y:180},left:{nodeId:guardExtractId},operator:"EQ",right:"GO"}
        );
        const triggerLink = connections.find(c => c.sourceNodeId === triggerNodeId && c.targetNodeId === startNodeId);
        if (!triggerLink) throw new Error("Missing trigger connection");
        triggerLink.sourceNodeId = guardConditionId;
        triggerLink.condition = "true";
        connections.push(
            {sourceNodeId:triggerNodeId,targetNodeId:guardExecId,condition:"on_success"},
            {sourceNodeId:guardExecId,targetNodeId:guardExtractId,condition:"on_success"},
            {sourceNodeId:guardExtractId,targetNodeId:guardConditionId,condition:"on_success"}
        );
        await Tools.Workflow.update(workflowId, {
            name: workflowName,
            description: taggedDescription,
            enabled: true,
            nodes,
            connections
        });
        await registry('ready', {...taskBody,token:claim.token,workflow_id:workflowId});
        return {
            success: true,
            message: "一次性提醒工作流已创建",
            data: {
                workflow_id: workflowId,
                task_id: claim.task_id,
                trigger_time: params.trigger_time,
                chat_id: resolvedChatId,
                role_card_id: resolvedRoleCardId || undefined,
                workflow_name: workflowName
            }
        };
    }
    /**
     * Set an alarm on the device
     * @param params - Parameters with alarm details
     */

exports.schedule_one_time_task=async function(params){try{complete(await native_schedule_one_time_task(params));}catch(e){console.error("native_schedule_failed");complete({success:false,error:String(e.message)});}};
