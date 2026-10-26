/* METADATA
{
  "name": "endocrine_bridge",
  "display_name": "情绪事件桥接 v5.3",
  "description": "使用稳定事件ID记录一次经历；密钥从手机本地配置读取。",
  "tools": [
    {
      "name": "analyze",
      "description": "分析一个真实消息事件。应传入上游消息ID或逻辑事件ID以支持跨调用防重。",
      "parameters": [
        {
          "name": "message",
          "type": "string",
          "required": true,
          "description": "待分析消息"
        },
        {
          "name": "event_id",
          "type": "string",
          "required": false,
          "description": "稳定逻辑事件ID；重试保持不变"
        },
        {
          "name": "source_message_id",
          "type": "string",
          "required": false,
          "description": "Operit原始消息ID"
        },
        {
          "name": "conversation_id",
          "type": "string",
          "required": false,
          "description": "对话ID"
        },
        {
          "name": "agent_id",
          "type": "string",
          "required": false,
          "description": "稳定角色ID，默认example_agent"
        },
        {
          "name": "actor_id",
          "type": "string",
          "required": false,
          "description": "稳定的人物ID"
        }
      ]
    },
    {
      "name": "snapshot",
      "description": "只读获取当前情绪，不新增经历。",
      "parameters": [
        {
          "name": "agent_id",
          "type": "string",
          "required": false,
          "description": "稳定角色ID"
        }
      ]
    },
    {
      "name": "reset",
      "description": "重置需通过离线迁移或新角色ID显式处理。",
      "parameters": []
    },
    {
      "name": "record_response",
      "description": "记录已实际生成的角色回复全文，供后续语义归因使用。只记上下文，不发送消息。应由生成完成回调调用。",
      "parameters": [
        {
          "name": "message",
          "type": "string",
          "required": true,
          "description": "实际回复完整正文"
        },
        {
          "name": "event_id",
          "type": "string",
          "required": false,
          "description": "稳定回复事件ID，重试保持不变"
        },
        {
          "name": "source_message_id",
          "type": "string",
          "required": false,
          "description": "真实回复消息ID，和event_id至少一个"
        },
        {
          "name": "conversation_id",
          "type": "string",
          "required": true,
          "description": "与来信一致的会话ID"
        },
        {
          "name": "agent_id",
          "type": "string",
          "required": false,
          "description": "稳定角色ID"
        }
      ]
    },
    {
      "name": "semantic_memory",
      "description": "云端理解完整查询和记忆段落，按语义检索；不新增经历。",
      "parameters": [
        {
          "name": "query",
          "type": "string",
          "required": true,
          "description": "完整问题或段落，无须挑关键词"
        },
        {
          "name": "agent_id",
          "type": "string",
          "required": false,
          "description": "稳定角色ID"
        },
        {
          "name": "actor_id",
          "type": "string",
          "required": false,
          "description": "限制人物"
        },
        {
          "name": "conversation_id",
          "type": "string",
          "required": false,
          "description": "限制会话"
        },
        {
          "name": "limit",
          "type": "number",
          "required": false,
          "description": "返回条数，默认5"
        }
      ]
    },
    {
      "name": "prepare_expression",
      "description": "只读获取当前会话的语气、句式与可选口癖建议，供回复生成前使用。不会分析新来信或发送消息。",
      "parameters": [
        {
          "name": "agent_id",
          "type": "string",
          "required": true,
          "description": "稳定角色ID"
        },
        {
          "name": "actor_id",
          "type": "string",
          "required": true,
          "description": "当前说话人ID"
        },
        {
          "name": "conversation_id",
          "type": "string",
          "required": true,
          "description": "真实会话ID"
        },
        {
          "name": "source_event_id",
          "type": "string",
          "required": false,
          "description": "可选：已分析的当前来信事件ID；校验人物、会话及已应用状态"
        }
      ]
    },
    {
      "name": "reflect_memory",
      "description": "按完整原始会话审查人物判断，保留支持/反证，不修改人格。",
      "parameters": [
        {
          "name": "agent_id",
          "type": "string",
          "required": true,
          "description": "agent_id"
        },
        {
          "name": "actor_id",
          "type": "string",
          "required": true,
          "description": "actor_id"
        },
        {
          "name": "conversation_id",
          "type": "string",
          "required": true,
          "description": "conversation_id"
        },
        {
          "name": "source_event_id",
          "type": "string",
          "required": true,
          "description": "source_event_id"
        },
        {
          "name": "hypothesis",
          "type": "string",
          "required": true,
          "description": "hypothesis"
        }
      ]
    },
    {
      "name": "review_reply",
      "description": "审查候选回复是否虚构前情或违背人物边界；不发送。",
      "parameters": [
        {
          "name": "agent_id",
          "type": "string",
          "required": true,
          "description": "agent_id"
        },
        {
          "name": "actor_id",
          "type": "string",
          "required": true,
          "description": "actor_id"
        },
        {
          "name": "conversation_id",
          "type": "string",
          "required": true,
          "description": "conversation_id"
        },
        {
          "name": "source_event_id",
          "type": "string",
          "required": true,
          "description": "source_event_id"
        },
        {
          "name": "reply",
          "type": "string",
          "required": true,
          "description": "reply"
        }
      ]
    },
    {
      "name": "generate_reviewed_reply",
      "description": "从已处理的最新来信生成并审查回复，最多修正一次；失败不返回草稿。不发送、不自动记录。",
      "parameters": [
        {
          "name": "agent_id",
          "type": "string",
          "required": true,
          "description": "agent_id"
        },
        {
          "name": "actor_id",
          "type": "string",
          "required": true,
          "description": "actor_id"
        },
        {
          "name": "conversation_id",
          "type": "string",
          "required": true,
          "description": "conversation_id"
        },
        {
          "name": "source_event_id",
          "type": "string",
          "required": true,
          "description": "source_event_id"
        }
      ]
    }
  ]
}
*/
(function () {
  'use strict';
  var CONFIG_PATH = '/sdcard/Download/Operit/.endocrine-client.json';

  function generatedCallId() {
    return 'call:' + Date.now().toString(36) + ':' + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
  }

  function unwrap(raw) {
    for (var depth = 0; depth < 6; depth++) {
      if (typeof raw === 'string') {
        try { raw = JSON.parse(raw.trim()); } catch (e) { return {success:false,error:'non_json_tool_output'}; }
      } else if (raw && typeof raw === 'object') {
        if (raw.success === true || raw.error || raw.success === false || raw.hormones || raw.engine || raw.status === 'pending') return raw;
        var fields = ['data', 'output', 'stdout', 'result'];
        var found = false;
        for (var i = 0; i < fields.length; i++) {
          if (typeof raw[fields[i]] === 'string') { raw = raw[fields[i]]; found = true; break; }
        }
        if (!found) return {success:false,error:'unsupported_tool_wrapper'};
      } else return {success:false,error:'empty_tool_output'};
    }
    return {success:false,error:'excessive_tool_wrapping'};
  }

  async function request(method, path, body, slow) {
    function bounded(execution, timeoutMs) {
      var timer;
      return Promise.race([execution, new Promise(function (resolve, reject) {
        timer=setTimeout(function () { reject(new Error('native_terminal_deadline')); },timeoutMs);
      })]).finally(function () { clearTimeout(timer); });
    }
    // Send the script on stdin in one isolated command. Never use run_python's
    // shared /tmp/temp_script.py. Network retries retain the exact event body.
    var literal = body ? JSON.stringify(JSON.stringify(body)) + ".encode('utf-8')" : 'None';
    var script = [
      'import json, time, urllib.request, urllib.error, urllib.parse',
      'try:',
      '    with open(' + JSON.stringify(CONFIG_PATH) + ", encoding='utf-8') as f: cfg = json.load(f)",
      "    base = cfg['api_base'].rstrip('/')",
      '    u = urllib.parse.urlsplit(base)',
      "    if u.scheme != 'https' and not (u.scheme == 'http' and u.hostname in ('127.0.0.1', 'localhost')): raise ValueError('HTTPS required')",
      '    payload = ' + literal,
      '    class NoRedirect(urllib.request.HTTPRedirectHandler):',
      '        def redirect_request(self, *args, **kwargs): return None',
      '    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())',
      '    result = None',
      '    for attempt in range(' + (slow ? '1' : '2') + '):',
      '        try:',
      '            req = urllib.request.Request(base + ' + JSON.stringify(path) + ", data=payload, headers={'X-Token':cfg['token'], 'Content-Type':'application/json'}, method=" + JSON.stringify(method) + ')',
      '            with opener.open(req, timeout=' + (slow ? '160' : '90') + ') as r: result = json.loads(r.read(1048576))',
      '            break',
      '        except urllib.error.HTTPError as e:',
      '            try: result = json.loads(e.read(65536))',
      "            except ValueError: result = {'success':False, 'error':'http_'+str(e.code)}",
      '            break',
      '        except (urllib.error.URLError, TimeoutError, ConnectionError):',
      "            if attempt or " + (slow ? "True" : "False") + ": result = {'success':False, 'error':'transport_unavailable_delivery_unknown'}",
      '            else: time.sleep(0.2)',
      '    print(json.dumps(result, ensure_ascii=False))',
      'except Exception:',
      "    print(json.dumps({'success':False, 'error':'client_configuration_or_transport_error'}))"
    ].join('\n');
    var delimiter = '__ENDOCRINE_HTTP_V4_EOF__';
    if (script.split('\n').indexOf(delimiter) !== -1) return {success:false,error:'unsafe_script_delimiter'};
    var command = "( python3 - <<'" + delimiter + "'\n" + script + '\n' + delimiter + '\n)';
    try {
      var execution = await bounded(Tools.System.terminal.hiddenExec(command,
        {executorKey:'endocrine_http_v4',timeoutMs:slow ? 170000 : 190000}), slow ? 170000 : 190000);
      if (!execution || execution.exitCode !== 0 || execution.timedOut === true) {
        return {success:false,error:'transport_execution_failed_delivery_unknown'};
      }
      return unwrap(execution.output);
    } catch (e) { return {success:false,error:'transport_executor_unavailable_delivery_unknown',
      reason:e && e.message==='native_terminal_deadline' ? 'native_terminal_deadline' : 'executor_unavailable'}; }
  }

  exports.analyze = async function (params) {
    params = params || {};
    if (typeof params.message !== 'string' || !params.message.trim()) {
      complete({success:false,error:'message_required'}); return;
    }
    var stable = !!(params.event_id || params.source_message_id);
    var eventId = params.event_id || (params.source_message_id ?
      'operit:' + (params.conversation_id || 'default') + ':' + params.source_message_id : generatedCallId());
    var agentId = params.agent_id || 'example_agent';
    var body = {msg:params.message, event_id:eventId, agent_id:agentId, actor_id:params.actor_id || 'example_user'};
    if (params.conversation_id) body.conversation_id = params.conversation_id;
    var result = await request('POST', '/emotion', body);
    if (result.success === true && (result.event_id !== eventId || result.agent_id !== agentId)) {
      result = {success:false,error:'server_receipt_identity_mismatch',requested_event_id:eventId,requested_agent_id:agentId};
    } else if (result.success !== true) {
      result.requested_event_id = eventId;
    }
    result.client_dedupe_scope = stable ? 'logical_event' : 'this_call_only';
    if (result.status === 'pending') result.notice = '仍在处理，查询或重试时复用同一event_id';
    complete(result);
  };
  exports.snapshot = async function (params) {
    var agentId = params && params.agent_id || 'example_agent';
    var result = await request('GET', '/snapshot?agent_id=' + encodeURIComponent(agentId), null);
    if (result.success === true && result.agent_id !== agentId) result = {success:false,error:'server_receipt_identity_mismatch'};
    complete(result);
  };
  exports.record_response = async function (params) {
    params = params || {};
    if (typeof params.message !== 'string' || !params.message.trim() || !params.conversation_id || !(params.event_id || params.source_message_id)) {
      complete({success:false,error:'actual_reply_text_conversation_and_stable_id_required'}); return;
    }
    var agent = params.agent_id || 'example_agent';
    var eid = params.event_id || ('operit-assistant:' + params.conversation_id + ':' + params.source_message_id);
    var result = await request('POST','/v2/messages',{agent_id:agent,event_id:eid,role:'assistant',text:params.message,conversation_id:params.conversation_id});
    if (result.success === true && (result.event_id !== eid || result.agent_id !== agent)) result={success:false,error:'server_receipt_identity_mismatch'};
    complete(result);
  };
  exports.semantic_memory = async function (params) {
    params = params || {};
    if (typeof params.query !== 'string' || !params.query.trim()) { complete({success:false,error:'query_required'}); return; }
    var body={agent_id:params.agent_id || 'example_agent',query:params.query,limit:params.limit || 5};
    if (params.actor_id) body.actor_id=params.actor_id;
    if (params.conversation_id) body.conversation_id=params.conversation_id;
    var result=await request('POST','/v2/memory/search',body);
    if (result.success === true && !Array.isArray(result.items)) result={success:false,error:'invalid_memory_response'};
    complete(result);
  };
  exports.prepare_expression = async function (params) {
    params = params || {};
    var keys = ['agent_id','actor_id','conversation_id'];
    if (keys.some(function (key) { return typeof params[key] !== 'string' || !params[key].trim(); })) {
      complete({success:false,error:'expression_scope_required'}); return;
    }
    var body = {agent_id:params.agent_id,actor_id:params.actor_id,conversation_id:params.conversation_id};
    if (params.source_event_id) body.source_event_id = params.source_event_id;
    var result = await request('POST','/v2/expression/context',body);
    if (result.success === true && (keys.some(function (key) { return result[key] !== body[key]; }) ||
        result.source_event_id !== (body.source_event_id || null) ||
        !result.expression || result.expression.version !== 'expression-v1' ||
        typeof result.prompt_suffix !== 'string' || !result.prompt_suffix)) {
      result = {success:false,error:'server_receipt_identity_mismatch'};
    }
    complete(result);
  };
  exports.reflect_memory = async function (params) {
    params = params || {};
    var keys = ["agent_id", "actor_id", "conversation_id", "source_event_id", "hypothesis"];
    if (keys.some(function(k) {return typeof params[k] !== 'string' || !params[k].trim();})) {
      complete({success:false,error:'grounding_scope_and_content_required'}); return;
    }
    var body = {}; keys.forEach(function(k) {body[k]=params[k];});
    var result = await request('POST', '/v2/memory/reflect', body, false);
    var scope = ['agent_id','actor_id','conversation_id','source_event_id'];
    if (result.success === true && (scope.some(function(k) {return result[k] !== body[k];}) ||
        result.state_mutated !== false || result.messages_sent !== 0 || typeof result.evidence_fingerprint !== 'string' ||
        !Array.isArray(result.evidence))) result={success:false,error:'server_receipt_identity_mismatch'};
    complete(result);
  };
  exports.review_reply = async function (params) {
    params = params || {};
    var keys = ["agent_id", "actor_id", "conversation_id", "source_event_id", "reply"];
    if (keys.some(function(k) {return typeof params[k] !== 'string' || !params[k].trim();})) {
      complete({success:false,error:'grounding_scope_and_content_required'}); return;
    }
    var body = {}; keys.forEach(function(k) {body[k]=params[k];});
    var result = await request('POST', '/v2/expression/review', body, false);
    var scope = ['agent_id','actor_id','conversation_id','source_event_id'];
    if (result.success === true && (scope.some(function(k) {return result[k] !== body[k];}) ||
        result.state_mutated !== false || result.messages_sent !== 0 || typeof result.evidence_fingerprint !== 'string' ||
        typeof result.accepted !== 'boolean' || !result.review)) result={success:false,error:'server_receipt_identity_mismatch'};
    complete(result);
  };
  exports.generate_reviewed_reply = async function (params) {
    params = params || {};
    var keys = ["agent_id", "actor_id", "conversation_id", "source_event_id"];
    if (keys.some(function(k) {return typeof params[k] !== 'string' || !params[k].trim();})) {
      complete({success:false,error:'grounding_scope_and_content_required'}); return;
    }
    var body = {}; keys.forEach(function(k) {body[k]=params[k];});
    var result = await request('POST', '/v2/expression/respond', body, true);
    var scope = ['agent_id','actor_id','conversation_id','source_event_id'];
    if (result.success === true && (scope.some(function(k) {return result[k] !== body[k];}) ||
        result.state_mutated !== false || result.messages_sent !== 0 || typeof result.evidence_fingerprint !== 'string' ||
        result.status !== 'model_reviewed' || typeof result.reply !== 'string' || !result.reply)) result={success:false,error:'server_receipt_identity_mismatch'};
    complete(result);
  };
  exports.reset = async function () {
    complete({success:false,error:'reset_requires_explicit_offline_migration_or_new_agent'});
  };
})();
