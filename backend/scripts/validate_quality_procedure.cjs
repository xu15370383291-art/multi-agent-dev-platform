// Static analysis only. Never execute submitted JavaScript on the host.
const fs = require('node:fs');
const ts = require('../../frontend/node_modules/typescript/lib/typescript.js');
const {inertWrapper} = require('../../infra/quality/contracts/script_entry.cjs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
function fail(code) { throw Error('quality_procedure_' + code); }
function parse(text) {
  if (typeof text !== 'string' || text.length > 16000) fail('script_invalid');
  // Parse in the same async-function-body context as the browser harness.
  const f = ts.createSourceFile('case.js', 'async function __qualityScript__(){\n'+text+'\n}', ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
  if (f.parseDiagnostics.length) fail('syntax_invalid');
  f.statements=f.statements[0].body.statements;
  return f;
}
function nodes(f) { const out=[]; function visit(n){out.push(n);ts.forEachChild(n,visit);}visit(f);return out; }
function isAssert(n,f) { return ts.isCallExpression(n) && /^assert(?:\.|\[|$)/.test(n.expression.getText(f)); }
function seq(haystack, needles) {
  const found=[]; let at=0;
  for(const needle of needles){while(at<haystack.length&&haystack[at]!==needle)at++;if(at===haystack.length)fail('assertions_changed');found.push(at++);}
  return found;
}
function fnOf(n) { for(let p=n.parent;p;p=p.parent)if(ts.isFunctionLike(p))return ts.isSourceFile(p.parent)?null:p;return null; }
function continueTarget(n) {
  if(!ts.isContinueStatement(n)||n.label)return null;
  for(let p=n.parent;p&&!ts.isFunctionLike(p);p=p.parent)
    if(ts.isForStatement(p)||ts.isForInStatement(p)||ts.isForOfStatement(p)||ts.isWhileStatement(p)||ts.isDoStatement(p))return p;
  return null;
}
function isBrowser(fn,f) {
  let p=fn.parent;
  return ts.isCallExpression(p) && /(?:^|\.)evaluate$/.test(p.expression.getText(f));
}
function awaitedIife(fn) {
  let expression=fn;while(ts.isParenthesizedExpression(expression.parent))expression=expression.parent;
  const call=expression.parent;
  return ts.isCallExpression(call)&&call.expression===expression&&ts.isAwaitExpression(call.parent);
}
function frame(n,f) {
  const fn=fnOf(n); if(!fn)return 'root';
  if(isBrowser(fn,f))return 'browser';
  if(awaitedIife(fn))return 'awaited_iife';
  return `${fn.kind}:${fn.name?.getText(f)||''}:${fn.parameters.map(p=>p.getText(f)).join(',')}:${ts.isCallExpression(fn.parent)?fn.parent.expression.getText(f):fn.parent.kind}`;
}
function gates(n,f) {
  const result=[];
  for(let p=n.parent;p;p=p.parent){
    if(ts.isFunctionLike(p))break;
    if(ts.isIfStatement(p)||ts.isConditionalExpression(p))result.push([p.kind,p.expression?.getText(f)||p.condition.getText(f), n.pos>=(p.elseStatement||p.whenFalse||p).pos && !!(p.elseStatement||p.whenFalse)]);
    if(ts.isForStatement(p)||ts.isWhileStatement(p)||ts.isDoStatement(p))result.push([p.kind,p.condition?.getText(f)]);
    if(ts.isForOfStatement(p)||ts.isForInStatement(p))result.push([p.kind,p.expression.getText(f)]);
    if(ts.isBinaryExpression(p)&&[ts.SyntaxKind.AmpersandAmpersandToken,ts.SyntaxKind.BarBarToken,ts.SyntaxKind.QuestionQuestionToken].includes(p.operatorToken.kind))result.push([p.operatorToken.kind,p.left.getText(f)]);
  }
  return JSON.stringify(result);
}
function literal(n) {
  return ts.isLiteralExpression(n)||[ts.SyntaxKind.TrueKeyword,ts.SyntaxKind.FalseKeyword,ts.SyntaxKind.NullKeyword].includes(n.kind)||
    (ts.isPrefixUnaryExpression(n)&&literal(n.operand))||
    (ts.isArrayLiteralExpression(n)&&n.elements.every(literal))||
    (ts.isObjectLiteralExpression(n)&&n.properties.every(p=>ts.isPropertyAssignment(p)&&literal(p.initializer)));
}
// A pure render-frame barrier observes the actual browser clock. It returns
// only the timestamp supplied by rAF, never invented DOM/game state.
function frameBarrier(fn) {
  const body=n=>{
    while(n&&ts.isParenthesizedExpression(n))n=n.expression;
    if(n&&ts.isBlock(n)&&n.statements.length===1){const s=n.statements[0];return ts.isReturnStatement(s)||ts.isExpressionStatement(s)?body(s.expression):null;}
    return n;
  };
  if(fn.parameters.length||/^(?:Promise|requestAnimationFrame)$/.test(fn.name?.text||''))return false;
  const promise=body(fn.body);
  if(!promise||!ts.isNewExpression(promise)||promise.expression.getText(newer)!=='Promise'||promise.arguments?.length!==1)return false;
  const executor=promise.arguments[0];
  if((!ts.isArrowFunction(executor)&&!ts.isFunctionExpression(executor))||/^(?:Promise|requestAnimationFrame)$/.test(executor.name?.text||''))return false;
  const parameter=executor.parameters[0];
  if(executor.parameters.length!==1||!ts.isIdentifier(parameter.name)||parameter.initializer||parameter.dotDotDotToken||['Promise','requestAnimationFrame','window'].includes(parameter.name.text))return false;
  function frame(n,depth){
    n=body(n);
    if(!n||depth>4||!ts.isCallExpression(n)||n.expression.getText(newer)!=='requestAnimationFrame'||n.arguments.length!==1)return false;
    const cb=n.arguments[0];
    if(ts.isIdentifier(cb))return cb.text===parameter.name.text;
    return (ts.isArrowFunction(cb)||ts.isFunctionExpression(cb))&&!cb.parameters.length&&!/^(?:Promise|requestAnimationFrame)$/.test(cb.name?.text||'')&&frame(cb.body,depth+1);
  }
  return frame(executor.body,0);
}
function failureObject(n,statement,f){
  if(!ts.isObjectLiteralExpression(n)||!n.properties.length||!n.properties.every(ts.isPropertyAssignment))return false;
  const negative=p=>[ts.SyntaxKind.FalseKeyword,ts.SyntaxKind.NullKeyword].includes(p.initializer.kind)||(/^(?:error|error_code)$/.test(p.name.getText())&&ts.isStringLiteral(p.initializer)&&p.initializer.text.length>0);
  if(n.properties.every(negative))return true;
  if(!n.properties.some(negative))return false;
  // Partial negative diagnostics (canvas exists but context missing, zero
  // samples) are genuine observations, not fabricated successful game state.
  // Require a branch on a real DOM/context read in this same browser function.
  const scope=fnOf(statement), observations=new Set();
  const decls=nodes(scope||f).filter(x=>ts.isVariableDeclaration(x)&&ts.isIdentifier(x.name)&&fnOf(x)===scope);
  for(let pass=0;pass<2;pass++)for(const d of decls){
    const v=d.initializer;
    if(!v||!ts.isCallExpression(v))continue;
    if(/^document\.(?:getElementById|querySelector|querySelectorAll)$/.test(v.expression.getText(f))||
      (ts.isPropertyAccessExpression(v.expression)&&v.expression.name.text==='getContext'&&observations.has(v.expression.expression.getText(f))))observations.add(d.name.text);
  }
  let guarded=false;
  for(let p=statement.parent;p&&p!==scope;p=p.parent)if(ts.isIfStatement(p)&&statement.pos>=p.thenStatement.pos&&statement.end<=p.thenStatement.end){
    guarded=nodes(p.expression).some(x=>ts.isPrefixUnaryExpression(x)&&x.operator===ts.SyntaxKind.ExclamationToken&&ts.isIdentifier(x.operand)&&observations.has(x.operand.text));
    if(guarded)break;
  }
  return guarded&&n.properties.every(p=>negative(p)||p.initializer.kind===ts.SyntaxKind.TrueKeyword||(ts.isNumericLiteral(p.initializer)&&p.initializer.text==='0'));
}
// Preserve frozen assertion text even if a model needlessly rephrases ONLY
// an optional diagnostic string. Never normalize an expected business string.
let restoredMessages=0;
if(input.normalize_entry){
  const tree=parse(input.original), entry=inertWrapper(ts,tree.statements);
  if(entry)input.script=entry.body.statements.map(s=>s.getText(tree)).join('\n');
}
if(input.restore_messages){
  const a=parse(input.original),b=parse(input.script);
  const aa=nodes(a).filter(n=>isAssert(n,a)),bb=nodes(b).filter(n=>isAssert(n,b));
  const edits=[];
  const messagePosition={ok:1,equal:2,notEqual:2,strictEqual:2,notStrictEqual:2,deepEqual:2,notDeepEqual:2,deepStrictEqual:2,notDeepStrictEqual:2,match:2,doesNotMatch:2};
  if(aa.length===bb.length)for(let i=0;i<aa.length;i++){
    const oldCall=aa[i],newCall=bb[i],method=oldCall.expression.getText(a).replace(/^assert\./,'');
    const pos=messagePosition[method];
    if(pos===undefined||oldCall.arguments.length!==pos+1||newCall.arguments.length!==pos+1||oldCall.expression.getText(a)!==newCall.expression.getText(b))continue;
    if(!ts.isStringLiteral(oldCall.arguments[pos])||!ts.isStringLiteral(newCall.arguments[pos]))continue;
    if(!oldCall.arguments.slice(0,pos).every((arg,j)=>arg.getText(a)===newCall.arguments[j].getText(b)))continue;
    if(oldCall.getText(a)===newCall.getText(b))continue;
    const prefix='async function __qualityScript__(){\n'.length;
    edits.push([newCall.getStart(b)-prefix,newCall.end-prefix,oldCall.getText(a)]);
  }
  for(const [start,end,replacement] of edits.reverse())input.script=input.script.slice(0,start)+replacement+input.script.slice(end);
  restoredMessages=edits.length;
}
const old=parse(input.original), newer=parse(input.script), before=nodes(old), after=nodes(newer);
// A sole uncalled async wrapper is an inert harness-format error, not an
// assertion scope chosen by the product. Permit only its exact whole body to
// become the async script body; every statement/argument/assertion stays intact.
const wrapper=inertWrapper(ts,old.statements);
const liftedWrapper=!!(wrapper&&
  wrapper.body.statements.length===newer.statements.length&&
  wrapper.body.statements.every((s,i)=>s.getText(old)===newer.statements[i].getText(newer)));
// Check proposals against the pinned harness API without executing scripts.
// Historical receipts keep their original assertion-validation contract.
function accessPath(n){
  if(ts.isIdentifier(n))return n.text;
  if(ts.isPropertyAccessExpression(n))return accessPath(n.expression)+'.'+n.name.text;
  if(ts.isElementAccessExpression(n)&&ts.isStringLiteral(n.argumentExpression))return accessPath(n.expression)+'.'+n.argumentExpression.text;
  return '';
}
if(input.clock_methods)for(const n of after){
  if(!ts.isCallExpression(n))continue;
  const match=/^(?:page|context)\.clock\.([\w$]+)$/.exec(accessPath(n.expression));
  if(match&&!input.clock_methods.includes(match[1]))fail('clock_api_invalid');
}
const oa=before.filter(n=>isAssert(n,old)), na=after.filter(n=>isAssert(n,newer));
if((!oa.length&&!input.new_case)||!na.length)fail('assertions_required');
if(input.new_case&&newer.statements.length===1&&ts.isExpressionStatement(newer.statements[0])){
  let e=newer.statements[0].expression;while(ts.isParenthesizedExpression(e))e=e.expression;
  if(ts.isArrowFunction(e)||ts.isFunctionExpression(e))fail('function_not_body');
}
if(input.new_case&&!after.some(n=>ts.isCallExpression(n)&&/^(?:page|context)\./.test(n.expression.getText(newer))))fail('browser_check_required');
const matched=seq(na.map(n=>n.getText(newer)),oa.map(n=>n.getText(old)));
const kept=new Set(matched);
for(let i=0;i<na.length;i++){
  const n=na[i],fn=fnOf(n);
  if(fn&&isBrowser(fn,newer))fail('assert_wrong_context');
  if(!kept.has(i)&&n.arguments.length&&[...n.arguments].every(literal))fail('fake_assertion');
  // Comparing a binding to itself cannot observe application behavior. Restrict
  // this rule to equal/deepEqual and plain identifiers: property getters and
  // separately evaluated expressions may genuinely change between reads.
  if(!kept.has(i)&&/^assert\.(?:equal|strictEqual|deepEqual|deepStrictEqual)$/.test(accessPath(n.expression))&&
     n.arguments.length>=2&&ts.isIdentifier(n.arguments[0])&&ts.isIdentifier(n.arguments[1])&&
     n.arguments[0].text===n.arguments[1].text)fail('self_comparison');
}
for(let i=0;i<oa.length;i++){
  const a=oa[i],b=na[matched[i]],af=frame(a,old),bf=frame(b,newer);
  if(af!==bf && !(af==='browser'&&['root','awaited_iife'].includes(bf)) && !(af==='root'&&bf==='awaited_iife') && !(liftedWrapper&&fnOf(a)===wrapper&&bf==='root'))fail('assertion_hidden');
  if(gates(a,old)!==gates(b,newer))fail('assertion_control_changed');
  // No newly inserted return/break/throw before a retained assertion in its scope.
  for(const exit of after.filter(x=>ts.isReturnStatement(x)||ts.isBreakStatement(x)||ts.isContinueStatement(x)||ts.isThrowStatement(x))){
    if(fnOf(exit)!==fnOf(b)||exit.pos>=b.pos)continue;
    const target=continueTarget(exit);
    if(target&&!(target.pos<=b.pos&&b.end<=target.end))continue;
    if(!before.some(x=>x.kind===exit.kind&&x.pos<a.pos&&fnOf(x)===fnOf(a)&&x.getText(old)===exit.getText(newer)&&gates(x,old)===gates(exit,newer)))fail('assertion_bypassed');
  }
}
const oldText=new Set(before.map(n=>n.getText(old)));
// Moving an existing URL read across realms is a transport correction, not a
// new observation. Permit only the same URL(input).field already in the old
// script, with the identical input passed to a single callback parameter.
function transportedURLRead(call){
  if(call.arguments.length!==2)return false;
  const fn=call.arguments[0], argument=call.arguments[1];
  if(!ts.isArrowFunction(fn)||fn.parameters.length!==1||!ts.isIdentifier(fn.parameters[0].name)||!ts.isIdentifier(argument))return false;
  if(argument.text!=='baseURL'||fn.parameters[0].name.text==='URL'||fn.parameters[0].initializer||fn.parameters[0].dotDotDotToken)return false;
  if(before.some(node=>(ts.isVariableDeclaration(node)||ts.isParameter(node)||ts.isClassDeclaration(node)||ts.isFunctionDeclaration(node))&&node.name&&/\bURL\b/.test(node.name.getText(old))))return false;
  const body=fn.body;
  if(!ts.isPropertyAccessExpression(body)||!['origin','protocol','host','hostname','pathname','search','hash','port','href'].includes(body.name.text))return false;
  const construct=body.expression;
  if(!ts.isNewExpression(construct)||!ts.isIdentifier(construct.expression)||construct.expression.text!=='URL'||construct.arguments?.length!==1)return false;
  if(!ts.isIdentifier(construct.arguments[0])||construct.arguments[0].text!==fn.parameters[0].name.text)return false;
  return before.some(node=>ts.isPropertyAccessExpression(node)&&node.name.text===body.name.text&&
    ts.isNewExpression(node.expression)&&ts.isIdentifier(node.expression.expression)&&node.expression.expression.text==='URL'&&
    node.expression.arguments?.length===1&&ts.isIdentifier(node.expression.arguments[0])&&node.expression.arguments[0].text===argument.text);
}
if(!input.new_case){
  const viewportCalls=(ns,f)=>ns.filter(n=>ts.isCallExpression(n)&&/\.setViewportSize$/.test(n.expression.getText(f))).map(n=>n.getText(f));
  const originalViewports=viewportCalls(before,old), revisedViewports=viewportCalls(after,newer);
  if(JSON.stringify(originalViewports)!==JSON.stringify(revisedViewports))fail('viewport_changed');
}
const observed=new Set(oa.flatMap(n=>nodes(n).filter(ts.isIdentifier).map(n=>n.text)));
const constants=before.filter(n=>ts.isVariableDeclaration(n)&&ts.isIdentifier(n.name)&&observed.has(n.name.text)&&n.initializer&&literal(n.initializer));
for(const c of constants){
  if(!after.some(n=>ts.isVariableDeclaration(n)&&n.name.getText(newer)===c.name.getText(old)&&n.initializer?.getText(newer)===c.initializer.getText(old)))fail('expected_binding_changed');
}
const protectedRoot=/^(?:assert|page|context|unitEvidence|candidateSHA|baseURL|canvasMetrics|runtimeWitness|runtimeCheckpoint|Date|JSON|Object|Reflect|Promise|performance)(?:\.|\[|$)/;
for(const n of after){
  const text=n.getText(newer), existing=oldText.has(text);
  if((ts.isVariableDeclaration(n)||ts.isParameter(n)||ts.isFunctionDeclaration(n)||ts.isClassDeclaration(n))&&n.name&&/\bassert\b/.test(n.name.getText(newer)))fail('assert_rebound');
  if(ts.isBinaryExpression(n)&&n.operatorToken.kind>=ts.SyntaxKind.FirstAssignment&&n.operatorToken.kind<=ts.SyntaxKind.LastAssignment){
    const left=n.left.getText(newer);
    if(protectedRoot.test(left)||(!existing&&/^(?:window|globalThis|Math)\b/.test(left)))fail('protected_mutation');
    if(!existing&&/^[\w$]+(?:\.|\[)/.test(left)&&observed.has(left.match(/^[\w$]+/)[0]))fail('observed_value_forged');
    if(!existing&&ts.isIdentifier(n.left)&&observed.has(n.left.text)&&literal(n.right))fail('observed_value_forged');
  }
  if((ts.isDeleteExpression(n)||ts.isPrefixUnaryExpression(n)||ts.isPostfixUnaryExpression(n))&&protectedRoot.test((n.operand||n.expression)?.getText(newer)||''))fail('protected_mutation');
  if(!existing){
    if(ts.isTryStatement(n)||ts.isCatchClause(n))fail('exception_swallowed');
    if(ts.isImportDeclaration(n)||ts.isExportDeclaration(n)||ts.isWithStatement(n))fail('dynamic_execution');
    if(ts.isCallExpression(n)&&(/^(?:require|eval|Function|assert\.skip)$/.test(n.expression.getText(newer))||n.expression.kind===ts.SyntaxKind.ImportKeyword))fail('dynamic_execution');
    if(ts.isNewExpression(n)&&['Function','Proxy'].includes(n.expression.getText(newer)))fail('dynamic_execution');
    if(ts.isCallExpression(n)&&/^(?:Object\.(?:assign|defineProperty|defineProperties)|Reflect\.)/.test(n.expression.getText(newer))&&n.arguments.some(a=>protectedRoot.test(a.getText(newer))||(ts.isIdentifier(a)&&observed.has(a.text))))fail('protected_mutation');
    if(ts.isVariableDeclaration(n)&&n.initializer&&/^(?:assert|page|context)(?:\.|\[|$)/.test(n.initializer.getText(newer))&&!ts.isCallExpression(n.initializer))fail('protected_alias');
    // Tests may operate the real UI, but may not manufacture its layout/content.
    // This also covers createElement('style') + appendChild, not just Playwright helpers.
    if(ts.isCallExpression(n)&&/\.(?:route|addScriptTag|addStyleTag|setContent|createElement|createElementNS|appendChild|insertBefore|replaceChild|insertAdjacentHTML|append|prepend|replaceWith)$/.test(n.expression.getText(newer)))fail('production_replaced');
    if(ts.isBinaryExpression(n)&&n.operatorToken.kind>=ts.SyntaxKind.FirstAssignment&&n.operatorToken.kind<=ts.SyntaxKind.LastAssignment&&/(?:\.style(?:\.|\[)|\.(?:innerHTML|outerHTML|textContent|cssText)\b)/.test(n.left.getText(newer)))fail('production_replaced');
    if(ts.isCallExpression(n)&&/\.(?:setAttribute|setProperty|insertRule|replaceSync)$/.test(n.expression.getText(newer)))fail('production_replaced');
    if(ts.isReturnStatement(n)&&n.expression&&ts.isObjectLiteralExpression(n.expression)&&literal(n.expression)&&!failureObject(n.expression,n,newer))fail('observed_value_forged');
    if(ts.isCallExpression(n)&&n.expression.getText(newer)==='page.evaluate'&&n.arguments[0]&&ts.isFunctionLike(n.arguments[0])&&!frameBarrier(n.arguments[0])&&!transportedURLRead(n)&&!nodes(n.arguments[0].body).some(x=>ts.isIdentifier(x)&&['window','document','localStorage','sessionStorage','navigator','performance','location'].includes(x.text)))fail('browser_result_forged');
  }
}
if(input.setup_only){
  let pos=0;
  for(const statement of old.statements){
    while(pos<newer.statements.length&&newer.statements[pos].getText(newer)!==statement.getText(old))pos++;
    if(pos===newer.statements.length)fail('setup_changed_existing_steps');pos++;
  }
}
if(input.realtime_required && after.some(n=>(ts.isPropertyAccessExpression(n)&&n.name.text==='clock'&&n.expression.getText(newer)==='page')||(ts.isElementAccessExpression(n)&&ts.isStringLiteral(n.argumentExpression)&&n.argumentExpression.text==='clock'&&n.expression.getText(newer)==='page')))fail('realtime_required');
process.stdout.write(JSON.stringify({assertions_unchanged:true,original_assertions:oa.length,total_assertions:na.length,contract:'test-procedure-v1',...(liftedWrapper?{inert_wrapper_lifted:true}:{}),...(restoredMessages||input.normalize_entry?{normalized_script:input.script,restored_messages:restoredMessages}:{} )}));

