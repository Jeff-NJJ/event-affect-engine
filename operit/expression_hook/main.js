'use strict';

// Additive ToolPkg hook for the pinned Operit 1.12.2 prompt-finalize contract.
// No event creation, generation, send operation or cloud-model invocation here.
function expressionRecent(payload) {
  var history = payload.preparedHistory === undefined ? payload.chatHistory : payload.preparedHistory;
  if (history === undefined) return [];
  if (!Array.isArray(history)) throw new Error('invalid_prompt_history');
  var recent = history.filter(function (turn) { return turn && turn.kind === 'ASSISTANT'; })
    .slice(-8).map(function (turn) {
      if (typeof turn.content !== 'string' || turn.content.length > 8000) throw new Error('expression_history_too_large');
      return turn.content;
    });
  if (recent.reduce(function (n, text) { return n + text.length; }, 0) > 16000) throw new Error('expression_history_too_large');
  return recent;
}

function expressionPython(payload) {
  // A hard-bounded subprocess covers DNS, connect and trickled response bytes.
  var worker = [
    'import json, sys, urllib.request, urllib.parse',
    'p=json.load(sys.stdin)',
    'class NoRedirect(urllib.request.HTTPRedirectHandler):',
    '    def redirect_request(self,*args,**kwargs): raise ValueError("redirect_rejected")',
    'u=urllib.parse.urlsplit(p["base"])',
    'if u.username or u.password or u.query or u.fragment or not u.hostname: raise ValueError("invalid_base")',
    'if u.scheme!="https" and not (u.scheme=="http" and u.hostname in ("127.0.0.1","localhost")): raise ValueError("https_required")',
    'opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())',
    'req=urllib.request.Request(p["base"].rstrip("/")+"/v2/expression/context",data=json.dumps(p["body"],ensure_ascii=False).encode(),headers={"X-Token":p["token"],"Content-Type":"application/json"},method="POST")',
    'with opener.open(req,timeout=3) as r: data=r.read(65537)',
    'if len(data)>65536: raise ValueError("response_too_large")',
    'result=json.loads(data)',
    'for key in ("agent_id","actor_id","conversation_id"):',
    '    if result.get(key)!=p["body"][key]: raise ValueError("receipt_identity_mismatch")',
    'if result.get("success") is not True or result.get("expression",{}).get("version")!="expression-v1": raise ValueError("unsupported_expression_response")',
    'result["binding_card_id"]=p["card_id"]',
    'print(json.dumps(result,ensure_ascii=False))'
  ].join('\n');
  return [
    'import json, subprocess, sys',
    'try:',
    '    p=json.loads(' + JSON.stringify(JSON.stringify(payload)) + ')',
    '    with open("/sdcard/Download/Operit/.endocrine-client.json",encoding="utf-8") as f: cfg=json.load(f)',
    '    routes=cfg.get("expression_routes",[])',
    '    if not isinstance(routes,list): raise ValueError("invalid_routes")',
    '    matches=[r for r in routes if isinstance(r,dict) and r.get("chat_id")==p["chat_id"] and r.get("card_id")==p["card_id"]]',
    '    if not matches:',
    '        print(json.dumps({"success":True,"status":"unbound"}))',
    '    else:',
    '        if len(matches)!=1: raise ValueError("ambiguous_route")',
    '        route=matches[0]',
    '        for key in ("agent_id","actor_id"):',
    '            if not isinstance(route.get(key),str) or not route[key].strip(): raise ValueError("invalid_route")',
    '        body={"agent_id":route["agent_id"],"actor_id":route["actor_id"],"conversation_id":p["chat_id"],"recent_assistant":p["recent_assistant"]}',
    '        request={"body":body,"base":cfg["api_base"],"token":cfg["token"],"card_id":p["card_id"]}',
    '        completed=subprocess.run([sys.executable,"-c",' + JSON.stringify(worker) + '],input=json.dumps(request).encode(),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=4)',
    '        if completed.returncode!=0: raise ValueError("expression_transport_or_receipt_failed")',
    '        print(completed.stdout.decode("utf-8"))',
    'except Exception:',
    '    print(json.dumps({"success":False,"error":"expression_configuration_transport_or_receipt_failed"}))'
  ].join('\n');
}

