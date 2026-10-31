// Compile only: never invoke model-produced code during the syntax preflight.
const fs = require('node:fs');
const tests = JSON.parse(fs.readFileSync('/tmp/qa-preflight.json', 'utf8'));
const errors = [];
for (let i = 0; i < tests.length; i++) {
  try { new Function('page', 'context', 'assert', 'baseURL', `return (async()=>{${tests[i].script}\n})()`); }
  catch (e) { errors.push({ index: i, type: 'SyntaxError', message: String(e.message).slice(0, 200) }); }
}
console.log(JSON.stringify({syntax_valid: errors.length === 0, errors}));
