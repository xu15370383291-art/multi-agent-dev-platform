// Compile-time diagnostics only. Never evaluate submitted code.
const capabilities=require('./capabilities.json');
const remoteMethods=new Set(['evaluate','evaluateHandle','$eval','$$eval','waitForFunction','addInitScript']);
const browserGlobals=new Set(['URL','URLSearchParams','window','document','location','navigator','localStorage','DOMException','fetch']);

function checkCapabilities(ts,source){
 const name='/case.js';
 const tree=ts.createSourceFile(name,source,ts.ScriptTarget.Latest,true,ts.ScriptKind.JS);
 const host={getSourceFile:file=>file===name?tree:undefined,getDefaultLibFileName:()=>'',writeFile(){},getCurrentDirectory:()=> '/',getDirectories:()=>[],fileExists:file=>file===name,readFile:file=>file===name?source:undefined,getCanonicalFileName:file=>file,useCaseSensitiveFileNames:()=>true,getNewLine:()=> '\n'};
 const program=ts.createProgram([name],{allowJs:true,noLib:true,noResolve:true},host);
 const checker=program.getTypeChecker(), remote=new Set();
 const isFunction=n=>ts.isArrowFunction(n)||ts.isFunctionExpression(n)||ts.isFunctionDeclaration(n);
 function mark(n){
  if(ts.isParenthesizedExpression(n))return mark(n.expression);
  if(isFunction(n)){remote.add(n);return;}
  if(ts.isIdentifier(n)){
   const symbol=checker.getSymbolAtLocation(n);
   for(const d of symbol?.declarations||[]){
    if(isFunction(d))remote.add(d);
    else if(ts.isVariableDeclaration(d)&&d.initializer&&isFunction(d.initializer))remote.add(d.initializer);
   }
  }
 }
 function find(n){
  const method=ts.isCallExpression(n)&&(
   ts.isPropertyAccessExpression(n.expression)?n.expression.name.text:
   ts.isElementAccessExpression(n.expression)&&ts.isStringLiteral(n.expression.argumentExpression)?n.expression.argumentExpression.text:null);
  if(method&&remoteMethods.has(method)){
   // Context.addInitScript supports {content}; strings are not executed here.
   for(const a of n.arguments)mark(a);
  }
  ts.forEachChild(n,find);
 }
 find(tree);
 function reference(n){
  const p=n.parent;
  if(ts.isPropertyAccessExpression(p)&&p.name===n)return false;
  if((ts.isPropertyAssignment(p)||ts.isMethodDeclaration(p)||ts.isPropertyDeclaration(p))&&p.name===n)return false;
  if((ts.isVariableDeclaration(p)||ts.isParameter(p)||ts.isFunctionDeclaration(p)||ts.isClassDeclaration(p)||ts.isBindingElement(p))&&p.name===n)return false;
  if(ts.isTypeOfExpression(p))return false; // Capability detection is legal.
  return true;
 }
 function visit(n,inBrowser=false){
  inBrowser=inBrowser||remote.has(n);
  const member=ts.isPropertyAccessExpression(n)?n.name.text:
   ts.isElementAccessExpression(n)&&ts.isStringLiteral(n.argumentExpression)?n.argumentExpression.text:null;
  const used=n.parent&&(ts.isCallExpression(n.parent)||ts.isNewExpression(n.parent)||
   ts.isPropertyAccessExpression(n.parent)&&n.parent.expression===n);
  if(member&&used&&ts.isIdentifier(n.expression)&&n.expression.text==='globalThis'&&
     !checker.getSymbolAtLocation(n.expression)?.declarations?.length&&
     Object.hasOwn(capabilities.unavailable_host_globals,member)&&(!inBrowser||!browserGlobals.has(member))){
   const position=tree.getLineAndCharacterOfPosition(n.getStart(tree));
   const error=new Error((inBrowser?'qa_browser_global_unavailable:':'qa_host_global_unavailable:')+member);
   error.qaDiagnostic={capability:member,line:position.line+1,column:position.character+1,
    alternative:capabilities.unavailable_host_globals[member],scope:inBrowser?'browser_callback':'qa_host'};
   throw error;
  }
  const symbol=ts.isIdentifier(n)?(ts.isShorthandPropertyAssignment(n.parent)?checker.getShorthandAssignmentValueSymbol(n.parent):checker.getSymbolAtLocation(n)):null;
  if(ts.isIdentifier(n)&&Object.hasOwn(capabilities.unavailable_host_globals,n.text)&&reference(n)&&!symbol){
   if(!inBrowser||!browserGlobals.has(n.text)){
    const position=tree.getLineAndCharacterOfPosition(n.getStart(tree));
    const error=new Error((inBrowser?'qa_browser_global_unavailable:':'qa_host_global_unavailable:')+n.text);
    error.qaDiagnostic={capability:n.text,line:position.line+1,column:position.character+1,
     alternative:capabilities.unavailable_host_globals[n.text],scope:inBrowser?'browser_callback':'qa_host'};
    throw error;
   }
  }
  ts.forEachChild(n,child=>visit(child,inBrowser));
 }
 visit(tree);
}
module.exports={checkCapabilities};