function decodeExpression(raw) {
  for (var depth = 0; depth < 6; depth++) {
    if (typeof raw === 'string') { raw = JSON.parse(raw.trim()); continue; }
    if (!raw || typeof raw !== 'object') break;
    if (typeof raw.success === 'boolean') return raw;
    var fields = ['data', 'output', 'stdout', 'result'];
    var next = fields.find(function (key) { return typeof raw[key] === 'string'; });
    if (!next) break;
    raw = raw[next];
  }
  throw new Error('invalid_expression_envelope');
}

function expressionMetadata(payload, status, extra) {
  var metadata = Object.assign({}, payload.metadata || {});
  metadata.endocrineExpression = Object.assign({version: 'expression-v1', status: status}, extra || {});
  return metadata;
}

// Primary Android transport. Secrets stay in the app process and never become
// toolCall parameters, shell arguments, package resources, or diagnostics.
function expressionReadConfig() {
  var File = Java.type('java.io.File');
  var file = new File('/sdcard/Download/Operit/.endocrine-client.json');
  if (!file.isFile() || file.length() > 65536) throw new Error('expression_config_unavailable');
  var input = new (Java.type('java.io.FileInputStream'))(file);
  var reader = new (Java.type('java.io.BufferedReader'))(
    new (Java.type('java.io.InputStreamReader'))(input, 'UTF-8'));
  var lines = [], total = 0;
  try {
    var line;
    while ((line = reader.readLine()) !== null) {
      total += line.length + 1;
      if (total > 65536) throw new Error('expression_config_too_large');
      lines.push(line);
    }
  } finally { reader.close(); }
  var text = lines.join('\n');
  var cfg = JSON.parse(text);
  if (!cfg || typeof cfg !== 'object' || Array.isArray(cfg)) throw new Error('expression_invalid_config');
  return cfg;
}

function expressionEndpoint(cfg) {
  if (typeof cfg.api_base !== 'string') throw new Error('expression_invalid_base');
  var base = cfg.api_base.replace(/\/+$/, '');
  var uri = new (Java.type('java.net.URI'))(base);
  var scheme = uri.getScheme(), host = uri.getHost(), port = uri.getPort();
  if (!host || uri.getUserInfo() !== null || uri.getRawQuery() !== null || uri.getRawFragment() !== null ||
      (port !== -1 && (port < 1 || port > 65535)) || uri.normalize().toString() !== base)
    throw new Error('expression_invalid_base');
  if (scheme !== 'https' && !(scheme === 'http' &&
      (host === 'localhost' || host === '127.0.0.1' || cfg.allow_http_base === base)))
    throw new Error('expression_http_base_not_approved');
  return base + '/v2/expression/context';
}

