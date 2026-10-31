// Controller-owned complete results. The console carries only a small descriptor.
const fs=require('node:fs');
const crypto=require('node:crypto');
function publishResult(result){
  const limit=JSON.parse(fs.readFileSync('/tmp/qa-result-limit.json','utf8'));
  const raw=Buffer.from(JSON.stringify(result));
  if(!Number.isSafeInteger(limit)||limit<=0||raw.length>limit)throw new Error('quality_result_file_limit');
  const path='/workspace/quality-artifacts/browser-result.json';
  const temporary=path+'.'+crypto.randomBytes(16).toString('hex')+'.tmp';
  const fd=fs.openSync(temporary,'wx',0o600);
  try{fs.writeFileSync(fd,raw);fs.fsyncSync(fd)}finally{fs.closeSync(fd)}
  try{fs.linkSync(temporary,path)}finally{fs.unlinkSync(temporary)}
  console.log(JSON.stringify({contract:'browser-result-file-v1',path:'quality-artifacts/browser-result.json',bytes:raw.length,sha256:crypto.createHash('sha256').update(raw).digest('hex')}));
}
module.exports={publishResult};
