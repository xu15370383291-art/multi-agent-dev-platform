// Compile and inspect only. Never execute a submitted script in preflight.
const fs=require('node:fs');
const ts=require('/opt/toolchain/node_modules/typescript/lib/typescript.js');
const tests=JSON.parse(fs.readFileSync('/tmp/qa-preflight.json','utf8'));
const {inertWrapper}=require('./script_entry.cjs');
const {checkCapabilities}=require('./capability_check.cjs');
const errors=[];
for(let i=0;i<tests.length;i++){
 try{
  const source=tests[i].script;
  if(/unitEvidence\s*\.\s*commit_sha\s*,\s*['"][a-f0-9]{40}['"]/i.test(source))throw Error('candidate_version_must_use_candidateSHA');
  new Function('page','context','assert','baseURL','unitEvidence','candidateSHA',`return (async()=>{${source}\n})()`);
  const tree=ts.createSourceFile('case.js',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.JS);
  if(inertWrapper(ts,tree.statements))throw Error('qa_inert_entry_requires_body');
  const scan=ts.createScanner(ts.ScriptTarget.Latest,false,ts.LanguageVariant.Standard,source);
  for(let t=scan.scan();t!==ts.SyntaxKind.EndOfFileToken;t=scan.scan()){
   if([ts.SyntaxKind.SingleLineCommentTrivia,ts.SyntaxKind.MultiLineCommentTrivia].includes(t)&&/[@#]\s*source(?:Mapping)?URL\s*=/.test(scan.getTokenText()))throw Error('qa_trace_source_directive_forbidden');
  }
  function walk(n){
   const text=n.getText(tree);
   if((ts.isVariableDeclaration(n)||ts.isParameter(n)||ts.isFunctionDeclaration(n))&&n.name&&/\b(?:assert|page|context|unitEvidence|candidateSHA|baseURL|canvasMetrics|runtimeWitness|runtimeCheckpoint)\b/.test(n.name.getText(tree)))throw Error('qa_assert_binding_forbidden');
   if(ts.isCallExpression(n)&&(/^(?:eval|Function|require)$/.test(n.expression.getText(tree))||n.expression.kind===ts.SyntaxKind.ImportKeyword))throw Error('qa_dynamic_execution_forbidden');
   if(ts.isNewExpression(n)&&['Function','Proxy'].includes(n.expression.getText(tree)))throw Error('qa_dynamic_execution_forbidden');
   if(ts.isBinaryExpression(n)&&n.operatorToken.kind>=ts.SyntaxKind.FirstAssignment&&n.operatorToken.kind<=ts.SyntaxKind.LastAssignment&&/^(?:assert|Error|runtimeWitness|runtimeCheckpoint)(?:\.|\[|$)/.test(n.left.getText(tree)))throw Error('qa_evidence_mutation_forbidden');
   if(ts.isCallExpression(n)&&/^Object\.(?:defineProperty|defineProperties|assign)$/.test(n.expression.getText(tree))&&n.arguments[0]&&/^(?:assert|Error|runtimeWitness|runtimeCheckpoint)$/.test(n.arguments[0].getText(tree)))throw Error('qa_evidence_mutation_forbidden');
   if(ts.isCallExpression(n)&&/^assert(?:\.|$)/.test(n.expression.getText(tree))&&n.expression.getText(tree)!=='assert.fail'&&n.arguments.length&&n.arguments.every(a=>ts.isStringLiteral(a)||ts.isNumericLiteral(a)||[ts.SyntaxKind.TrueKeyword,ts.SyntaxKind.FalseKeyword,ts.SyntaxKind.NullKeyword].includes(a.kind)))throw Error('qa_literal_assertion_not_evidence');
   if(ts.isCallExpression(n)&&/^assert\.(?:equal|strictEqual|deepEqual|deepStrictEqual)$/.test(n.expression.getText(tree))&&n.arguments.length>=2&&ts.isIdentifier(n.arguments[0])&&ts.isIdentifier(n.arguments[1])&&n.arguments[0].text===n.arguments[1].text)throw Error('qa_self_comparison_not_evidence');
   ts.forEachChild(n,walk);
  }
  walk(tree);
  checkCapabilities(ts,source);
 }catch(e){errors.push({index:i,type:e.qaDiagnostic?'CapabilityError':'SyntaxError',message:String(e.message).slice(0,200),...(e.qaDiagnostic||{})});}
}
console.log(JSON.stringify({syntax_valid:errors.length===0,errors}));