async function expressionNativeContext(request) {
  var cfg = expressionReadConfig();
  if (!Array.isArray(cfg.expression_routes)) throw new Error('expression_invalid_routes');
  var routes = cfg.expression_routes.filter(function(route) {
    return route && route.chat_id === request.chat_id && route.card_id === request.card_id;
  });
  if (routes.length === 0) return {success:true, status:'unbound'};
  if (routes.length !== 1) throw new Error('expression_ambiguous_route');
  var route = routes[0];
  ['agent_id', 'actor_id'].forEach(function(key) {
    if (typeof route[key] !== 'string' || !route[key].trim()) throw new Error('expression_invalid_route');
  });
  if (typeof cfg.token !== 'string' || !cfg.token.trim() || /[\r\n]/.test(cfg.token))
    throw new Error('expression_invalid_token');
  var url = expressionEndpoint(cfg);
  if (typeof request.query === 'string') url=url.replace(/\/v2\/expression\/context$/, '/v2/operit/context');
  var body = {agent_id:route.agent_id, actor_id:route.actor_id, conversation_id:request.chat_id,
    recent_assistant:request.recent_assistant};
  if (typeof request.query === 'string') body.query=request.query;
  var TimeUnit = Java.type('java.util.concurrent.TimeUnit');
  var builder = new (Java.type('okhttp3.OkHttpClient$Builder'))();
  builder.callTimeout(5, TimeUnit.SECONDS);
  builder.connectTimeout(3, TimeUnit.SECONDS);
  builder.readTimeout(3, TimeUnit.SECONDS);
  builder.followRedirects(false);
  builder.followSslRedirects(false);
  builder.retryOnConnectionFailure(false);
  builder.proxy(Java.type('java.net.Proxy').NO_PROXY);
  var client = builder.build();
  var media = Java.type('okhttp3.MediaType').parse('application/json; charset=utf-8');
  var content = Java.type('okhttp3.RequestBody').create(media, JSON.stringify(body));
  var httpRequest = new (Java.type('okhttp3.Request$Builder'))().url(url)
    .header('X-Token', cfg.token).post(content).build();
  var call = client.newCall(httpRequest), timer;
  try {
    var result = await new Promise(function(resolve, reject) {
      timer = setTimeout(function() { call.cancel(); reject(new Error('expression_http_deadline')); }, 6000);
      call.enqueue(Java.implement('okhttp3.Callback', {
        onFailure: function() { reject(new Error('expression_http_failed')); },
        onResponse: function(c, response) {
          try {
            if (response.code() !== 200) throw new Error('expression_http_status');
            var responseBody = response.body();
            if (!responseBody || responseBody.contentLength() > 65536) throw new Error('expression_response_too_large');
            var source = responseBody.source();
            source.request(65537);
            var buffer = source.getBuffer();
            if (buffer.size() > 65536) throw new Error('expression_response_too_large');
            var value = JSON.parse(buffer.readUtf8());
            if (!value || value.success !== true) throw new Error('expression_invalid_response');
            ['agent_id', 'actor_id', 'conversation_id'].forEach(function(key) {
              if (value[key] !== body[key]) throw new Error('expression_receipt_identity_mismatch');
            });
            if (!value.expression || value.expression.version !== 'expression-v1')
              throw new Error('expression_invalid_version');
            value.binding_card_id = request.card_id;
            resolve(value);
          } catch(e) { reject(e); }
          finally { response.close(); }
        }
      }));
    });
    return result;
  } finally {
    clearTimeout(timer);
    call.cancel();
    // Each short-lived client owns its pool/executor; release both on every exit.
    client.connectionPool().evictAll();
    client.dispatcher().executorService().shutdown();
  }
}

async function expressionAwait(execution, timeoutMs) {
  // Native terminal initialization happens before its own command timeout.
  // Bound the whole await in JS so a broken terminal cannot hang prompt hooks.
  var timer;
  try {
    return await Promise.race([execution, new Promise(function (resolve, reject) {
      timer = setTimeout(function () { reject(new Error('expression_native_deadline')); }, timeoutMs);
    })]);
  } finally { clearTimeout(timer); }
}

