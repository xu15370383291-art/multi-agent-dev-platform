// Source inventory only; no submitted script is executed here.
const fs=require('node:fs');
const ts=require('../../frontend/node_modules/typescript/lib/typescript.js');
const crypto=require('node:crypto');
const tests=JSON.parse(fs.readFileSync(0,'utf8'));
const result=tests.map((test,case_index)=>{
 const source=test.script, tree=ts.createSourceFile('case.js',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.JS);
 const anchors=[];
 const preview=text=>Array.from(text).slice(0,180).join('');
 function unreachable(n){
  for(let child=n,parent=n.parent;parent;child=parent,parent=parent.parent){
   if(ts.isIfStatement(parent)&&((parent.expression.kind===ts.SyntaxKind.FalseKeyword&&child===parent.thenStatement)||
      (parent.expression.kind===ts.SyntaxKind.TrueKeyword&&child===parent.elseStatement)))return true;
   if(ts.isWhileStatement(parent)&&parent.expression.kind===ts.SyntaxKind.FalseKeyword&&child===parent.statement)return true;
  }
  return false;
 }
 function add(n,kind){
  const start=tree.getLineAndCharacterOfPosition(n.getStart(tree));
  const end=tree.getLineAndCharacterOfPosition(n.end);
  anchors.push({kind,line:start.line+1,column:start.character+1,end_line:end.line+1,end_column:end.character+1,
    statically_unreachable:unreachable(n),
    source_hash:crypto.createHash('sha256').update(n.getText(tree)).digest('hex'),
    excerpt:preview(n.getText(tree)),excerpt_truncated:Array.from(n.getText(tree)).length>180,
    ...(kind==='assertion'?{observed:preview(n.arguments[0]?.getText(tree)||''),expected:preview(n.arguments[1]?.getText(tree)||'')}:{})});
 }
 function visit(n){
  if(ts.isCallExpression(n))add(n,/^assert(?:\.|\[|$)/.test(n.expression.getText(tree))?'assertion':'operation');
  if(ts.isIfStatement(n)||ts.isVariableDeclaration(n))add(n,'precondition');
  ts.forEachChild(n,visit);
 }
 visit(tree);
 return {case_index,criterion_id:test.criterion_id,anchors};
});
process.stdout.write(JSON.stringify(result));
