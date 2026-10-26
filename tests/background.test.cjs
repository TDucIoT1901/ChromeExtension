const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const core=require('../extension/core.js');
const idle=()=>new Promise(resolve=>setImmediate(resolve));
const create=title=>({type:'notes:create',input:{title}});
function harness(initial=[]) {
  let data=structuredClone(initial),listener;
  const writes=[],control={failRead:false,failWrite:false};
  const sender={id:'test',url:'chrome-extension://test/popup.html',origin:'chrome-extension://test'};
  function restart(){
    const runtime={id:'test',getURL:p=>'chrome-extension://test/'+p,onMessage:{addListener:f=>{listener=f;}}};
    const chrome={runtime,storage:{local:{
      get(keys,cb){queueMicrotask(()=>{runtime.lastError=control.failRead?{message:'read failed'}:undefined;control.failRead=false;cb({pocket_notes_data:structuredClone(data)});runtime.lastError=undefined;});},
      set(value,cb){writes.push(structuredClone(value));queueMicrotask(()=>{const failed=control.failWrite;control.failWrite=false;if(!failed)data=structuredClone(value.pocket_notes_data);runtime.lastError=failed?{message:'write failed'}:undefined;cb();runtime.lastError=undefined;});}
    }}};
    const context=vm.createContext({chrome,URL,console});
    context.importScripts=file=>vm.runInContext(fs.readFileSync(require.resolve('../extension/'+file),'utf8'),context);
    vm.runInContext(fs.readFileSync(require.resolve('../extension/background.js'),'utf8'),context);
  }
  restart();return {control,writes,sender,restart,data:()=>structuredClone(data),invoke:(m,s,cb)=>listener(structuredClone(m),s,cb),request:m=>new Promise(resolve=>listener(structuredClone(m),sender,resolve))};
}
test('concurrent creates retain both notes',async()=>{
  const h=harness();const r=await Promise.all([h.request(create('A')),h.request(create('B'))]);assert.ok(r.every(x=>x.ok));assert.deepEqual(h.data().map(n=>n.title).sort(),['A','B']);
});
test('concurrent creates enforce latest 300-note limit',async()=>{
  const h=harness(Array.from({length:299},(_,i)=>({...core.createNote({title:'N'}),id:'n'+i})));const r=await Promise.all([h.request(create('A')),h.request(create('B'))]);assert.equal(r.filter(x=>x.ok).length,1);assert.equal(h.data().length,300);assert.equal(h.writes.length,1);
});
test('read/write failures do not poison queue',async()=>{
  const h=harness();h.control.failRead=true;assert.equal((await h.request(create('A'))).ok,false);h.control.failWrite=true;assert.equal((await h.request(create('B'))).ok,false);assert.equal((await h.request(create('C'))).ok,true);assert.deepEqual(h.data().map(n=>n.title),['C']);
});
test('worker restart reads persisted data',async()=>{
  const h=harness();await h.request(create('Persistent'));h.restart();assert.equal((await h.request({type:'notes:list'})).notes[0].title,'Persistent');
});
test('stale update and delete cannot overwrite a newer edit',async()=>{
  const n=core.createNote({title:'Original'}),h=harness([n]);assert.equal((await h.request({type:'notes:update',id:n.id,snapshot:n,input:{title:'Changed'}})).ok,true);
  for(const type of ['notes:update','notes:delete']){const r=await h.request({type,id:n.id,snapshot:n,input:{title:'Lost'}});assert.equal(r.code,'CONFLICT_MODIFIED');}assert.equal(h.data()[0].title,'Changed');assert.equal(h.writes.length,1);
});
test('stale edit cannot resurrect a deleted note',async()=>{
  const n=core.createNote({title:'Original'}),h=harness([n]);await h.request({type:'notes:delete',id:n.id,snapshot:n});const r=await h.request({type:'notes:update',id:n.id,snapshot:n,input:{title:'Lost'}});assert.equal(r.code,'CONFLICT_DELETED');assert.equal(h.data().length,0);
});
test('snapshot compares content even with equal timestamps',async()=>{
  const n=core.createNote({title:'Original'}),h=harness([{...n,title:'Changed'}]);const r=await h.request({type:'notes:update',id:n.id,snapshot:n,input:{title:'Overwrite'}});assert.equal(r.code,'CONFLICT_MODIFIED');assert.equal(h.writes.length,0);
});
test('unknown messages and untrusted senders never write',async()=>{
  const h=harness();assert.equal(h.invoke({type:'unknown'},h.sender,()=>{}),false);for(const s of [{...h.sender,id:'other'},{...h.sender,tab:{id:1}},{...h.sender,url:'https://example.org/'}])assert.equal(h.invoke(create('Rejected'),s,()=>{}),false);await idle();assert.equal(h.writes.length,0);
});
test('closed response channel does not prevent persistence',async()=>{
  const h=harness();assert.equal(h.invoke(create('Saved'),h.sender,()=>{throw Error('closed');}),true);await idle();assert.equal(h.data()[0].title,'Saved');assert.equal((await h.request(create('Next'))).ok,true);
});
test('invalid/duplicate stored records are never overwritten',async()=>{
  const n=core.createNote({title:'Existing'});for(const data of [null,[null],[n,n]]){const h=harness(data);assert.equal((await h.request(create('Bad'))).ok,false);assert.equal(h.writes.length,0);}
});