async function endocrineSystemStringHook(event) {
  if (event.eventName !== 'before_finalize_prompt') return null;
  var payload = event.eventPayload;
  if (!payload) return null;
  var original = payload.systemPrompt;
  if (typeof original !== 'string') return null;
  // Another plugin may append after our block. Locate only the exact recorded
  // block, at its recorded offset when possible, or at a unique exact match.
  // An ambiguous match is not permission to delete character or plugin text.
  var old = payload.metadata && payload.metadata.endocrineExpression;
  var removed = false;
  if (old && typeof old.appended === 'string' && old.appended) {
    var at = -1;
    if (Number.isInteger(old.offset) && old.offset >= 0 &&
        original.slice(old.offset, old.offset + old.appended.length) === old.appended) at = old.offset;
    else {
      var first = original.indexOf(old.appended);
      if (first >= 0 && first === original.lastIndexOf(old.appended)) at = first;
    }
    if (at >= 0) {
      original = original.slice(0, at) + original.slice(at + old.appended.length);
      removed = true;
    } else if (original.indexOf(old.appended) >= 0) {
      console.error('endocrine_expression_block_ambiguous');
      return {metadata: expressionMetadata(payload, 'ambiguous_previous_block', {
        appended: old.appended, offset: old.offset
      })};
    }
  }
  var active = payload.metadata && payload.metadata.activePrompt;
  if ((payload.promptFunctionType || payload.functionType) !== 'CHAT' ||
      !active || active.type !== 'character_card' || typeof active.id !== 'string' || !active.id ||
      typeof payload.chatId !== 'string' || !payload.chatId)
    return removed ? {systemPrompt: original, metadata: expressionMetadata(payload, 'not_applicable')} : null;
  try {
    try{await endocrineFlushOne(expressionReadConfig(),1);}catch(syncError){console.error('endocrine_pending_sync_deferred');}
    var request = {chat_id: payload.chatId, card_id: active.id, recent_assistant: expressionRecent(payload),
      query:expressionQuery(payload)};
    var result = await expressionNativeContext(request);
    if (result.success === true && result.status === 'unbound')
      return {systemPrompt: original, metadata: expressionMetadata(payload, 'unbound')};
    if (result.success !== true || result.conversation_id !== payload.chatId || result.binding_card_id !== active.id ||
        !result.expression || result.expression.version !== 'expression-v1' ||
        typeof result.prompt_suffix !== 'string' || !result.prompt_suffix || result.prompt_suffix.length > 24000)
      throw new Error('invalid_expression_receipt');
    var appended = '\n\n' + result.prompt_suffix;
    return {systemPrompt: original + appended, metadata: expressionMetadata(payload, 'injected', {
      agent_id: result.agent_id, actor_id: result.actor_id, conversation_id: result.conversation_id,
      revision: result.persisted_revision, source_status: result.source_status, appended: appended,
      offset: original.length, memory_context:result.memory_context, state_basis:result.state_basis
    })};
  } catch (error) {
    // Explicit diagnostic; never retain stale mood after a failed refresh.
    console.error('endocrine_expression_hook_failed');
    return {systemPrompt: original, metadata: expressionMetadata(payload, 'failed', {
      reason: error && error.message === 'expression_http_deadline' ? 'native_http_deadline' : 'configuration_transport_or_receipt'
    })};
  }
}

async function endocrineExpressionHook(event){
  if(event.eventName==='before_send_to_model'){endocrineAuditFinalPrompt(event.eventPayload);return null;}
  if(event.eventName!=='before_finalize_prompt' || !event.eventPayload)return null;
  var p=event.eventPayload;
  if(typeof p.systemPrompt==='string')return endocrineSystemStringHook(event);
  // Real Operit finalizes preparedHistory and may leave systemPrompt null.
  // Mutating an unused systemPrompt field would silently miss the model request.
  if(!Array.isArray(p.preparedHistory))return null;
  var systems=[];
  p.preparedHistory.forEach(function(t,i){if(t && t.kind==='SYSTEM' && typeof t.content==='string')systems.push(i);});
  if(systems.length===0)return {metadata:expressionMetadata(p,'missing_prepared_system_turn')};
  var previous=p.metadata && p.metadata.endocrineExpression;
  var owned=previous && typeof previous.appended==='string' && previous.appended?
    systems.filter(function(i){return p.preparedHistory[i].content.indexOf(previous.appended)>=0;}):[];
  if(owned.length>1)return {metadata:expressionMetadata(p,'ambiguous_previous_block')};
  var index=owned.length===1?owned[0]:systems[0];
  var proxy=Object.assign({},p,{systemPrompt:p.preparedHistory[index].content});
  var result=await endocrineSystemStringHook(Object.assign({},event,{eventPayload:proxy}));
  if(!result)return null;
  if(typeof result.systemPrompt==='string'){
    var history=p.preparedHistory.slice();
    history[index]=Object.assign({},history[index],{content:result.systemPrompt});
    result.preparedHistory=history;delete result.systemPrompt;
  }
  if(result.metadata && result.metadata.endocrineExpression)result.metadata.endocrineExpression.prompt_target='prepared_history_system';
  return result;
}

