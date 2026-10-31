// Static entry contract shared by the controller and the isolated harness.
function inertWrapper(ts, statements) {
  if (statements.length !== 1) return null;
  let fn = statements[0];
  if (ts.isExpressionStatement(fn)) {
    fn = fn.expression;
    while (ts.isParenthesizedExpression(fn)) fn = fn.expression;
    if (!ts.isArrowFunction(fn) && !ts.isFunctionExpression(fn)) return null;
  } else if (!ts.isFunctionDeclaration(fn)) return null;
  const supplied = new Set(['page','context','assert','baseURL','unitEvidence','candidateSHA','canvasMetrics']);
  if (!fn.body || !ts.isBlock(fn.body) || fn.asteriskToken ||
      !fn.modifiers?.some(m => m.kind === ts.SyntaxKind.AsyncKeyword) ||
      !fn.parameters.every(p => ts.isIdentifier(p.name) && supplied.has(p.name.text) && !p.initializer && !p.dotDotDotToken)) return null;
  // A named recursive wrapper cannot be lifted without changing its binding.
  let recursive = false;
  function visit(n) {
    if (fn.name && ts.isIdentifier(n) && n.text === fn.name.text) recursive = true;
    ts.forEachChild(n, visit);
  }
  visit(fn.body);
  return recursive ? null : fn;
}
module.exports = {inertWrapper};
