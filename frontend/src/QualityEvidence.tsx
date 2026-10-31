import type { QualityReport } from './api'

type Case={criterion_id:string;name?:string;status:string;assertions?:number;error?:string;screenshot?:string}
type Issue={severity:string;path:string;line:number;description:string;evidence:string}
const severity:Record<string,string>={critical:'严重',major:'主要',minor:'次要',suggestion:'建议'}
type Coverage={criterion_id:string;approved_quote:string;branches:{kind:string;scenario:string;status:string;reason:string;evidence?:{case_index:number;line:number|null;column?:number|null}[]}[]}
const branchNames:Record<string,string>={normal:'正常场景',exception:'异常场景',boundary:'边界场景'}
const branchStatuses:Record<string,string>={verified:'已验证',failed:'执行失败',not_verified:'尚未验证',not_applicable:'不适用（审查结论）'}

export function QualityEvidence({projectId,report}:{projectId:string;report:QualityReport}){
  const execution=report.payload.execution as {results?:Case[];stdout?:string;stderr?:string;timed_out?:boolean;truncated?:boolean;unit_tests?:{status:string;tests:number|null;commit_sha:string;files?:string[];execution?:{stdout?:string;stderr?:string;timed_out?:boolean;truncated?:boolean}}}|undefined
  const units=execution?.unit_tests
  const unitsPassed=units?.status==='passed'&&typeof units.tests==='number'&&units.tests>0&&units.commit_sha===report.commit_sha&&!units.execution?.timed_out&&!units.execution?.truncated
  const results=Array.isArray(execution?.results)?execution.results:[]
  const issues=Array.isArray(report.payload.issues)?report.payload.issues as Issue[]:[]
  const coverage=Array.isArray(report.payload.coverage)?report.payload.coverage as Coverage[]:[]
  return <div className="quality-evidence">
    {typeof report.payload.summary==='string'&&<p>{report.payload.summary}</p>}
    {coverage.length>0&&<section aria-label="验收分支覆盖"><h3>验收分支覆盖</h3><p className="note">逐项核对批准要求和实际执行证据；用例数量不代表分支全部验证。</p>{coverage.map((item,i)=><details key={`${item.criterion_id}-${i}`}><summary>{item.criterion_id} · {Array.isArray(item.branches)?item.branches.filter(b=>b.status==='not_verified').length:0} 项尚未验证</summary><p>{item.approved_quote}</p>{Array.isArray(item.branches)&&item.branches.map((branch,j)=><div key={j}><strong>{branchNames[branch.kind]||branch.kind} · {branchStatuses[branch.status]||'未确认'}</strong><p>{branch.scenario}</p><p>{branch.reason}</p>{Array.isArray(branch.evidence)&&branch.evidence.length>0&&<p className="note">执行证据：{branch.evidence.map(ref=>`第 ${ref.case_index+1} 项用例${ref.line===null?'（断言前执行失败）':`第 ${ref.line} 行${ref.column?`第 ${ref.column} 列`:""}`}`).join('；')}</p>}</div>)}</details>)}</section>}
    {units&&<section><h3>固定候选的 Node 单元测试</h3><p>{unitsPassed?`通过 · ${units.tests} 项`:units.status==='not_applicable'&&units.commit_sha===report.commit_sha?'未发现约定命名的 Node 测试文件':'未通过或未验证，不能交付'}</p><p className="note">适用版本 <code>{units.commit_sha}</code> · 与浏览器验收分别记录</p>{units.execution&&<details><summary>查看单元测试日志</summary>{units.execution.timed_out&&<p>执行超时</p>}{units.execution.truncated&&<p>日志已达到上限</p>}<pre>{units.execution.stdout||'无标准输出'}</pre>{units.execution.stderr&&<pre>{units.execution.stderr}</pre>}</details>}</section>}
    {results.length>0&&<><p className="acceptance-summary">验收用例 {results.length} 项 · 通过 {results.filter(row=>row.status==='passed'&&(row.assertions||0)>0).length} 项</p><details><summary>查看逐项验收与截图</summary><ol className="quality-cases">{results.map((row,index)=><li key={`${row.criterion_id}-${index}`}><strong>{row.criterion_id} · {row.name||`用例 ${index+1}`}</strong><p>{row.status==='passed'&&(row.assertions||0)>0?'通过':'未通过'} · 实际断言 {row.assertions||0} 次</p>{row.error&&<p className="failure">{row.error}</p>}{row.screenshot===`case-${index}.png`&&<details><summary>查看此用例截图</summary><img loading="lazy" alt={`${row.criterion_id} 第 ${index+1} 个用例的实际运行截图`} src={`/api/v1/projects/${projectId}/quality-reports/${report.id}/screenshots/${index}`} onError={event=>{event.currentTarget.hidden=true;event.currentTarget.parentElement?.querySelector('[role="status"]')?.removeAttribute('hidden')}}/><p role="status" hidden>截图暂时不可用，请核对报告证据或下载交付包查看。</p></details>}</li>)}</ol></details></>}
    {issues.length>0&&<section><h3>审查发现 · {issues.length} 项</h3>{issues.map((issue,index)=><article className="review-finding" key={index}><p><span className="badge">{severity[issue.severity]||issue.severity}</span> <code>{issue.path}:{issue.line}</code></p><p>{issue.description}</p><details><summary>查看具体证据</summary><p>{issue.evidence}</p></details></article>)}</section>}
    {execution&&(execution.stdout||execution.stderr)&&<details><summary>查看测试执行日志</summary>{execution.truncated&&<p>日志达到上限，已截断。</p>}{execution.timed_out&&<p className="failure">测试执行超时。</p>}<pre>{execution.stdout||'无标准输出'}</pre>{execution.stderr&&<pre>{execution.stderr}</pre>}</details>}
  </div>
}