function registerToolPkg() {
  ToolPkg.registerPromptFinalizeHook({id: 'endocrine_expression_v1', function: endocrineExpressionHook});
  ToolPkg.registerChatMessageHook({id:'endocrine_message_sync_v2',function:endocrineMessageHook});
  ToolPkg.registerAppLifecycleHook({id:'endocrine_reconnect_sync_v2',event:'application_on_create',function:endocrineStartupSync});
  return true;
}
exports.registerToolPkg = registerToolPkg;
exports.endocrineExpressionHook = endocrineExpressionHook;
exports.expressionPython = expressionPython;
exports.expressionNativeContext = expressionNativeContext;

function expressionQuery(payload) {
  if (typeof payload.processedInput==='string' && payload.processedInput.trim()) return payload.processedInput;
  var turns=payload.preparedHistory===undefined ? payload.chatHistory : payload.preparedHistory;
  if (!Array.isArray(turns)) return '';
  for(var i=turns.length-1;i>=0;i--) if(turns[i].kind==='USER' && typeof turns[i].content==='string') return turns[i].content;
  return '';
}

function endocrineOutbox() {
  var path=Java.getApplicationContext().getFilesDir().getAbsolutePath()+'/endocrine-native-v2.sqlite3';
  var db=Java.type('android.database.sqlite.SQLiteDatabase').openOrCreateDatabase(path,null);
  db.execSQL('CREATE TABLE IF NOT EXISTS messages(local_id TEXT PRIMARY KEY,body TEXT NOT NULL,status TEXT NOT NULL,receipt TEXT)');
  return db;
}

function endocrineMessageBody(payload,route,now) {
  if (payload.sender!=='user' && payload.sender!=='ai') return null;
  if (typeof payload.content!=='string' || !payload.content.trim()) return null;
  if (payload.content.length>8000) throw new Error('native_message_too_large');
  if (!Number.isSafeInteger(payload.timestamp) || payload.timestamp<=0) throw new Error('invalid_message_timestamp');
  if (payload.sender==='ai' && (!Number.isSafeInteger(payload.completedAt) || payload.completedAt<=0)) return null;
  return {agent_id:route.agent_id,actor_id:route.actor_id,conversation_id:payload.chatId,
    message_id:payload.sender+':'+payload.timestamp,role:payload.sender==='user'?'user':'assistant',
    occurred_at:new Date(payload.timestamp).toISOString(),client_now:new Date(now).toISOString(),text:payload.content};
}

async function endocrinePost(cfg,body,timeoutSeconds) {
  if(timeoutSeconds===undefined)timeoutSeconds=4;
  var url=expressionEndpoint(cfg).replace(/\/v2\/expression\/context$/, '/v2/operit/messages');
  var unit=Java.type('java.util.concurrent.TimeUnit'),builder=new (Java.type('okhttp3.OkHttpClient$Builder'))();
  builder.callTimeout(timeoutSeconds,unit.SECONDS);builder.connectTimeout(Math.min(2,timeoutSeconds),unit.SECONDS);builder.readTimeout(Math.min(3,timeoutSeconds),unit.SECONDS);
  builder.followRedirects(false);builder.followSslRedirects(false);builder.retryOnConnectionFailure(false);
  builder.proxy(Java.type('java.net.Proxy').NO_PROXY);
  var client=builder.build(),media=Java.type('okhttp3.MediaType').parse('application/json; charset=utf-8');
  var req=new (Java.type('okhttp3.Request$Builder'))().url(url).header('X-Token',cfg.token)
    .post(Java.type('okhttp3.RequestBody').create(media,JSON.stringify(body))).build();
  var call=client.newCall(req),timer;
  try{return await new Promise(function(resolve,reject){
    timer=setTimeout(function(){call.cancel();reject(new Error('native_sync_deadline'));},timeoutSeconds*1000+500);
    call.enqueue(Java.implement('okhttp3.Callback',{
      onFailure:function(){reject(new Error('native_sync_transport_failed'));},
      onResponse:function(c,response){try{
        var code=response.code();
        if(code!==200 && code!==202)throw new Error(code===409?'native_sync_conflict':'native_sync_http_failed');
        var rb=response.body();if(!rb || rb.contentLength()>16384)throw new Error('native_sync_response_limit');
        var source=rb.source();source.request(16385);var buffer=source.getBuffer();
        if(buffer.size()>16384)throw new Error('native_sync_response_limit');
        var r=JSON.parse(buffer.readUtf8());
        if(!r || r.success!==true || r.accepted_durably!==true || r.event_id!==endocrineEventId(body) ||
          ['agent_id','actor_id','conversation_id','message_id'].some(function(k){return r[k]!==body[k];}))throw new Error('native_sync_receipt_mismatch');
        resolve(r);
      }catch(e){reject(e);}finally{response.close();}}
    }));
  });}finally{clearTimeout(timer);call.cancel();client.connectionPool().evictAll();client.dispatcher().executorService().shutdown();}
}

