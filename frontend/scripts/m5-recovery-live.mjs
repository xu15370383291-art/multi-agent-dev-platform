import {chromium,expect} from '@playwright/test'
import fs from 'node:fs/promises'
const command=process.argv[2]
if(!['check','read','resume'].includes(command))throw Error('check/read/resume required')
const root=new URL('../../.local/m5-acceptance/',import.meta.url),origin='http://127.0.0.1:5175'
const state=JSON.parse(await fs.readFile(new URL('real-ui.json',root),'utf8')),entry=state.cases.game
const response=await fetch(`${origin}/api/v1/runs/${entry.run_id}/diagnostics`)
if(!response.ok)throw Error(`Diagnostics HTTP ${response.status}`)
const diagnostic=await response.json()
if(command==='read'){console.log(JSON.stringify(diagnostic));process.exit()}
if(diagnostic.status!=='paused'||diagnostic.usage.model_calls>=diagnostic.usage.max_model_calls)throw Error('A paused run with original remaining budget required')
if(command==='check'){
 if(diagnostic.latest_check&&!(['failed','succeeded','blocked'].includes(diagnostic.latest_check.status)&&(diagnostic.latest_check.expected_failure_id!==diagnostic.historical_failure_id||(process.argv.includes('--after-binding-fix')&&diagnostic.reason==='source_regression_suite_limit_requires_human'))))throw Error('Existing check retained; only a new, separately inspected failure or this explicit post-fix inspection may receive a new check')
 if(!diagnostic.actions.inspect.enabled)throw Error('Inspection not allowed')
}else if(!diagnostic.actions.resume.enabled||diagnostic.latest_check.status!=='ready')throw Error('Verified ready checkpoint required')
const browser=await chromium.launch({channel:'msedge',headless:true})
try{
 const page=await browser.newPage({viewport:{width:1440,height:1050}})
 await page.goto(origin)
 await page.getByRole('combobox',{name:'当前项目'}).selectOption(entry.project_id)
 await page.getByRole('button',{name:'执行记录',exact:true}).click()
 const target=command==='check'?'recovery-checks':'resume'
 const submitted=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/'+target))
 const button=page.getByRole('button',{name:command==='check'?'核对执行结果 · 不调用模型':'继续执行 · 可能产生模型费用',exact:true})
 await expect(button).toBeEnabled()
 await button.click()
 const result=await submitted
 if(!result.ok())throw Error(`Action HTTP ${result.status()}: ${await result.text()}`)
 const body=await result.json()
 const label=body.id||body.work_item_id
 await fs.writeFile(new URL(`game-recovery-${command}-${label}.json`,root),JSON.stringify({diagnostic_before:diagnostic,result:body},null,2))
 await page.screenshot({path:new URL(`game-recovery-${command}-${label}.png`,root).pathname.replace(/^\/([A-Za-z]:)/,'$1')})
 console.log(JSON.stringify(body))
}finally{await browser.close()}
