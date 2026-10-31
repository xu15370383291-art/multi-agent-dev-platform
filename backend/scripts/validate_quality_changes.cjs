// Parse only: never evaluate model/user JavaScript. Fail closed on unsupported edits.
const fs=require('node:fs');
const ts=require('../../frontend/node_modules/typescript/lib/typescript.js');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
function fail(){throw Error('unsupported_test_api_change')}
function parse(s){const f=ts.createSourceFile('case.js',s,ts.ScriptTarget.Latest,true,ts.ScriptKind.JS);if(f.parseDiagnostics.length)fail();return f}
function nodes(f){const out=[];function visit(n){out.push(n);ts.forEachChild(n,visit)}visit(f);return out}
function assertions(f){return nodes(f).filter(n=>ts.isCallExpression(n)&&/^assert(?:\.|\[|$)/.test(n.expression.getText(f))).map(n=>n.getText(f))}
function duplicateArrowArguments(n,f){
 let expression;
 if(ts.isExpressionStatement(n))expression=n.expression;
 else if(ts.isVariableStatement(n)&&n.declarationList.declarations.length===1){
  const d=n.declarationList.declarations[0];
  if(!ts.isIdentifier(d.name)||nodes(f).filter(x=>ts.isIdentifier(x)&&x.text===d.name.text).length!==1)return false;
  expression=d.initializer;
 }
 if(!expression||!ts.isAwaitExpression(expression))return false;
 const call=expression.expression, statements=n.parent.statements;
 if(!ts.isCallExpression(call)||call.expression.getText(f)!=='page.evaluate'||call.arguments.length!==2||!statements)return false;
 const fn=call.arguments[0];
 if(!ts.isArrowFunction(fn)||fn.parameters.length||!ts.isCallExpression(fn.body)||fn.body.arguments.length!==1)return false;
 if(!/^window\.[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+$/.test(fn.body.expression.getText(f)))return false;
 const bad=fn.body.arguments[0];
 if(!ts.isElementAccessExpression(bad)||bad.expression.getText(f)!=='arguments'||bad.argumentExpression.getText(f)!=='0')return false;
 const next=statements[statements.indexOf(n)+1];let value;
 if(next&&ts.isVariableStatement(next)&&next.declarationList.declarations.length===1)value=next.declarationList.declarations[0].initializer;
 else if(next&&ts.isExpressionStatement(next))value=next.expression;
 if(!value||!ts.isAwaitExpression(value)||!ts.isCallExpression(value.expression))return false;
 const correct=value.expression, g=correct.arguments[0];
 return correct.expression.getText(f)==='page.evaluate'&&correct.arguments.length===2&&correct.arguments[1].getText(f)===call.arguments[1].getText(f)&&
  ts.isArrowFunction(g)&&g.parameters.length===1&&ts.isIdentifier(g.parameters[0].name)&&ts.isCallExpression(g.body)&&
  g.body.expression.getText(f)===fn.body.expression.getText(f)&&g.body.arguments.length===1&&g.body.arguments[0].getText(f)===g.parameters[0].name.text;
}
function injectedAssert(n,f){
 if(n!==f.statements[0]||!ts.isVariableStatement(n)||!(n.declarationList.flags&ts.NodeFlags.Const)||n.declarationList.declarations.length!==1)return false;
 const d=n.declarationList.declarations[0],call=d.initializer;
 if(!ts.isIdentifier(d.name)||d.name.text!=='assert'||!call||!ts.isCallExpression(call)||call.questionDotToken||!ts.isIdentifier(call.expression)||call.expression.text!=='require'||call.arguments.length!==1||!ts.isStringLiteral(call.arguments[0])||!['assert/strict','node:assert/strict'].includes(call.arguments[0].text))return false;
 for(const x of nodes(f)){
  if(ts.isIdentifier(x)&&x.text==='require'&&x!==call.expression)return false;
  if(x!==d&&(ts.isVariableDeclaration(x)||ts.isParameter(x)||ts.isFunctionDeclaration(x)||ts.isClassDeclaration(x))&&x.name&&/\bassert\b/.test(x.name.getText(f)))return false;
  if(ts.isBinaryExpression(x)&&x.operatorToken.kind>=ts.SyntaxKind.FirstAssignment&&x.operatorToken.kind<=ts.SyntaxKind.LastAssignment&&/^assert(?:\.|\[|$)/.test(x.left.getText(f)))return false;
  if((ts.isPrefixUnaryExpression(x)||ts.isPostfixUnaryExpression(x)||ts.isDeleteExpression(x))&&/^assert(?:\.|\[|$)/.test(x.operand?.getText(f)||x.expression?.getText(f)||''))return false;
 }
 return true;
}
function discover(options){
 const source=parse(options.script),menu=[];
 for(const n of source.statements)if(injectedAssert(n,source))menu.push({kind:'injected_strict_assert',before:n.getText(source),after:'',contract:'quality harness injects measured Node strict assert; no assertion changes'});
 for(const n of nodes(source)){
  if(duplicateArrowArguments(n,source))menu.push({kind:'duplicate_arrow_arguments',before:n.getText(source),after:'',contract:'actual arguments ReferenceError before invocation; adjacent correctly parameterized identical call is preserved'});
  if(ts.isObjectLiteralExpression(n)&&n.properties.length===1&&ts.isCallExpression(n.parent)&&n.parent.expression.getText(source)==='page.evaluate'&&n.parent.arguments[1]===n){
   const fn=n.parent.arguments[0],prop=n.properties[0];
   if(ts.isArrowFunction(fn)&&fn.parameters.length===1&&ts.isIdentifier(fn.parameters[0].name)&&ts.isPropertyAssignment(prop)){
    const name=fn.parameters[0].name.text,uses=nodes(fn.body).filter(x=>ts.isIdentifier(x)&&x.text===name);
    if(prop.name.getText(source)===name&&uses.length&&uses.every(x=>ts.isCallExpression(x.parent)&&x.parent.arguments.includes(x)))
     menu.push({kind:'evaluate_argument_unpack',before:n.getText(source),after:prop.initializer.getText(source)});
   }
  }
 }
 const exports=[],defined=new Set();
 for(const raw of Object.values(options.files||{})){
  const scripts=raw.includes('<script')?[...raw.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].map(m=>m[1]):[raw];
  for(const script of scripts){let f;try{f=parse(script)}catch{continue}
   for(const n of nodes(f))if(ts.isBinaryExpression(n)&&n.operatorToken.kind===ts.SyntaxKind.EqualsToken&&/^window\.[\w$.]+$/.test(n.left.getText(f))){
    defined.add(n.left.getText(f));
    if(ts.isObjectLiteralExpression(n.right))for(const prop of n.right.properties)if(ts.isPropertyAssignment(prop)&&(/^[\w$]+$/).test(prop.name.getText(f))&&(ts.isFunctionExpression(prop.initializer)||ts.isArrowFunction(prop.initializer)))exports.push(n.left.getText(f)+'.'+prop.name.getText(f));
   }
  }
 }
 for(const n of nodes(source))if(ts.isPropertyAccessExpression(n)&&ts.isCallExpression(n.parent)&&n.parent.expression===n&&!defined.has(n.getText(source))&&/^window\.[\w$]+$/.test(n.getText(source)))
  for(const path of exports)if(path.split('.').at(-1)===n.name.text&&path!==n.getText(source))menu.push({kind:'method_receiver',before:n.getText(source),after:path});
 const grouped=[];for(const item of menu){const prior=grouped.find(x=>x.before===item.before&&x.after===item.after&&x.kind===item.kind);if(prior)prior.occurrences++;else grouped.push({...item,occurrences:1})}
 return grouped;
}
if(input.discover_batch){process.stdout.write(JSON.stringify(input.scripts.map(script=>discover({script,files:input.files}))));process.exit(0)}
if(input.discover){process.stdout.write(JSON.stringify(discover(input)));process.exit(0)}
let script=input.script;if(typeof script!=='string'||script.length>24000||input.changes.length>30)fail();
const originalAssertions=assertions(parse(script));const applied=[];
const expanded=input.changes.flatMap(c=>Array.from({length:c.occurrences||1},()=>c));if(expanded.length>60)fail();
for(const change of expanded){
 if(!change.before||change.before===change.after)fail();
 const f=parse(script),all=nodes(f);let matches=all.filter(n=>n.getText(f)===change.before);
 let chosen=null;
 for(const n of matches){
  if(change.kind==='injected_strict_assert'&&change.after===''&&injectedAssert(n,f)){chosen=n;break;}
  if(change.kind==='duplicate_arrow_arguments'&&change.after===''&&duplicateArrowArguments(n,f)){chosen=n;break;}
  if(change.kind==='evaluate_argument_unpack'&&ts.isObjectLiteralExpression(n)&&n.properties.length===1){
   const p=n.parent;if(!ts.isCallExpression(p)||p.arguments.length!==2||p.arguments[1]!==n||p.expression.getText(f)!=='page.evaluate')continue;
   const fn=p.arguments[0],prop=n.properties[0];
   if(!ts.isArrowFunction(fn)||fn.parameters.length!==1||!ts.isIdentifier(fn.parameters[0].name)||!ts.isPropertyAssignment(prop))continue;
   const name=fn.parameters[0].name.text;if(prop.name.getText(f)!==name||prop.initializer.getText(f)!==change.after)continue;
   const uses=nodes(fn.body).filter(x=>ts.isIdentifier(x)&&x.text===name);
   if(!uses.length||uses.some(x=>!ts.isCallExpression(x.parent)||!x.parent.arguments.includes(x)))continue;
   chosen=n;break;
  }
  if(change.kind==='method_receiver'&&ts.isPropertyAccessExpression(n)&&ts.isCallExpression(n.parent)&&n.parent.expression===n){
   const old=n.getText(f);if(!/^window\.[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*$/.test(old)||old.includes('assert'))continue;
   const newer=change.after;if(!/^window\.[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+$/.test(newer)||newer.includes('assert'))continue;
   if(old.split('.').at(-1)!==newer.split('.').at(-1))continue;
   chosen=n;break;
  }
 }
 if(!chosen)fail();
 // Ambiguous text occurrences must be split into individual AST-confirmed edits.
 const start=chosen.getStart(f),end=chosen.getEnd();
 script=script.slice(0,start)+change.after+script.slice(end);applied.push({kind:change.kind,before:change.before,after:change.after});
}
const finalAssertions=assertions(parse(script));
if(JSON.stringify(originalAssertions)!==JSON.stringify(finalAssertions)||!originalAssertions.length)fail();
process.stdout.write(JSON.stringify({script,assertions_unchanged:true,applied}));