async function endocrineFlushOne(cfg,timeoutSeconds) {
  var db=endocrineOutbox(),cursor,record,id;
  try{
    cursor=db.rawQuery("SELECT local_id,body FROM messages WHERE status='pending' ORDER BY rowid",null);
    if(!cursor.moveToFirst())return {status:'idle'};
    do{
      var candidate=JSON.parse(cursor.getString(1));
      var bound=cfg.expression_routes.filter(function(r){return r.chat_id===candidate.conversation_id && r.agent_id===candidate.agent_id && r.actor_id===candidate.actor_id;});
      if(bound.length===1){id=cursor.getString(0);record=candidate;break;}
    }while(cursor.moveToNext());
  }finally{if(cursor)cursor.close();db.close();}
  if(!record)return {status:'pending_route_review'};
  // Sample the clock when sending, so offline time is not erased on reconnect.
  // occurred_at remains immutable; server dedupe excludes this fresh clock sample.
  record.client_now=new Date().toISOString();
  var response;
  try{response=await endocrinePost(cfg,record,timeoutSeconds);}catch(e){
    if(e.message==='native_sync_conflict'){
      db=endocrineOutbox();try{db.execSQL("UPDATE messages SET status='conflict' WHERE local_id=? AND status='pending'",[id]);}finally{db.close();}
      return {status:'conflict_requires_review'};
    }
    throw e;
  }
  db=endocrineOutbox();try{db.execSQL("UPDATE messages SET status='accepted',receipt=? WHERE local_id=? AND status='pending'",[response.event_id,id]);}finally{db.close();}
  return {status:'accepted',event_id:response.event_id};
}

