// Compile only: never invoke model-produced code during the syntax preflight.
const fs = require('node:fs');
const tests = JSON.parse(fs.readFileSync('/tmp/qa-preflight.json', 'utf8'));
const errors = [];
for (let i = 0; i < tests.length; i++) {
  try {
    if(/unitEvidence\s*\.\s*commit_sha\s*,\s*['"][a-f0-9]{40}['"]/i.test(tests[i].script))throw new Error('candidate_version_must_use_candidateSHA: use assert.equal(unitEvidence.commit_sha,candidateSHA), never a source-version literal');
    new Function('page', 'context', 'assert', 'baseURL', 'unitEvidence','candidateSHA', `return (async()=>{${tests[i].script}\n})()`);
  }
  catch (e) { errors.push({ index: i, type: 'SyntaxError', message: String(e.message).slice(0, 200) }); }
}
console.log(JSON.stringify({syntax_valid: errors.length === 0, errors}));
