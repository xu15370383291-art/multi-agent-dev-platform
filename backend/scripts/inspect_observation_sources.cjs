// Parse only: submitted application code is never evaluated on the host.
const fs=require('node:fs');
const ts=require('../../frontend/node_modules/typescript/lib/typescript.js');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const casesMode=input.mode==='case-observations-v1'&&Array.isArray(input.tests)&&input.files&&typeof input.files==='object';
const files=casesMode?input.files:input;
const result=[];
const adapters=new Set();
function walk(node,visit){visit(node);ts.forEachChild(node,n=>walk(n,visit));}
function path(n){
  if(ts.isIdentifier(n))return n.text;
  if(ts.isPropertyAccessExpression(n))return path(n.expression)+'.'+n.name.text;
  if(ts.isElementAccessExpression(n)&&ts.isStringLiteral(n.argumentExpression))return path(n.expression)+'.'+n.argumentExpression.text;
  return '';
}
for(const [name,source] of Object.entries(files).sort()){
  if(!/\.(?:html|js|mjs|ts|tsx)$/.test(name))continue;
  const chunks=/\.html$/.test(name)?[...source.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script\s*>/gi)].map(m=>({text:m[1],offset:m.index+m[0].indexOf('>')+1})):[{text:source,offset:0}];
  for(const chunk of chunks){
    const f=ts.createSourceFile(name,chunk.text,ts.ScriptTarget.Latest,true,/\.tsx$/.test(name)?ts.ScriptKind.TSX:ts.ScriptKind.TS);
    walk(f,n=>{
      if(!ts.isBinaryExpression(n)||n.operatorToken.kind!==ts.SyntaxKind.EqualsToken||!ts.isObjectLiteralExpression(n.right)||!/^(?:window|globalThis)\.[\w$]+$/.test(path(n.left)))return;
      const getter=n.right.properties.find(p=>p.name&&p.name.getText(f)==='getState'&&(ts.isMethodDeclaration(p)||ts.isPropertyAssignment(p)&&(ts.isArrowFunction(p.initializer)||ts.isFunctionExpression(p.initializer))));
      if(!getter)return;
      adapters.add(path(n.left).replace(/^globalThis\./,'window.'));
      const own=new Set();
      walk(getter.body||getter.initializer,p=>{if(ts.isPropertyAccessExpression(p)&&p.expression.kind===ts.SyntaxKind.ThisKeyword)own.add(p.name.text);});
      if(!own.size)return; // A bridge to another store is not classified as an owned store.
      result.push({global:path(n.left),path:name,line:source.slice(0,chunk.offset+n.getStart(f)).split('\n').length,
        method:'getState',state_fields:[...own].sort(),source_excerpt:getter.getText(f),
        observation_scope:'object_owned_state',runtime_binding:'not_established_by_this_declaration'});
    });
  }
}
if(casesMode){
 const cases=input.tests.map((test,index)=>{
  const f=ts.createSourceFile('case.js',test.script,ts.ScriptTarget.Latest,true,ts.ScriptKind.JS);
  const aliases=new Map(),used=new Set();
  const resolve=n=>{const p=path(n).replace(/^globalThis\./,'window.');const [first,...tail]=p.split('.');return aliases.has(first)?[aliases.get(first),...tail].join('.'):p;};
  walk(f,n=>{
   if(ts.isVariableDeclaration(n)&&ts.isIdentifier(n.name)&&n.initializer){const p=resolve(n.initializer);if(/^window\./.test(p))aliases.set(n.name.text,p);}
   const p=resolve(n);
   const getter=p.match(/^(window\.[\w$]+)\.getState(?:\.|$)/);
   if(getter)used.add(getter[1]); // Classes/imported controllers need not use an object literal.
   for(const a of adapters)if(p===a||p.startsWith(a+'.'))used.add(a);
  });
  return {case_index:index,helper_globals:[...used].sort()};
 });
 process.stdout.write(JSON.stringify(cases));
}else process.stdout.write(JSON.stringify(result));
