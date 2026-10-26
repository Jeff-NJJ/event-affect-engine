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
