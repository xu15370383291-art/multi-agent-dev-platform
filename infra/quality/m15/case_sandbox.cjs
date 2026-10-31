// QA receives browser capabilities, not the Node process that owns the evidence.
const vm=require('node:vm');
const {performance}=require('node:perf_hooks');
const nativeApply=Reflect.apply, nativeGet=Reflect.get;
const capabilities=require('./capabilities.json');
const forbidden=new Set(['constructor','__proto__','prototype','caller','callee','arguments']);

function executeCase(script, bindings, filename, options={}){
 const realm=vm.createContext(Object.create(null),{codeGeneration:{strings:false,wasm:false}});
 const factory=vm.runInContext(`({parse:s=>JSON.parse(s),error:(name,message)=>{const C=({Error,TypeError,RangeError,SyntaxError,ReferenceError})[name]||Error;return new C(message)},logger:record=>Object.freeze({log:(...a)=>record('log',a),warn:(...a)=>record('warn',a),error:(...a)=>record('error',a)}),clock:now=>Object.freeze({now}),date:n=>new Date(n),bytes:a=>new Uint8Array(a),freeze:value=>{const freeze=v=>{if(v&&typeof v==='object'){Object.values(v).forEach(freeze);Object.freeze(v)}};freeze(value);return value}})`,realm);
 const containers=vm.runInContext('({array:()=>[],record:()=>({}),objectPrototype:Object.prototype})',realm);
 const proxies=new WeakMap(), originals=new WeakMap();
 const unwrap=value=>value&&(typeof value==='object'||typeof value==='function')&&originals.has(value)?originals.get(value):value;
 function wrap(value,receiver,method=''){
  if(value===null||!['object','function'].includes(typeof value))return value;
  if(originals.has(value))return value;
  if(value instanceof Error)return factory.error(value.name,value.message);
  if(value instanceof Date)return factory.date(value.getTime());
  if(value instanceof Uint8Array)return factory.bytes(Array.from(value));
  const proto=Object.getPrototypeOf(value);
  if(Array.isArray(value)||proto===Object.prototype||proto===containers.objectPrototype||proto===null){
   if(proxies.has(value))return proxies.get(value);
   const copy=Array.isArray(value)?containers.array():containers.record();
   proxies.set(value,copy);
   // JSON cloning drops Locator/ElementHandle methods (and may serialize
   // internal fields). Data containers belong to the realm, handles retain
   // the same constructor/private-property membrane as scalar returns.
   for(const key of Object.keys(value))Object.defineProperty(copy,key,{value:wrap(value[key]),enumerable:true,writable:true,configurable:true});
   if(Array.isArray(value))copy.length=value.length;
   return copy;
  }
  if(typeof value==='function'){
   const target=Object.setPrototypeOf((...args)=>{
    try{
     // Browser-evaluated functions must retain their source for Playwright's
     // serializer. Every host-invoked callback receives membrane-wrapped values,
     // including waitForEvent predicates nested inside an options object.
     const remote=new Set(['evaluate','evaluateHandle','$eval','$$eval','waitForFunction','addInitScript']);
     const callback=fn=>(...incoming)=>unwrap(fn(...incoming.map(v=>wrap(v))));
     const converted=args.map(arg=>{
      if(remote.has(method))return unwrap(arg);
      if(typeof arg==='function')return callback(arg);
      if(arg&&typeof arg==='object'&&!originals.has(arg)&&typeof arg.predicate==='function')return {...arg,predicate:callback(arg.predicate)};
      return unwrap(arg);
     });
     return wrap(nativeApply(value,receiver,converted));
    }catch(error){throw factory.error(error.name,String(error.message));}
   },null);
   const proxy=new Proxy(target,{get(_t,key){if(forbidden.has(key)||typeof key==='string'&&key.startsWith('_'))throw factory.error('TypeError','QA capability property is not available');try{return wrap(nativeGet(value,key,value),value,String(key))}catch(error){throw factory.error(error.name,String(error.message))}},defineProperty(){throw factory.error('TypeError','QA capability is read only')},setPrototypeOf(){return false},set(){throw factory.error('TypeError','QA capability is read only')},getPrototypeOf(){return null}});
   originals.set(proxy,value);return proxy;
  }
  if(proxies.has(value))return proxies.get(value);
  const proxy=new Proxy(Object.create(null),{get(_t,key){if(forbidden.has(key)||typeof key==='string'&&key.startsWith('_'))throw factory.error('TypeError','QA capability property is not available');try{return wrap(nativeGet(value,key,value),value,String(key))}catch(error){throw factory.error(error.name,String(error.message))}},set(){throw factory.error('TypeError','QA capability is read only')},getPrototypeOf(){return null}});
  proxies.set(value,proxy);originals.set(proxy,value);return proxy;
 }
 for(const [name,value] of Object.entries(bindings)){
  if(!capabilities.entry_bindings.includes(name))throw new Error('qa_binding_not_declared:'+name);
  Object.defineProperty(realm,name,{value:name==='unitEvidence'?factory.freeze(wrap(value)):wrap(value),writable:false,configurable:false});
 }
 // Keep waiting/logging APIs capability-safe; timer handles are numeric IDs.
 const timers=new Map();let next=0;
 const schedule=(callback,ms,...args)=>{const id=++next;timers.set(id,setTimeout(()=>{timers.delete(id);callback(...args)},ms));return id};
 Object.defineProperty(realm,'setTimeout',{value:wrap(schedule)});
 Object.defineProperty(realm,'clearTimeout',{value:wrap(id=>{clearTimeout(timers.get(id));timers.delete(id)})});
 Object.defineProperty(realm,'performance',{value:factory.clock(wrap(()=>performance.now()))});
 Object.defineProperty(realm,'console',{value:factory.logger(wrap((level,args)=>{if(options.onConsole)options.onConsole(level,args)}))});
 const compiled=new vm.Script(`(async()=>{${script}\n})()`,{filename});
 return Promise.resolve(compiled.runInContext(realm)).finally(()=>{for(const timer of timers.values())clearTimeout(timer)});
}
module.exports={executeCase};
