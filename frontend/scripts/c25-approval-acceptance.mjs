// Real HTTP UI approval using explicitly seeded, non-business fixture documents.
import {chromium} from 'playwright'
import assert from 'node:assert/strict'
import {readFile,writeFile} from 'node:fs/promises'
const rows=JSON.parse(await readFile('../.local/c25-acceptance/approval-fixtures.json','utf8'))
const browser=await chromium.launch({channel:'msedge',headless:true});const results=[]
try{
 for(const row of rows){
  const context=await browser.newContext({viewport:{width:row.width,height:1000}});const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message))
  await page.goto('http://127.0.0.1:5173/');await page.getByRole('button',{name:new RegExp(row.name)}).click()
  const change=page.getByRole('button',{name:'要求修改',exact:true});await change.waitFor();assert.equal(await change.isDisabled(),true)
  if(row.decision==='request_changes')await page.getByLabel('修改意见',{exact:true}).fill('C5实际HTTP审批测试：保留范围，补充说明。')
  const responsePromise=page.waitForResponse(r=>r.url().endsWith(`/approvals/${row.approval_id}/decision`) && r.request().method()==='POST')
  await page.getByRole('button',{name:row.decision==='approve'?'批准此版本':'要求修改',exact:true}).click()
  const response=await responsePromise;assert.equal(response.status(),202)
  const payload=response.request().postDataJSON();assert.equal(payload.document_version,1);assert.equal(payload.decision,row.decision)
  const body=await response.json();assert.equal(body.status,row.decision==='approve'?'approved':'changes_requested')
  await page.getByText('审批决定已保存，等待工作流继续。',{exact:true}).waitFor();assert.deepEqual(errors,[])
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))
  await page.screenshot({path:`../.local/c25-acceptance/approval-${row.kind}-${row.width}.png`,fullPage:true})
  results.push({...row,status:'passed',request_payload:payload,response:body,page_errors:errors.length})
  await context.close()
 }
 await writeFile('../.local/c25-acceptance/approval-browser.json',JSON.stringify({status:'passed',scope:'real UI/API with explicit non-model fixture documents; not business completion',results},null,2))
 console.log(JSON.stringify({status:'passed',contexts:results.length},null,2))
}finally{
 for(const row of rows){const r=await fetch(`http://127.0.0.1:8000/api/v1/runs/${row.run_id}/cancel`,{method:'POST'});assert.equal(r.status,202)}
 await browser.close()
}