async function endocrineMessageHook(event) {
  if(event.eventName!=='message_persisted')return null;
  var p=event.eventPayload;
  if(!p || typeof p.chatId!=='string')return null;
  try{
    var cfg=expressionReadConfig();
    var routes=cfg.expression_routes.filter(function(r){return r.chat_id===p.chatId;});
    if(routes.length===0)return {status:'unbound'};
    if(routes.length!==1)throw new Error('native_sync_ambiguous_route');
    // Check the persisted chat's own card, not whichever chat is currently open.
    var appdb=Java.type('com.ai.assistance.operit.data.db.AppDatabase').getDatabase(Java.getApplicationContext());
    var summary=await appdb.chatDao().callSuspend('getChatById',p.chatId);
    if(!summary || !Object.prototype.hasOwnProperty.call(routes[0],'persisted_character_card_name') ||
      summary.getCharacterCardName()!==routes[0].persisted_character_card_name ||
      summary.getCharacterGroupId()!==routes[0].persisted_character_group_id)return {status:'card_scope_mismatch'};
    var body=endocrineMessageBody(p,routes[0],Date.now());
    if(body===null)return {status:'not_a_completed_message'};
    var id=JSON.stringify([body.agent_id,body.conversation_id,body.message_id]);
    var db=endocrineOutbox(),cursor;
    try{
      db.beginTransaction();
      cursor=db.rawQuery('SELECT body FROM messages WHERE local_id=?',[id]);
      if(cursor.moveToFirst()){
        var old=JSON.parse(cursor.getString(0));
        if(old.text!==body.text || old.role!==body.role || old.occurred_at!==body.occurred_at)throw new Error('native_sync_message_edit_conflict');
      }else db.execSQL("INSERT INTO messages VALUES(?,?,'pending',NULL)",[id,JSON.stringify(body)]);
      db.setTransactionSuccessful();
    }finally{if(cursor)cursor.close();if(db.inTransaction())db.endTransaction();db.close();}
    return await endocrineFlushOne(cfg);
  }catch(e){console.error('endocrine_message_sync_failed');return {status:'pending_or_conflict',retry_is_idempotent:true};}
}
exports.endocrineMessageHook=endocrineMessageHook;
exports.endocrineMessageBody=endocrineMessageBody;
exports.endocrineFlushOne=endocrineFlushOne;
function endocrineEventId(body){
  return 'operit-native:'+endocrineDigest(JSON.stringify([body.agent_id,body.conversation_id,body.role,body.message_id]));
}
function endocrineDigest(text){
  var bytes=new (Java.type('java.lang.String'))(text).getBytes('UTF-8');
  var hash=Java.type('java.security.MessageDigest').getInstance('SHA-256').digest(bytes);
  return new (Java.type('java.math.BigInteger'))(1,hash).call('toString',16).padStart(64,'0');
}
exports.endocrineEventId=endocrineEventId;
function endocrineAuditFinalPrompt(payload){
  if(!payload || (payload.promptFunctionType||payload.functionType)!=='CHAT')return;
  try{
    var state=payload.metadata && payload.metadata.endocrineExpression;
    var systems=Array.isArray(payload.preparedHistory)?payload.preparedHistory.filter(function(t){return t.kind==='SYSTEM' && typeof t.content==='string';}).map(function(t){return t.content;}):[];
    if(typeof payload.systemPrompt==='string')systems.push(payload.systemPrompt);
    var audit={stage:'before_send_to_model',at_utc:new Date().toISOString(),
      status:state?state.status:'not_injected',agent_id:state&&state.agent_id,
      owned_block_present:!!state && typeof state.appended==='string' && systems.some(function(text){return text.indexOf(state.appended)>=0;}),
      memory_context:state&&state.memory_context,state_basis:state&&state.state_basis,
      prompt_fingerprint:endocrineDigest(JSON.stringify(systems)),prompt_text_stored:false};
    var file=new (Java.type('java.io.File'))(Java.getApplicationContext().getFilesDir().getAbsolutePath()+'/endocrine_last_prompt_audit.json');
    var writer=new (Java.type('java.io.OutputStreamWriter'))(new (Java.type('java.io.FileOutputStream'))(file),'UTF-8');
    try{writer.write(JSON.stringify(audit));}finally{writer.close();}
  }catch(e){console.error('endocrine_prompt_audit_unavailable');}
}
async function endocrineStartupSync(){
  try{return await endocrineFlushOne(expressionReadConfig());}
  catch(e){console.error('endocrine_startup_sync_deferred');return {status:'pending'};}
}
exports.endocrineStartupSync=endocrineStartupSync;
exports.flush_messages=async function(){
  var accepted=0,conflicts=0,status='idle';
  try{
    var cfg=expressionReadConfig();
    for(var i=0;i<8;i++){
      var r=await endocrineFlushOne(cfg);status=r.status;
      if(status==='accepted')accepted++;
      else if(status==='conflict_requires_review')conflicts++;
      else break;
    }
    complete({success:true,status:status,accepted:accepted,conflicts:conflicts,messages_sent:0});
  }catch(e){console.error('endocrine_background_sync_deferred');complete({success:false,status:'pending',accepted:accepted,
    error:e&&/^native_|^expression_/.test(e.message)?e.message:'native_sync_runtime_unavailable',messages_sent:0});}
};
