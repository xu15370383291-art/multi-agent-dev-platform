import {useState} from 'react'
import {api,type QualityReport} from './api'

type Draft={candidate_id:string;commit_sha:string;qa_report_id:string;reviewer_report_id:string;
 patches:{index:number;criterion_id:string;reason:string;script:string}[];issues:{index:number;rationale:string}[];
 confirmed_against_approved_requirements:boolean;confirmation?:string}
type Target={enabled:boolean;candidate_id?:string;qa_report_id?:string;reviewer_report_id?:string}
type Preview={confirmation:string;effect:string}
export function ReviewedTestCorrection({target,reports,onRun}:{target:Target;reports:QualityReport[];onRun:(fn:()=>Promise<void>)=>Promise<void>}){
 const [text,setText]=useState(''),[draft,setDraft]=useState<Draft|null>(null),[preview,setPreview]=useState<Preview|null>(null)
 const [confirmed,setConfirmed]=useState(false),[pending,setPending]=useState(false),[error,setError]=useState('')
 const qa=reports.find(r=>r.id===target.qa_report_id)
 const oldTests=(qa?.payload.tests||[]) as {script:string;name:string;criterion_id:string}[]
 const body=(plan:Draft)=>({qa_report_id:target.qa_report_id,reviewer_report_id:target.reviewer_report_id,
   feedback:'逐项核对批准需求后的显式测试修订；保留全部原AC和失败记录，原阻断项必须由新一轮独立Reviewer复核。',reviewed_plan:plan})
 async function inspect(){
  setError('');setPreview(null);setDraft(null);setConfirmed(false);setPending(true)
  try{
   let plan:Draft
   try{plan=JSON.parse(text)}catch{throw Error('清单不是有效的JSON，请检查后重新粘贴。')}
   if(!plan||!Array.isArray(plan.patches)||!Array.isArray(plan.issues)||plan.candidate_id!==target.candidate_id||plan.qa_report_id!==target.qa_report_id||plan.reviewer_report_id!==target.reviewer_report_id)throw Error('清单与当前候选或报告不一致，请重新核对版本。')
   const result=await api<Preview>(`/candidates/${target.candidate_id}/test-correction-preview`,{method:'POST',body:JSON.stringify(body(plan))})
   setDraft(plan);setPreview(result)
  }catch(e){setError(e instanceof Error?e.message:'清单核对失败')}
  finally{setPending(false)}
 }
 async function submit(){
  if(!preview||!draft||!confirmed||pending)return
  setPending(true);setError('')
  try{await onRun(async()=>{await api(`/candidates/${target.candidate_id}/quality-revisions`,{method:'POST',body:JSON.stringify(body({...draft,confirmation:preview.confirmation}))})})}
  finally{setPending(false)}
 }
 return <details className="panel reviewed-test-correction"><summary>核对测试脚本（高级）</summary>
  <p>仅在测试本身与批准需求冲突时使用。业务功能的缺陷仍需源码修复；此操作不会把失败改成通过。</p>
  <label>逐项修订清单<textarea aria-label="测试修订清单JSON" rows={5} maxLength={700000} value={text} onChange={e=>{setText(e.target.value);setPreview(null);setDraft(null);setConfirmed(false);setError('')}} placeholder="粘贴含原版本摘要、逐项理由和新脚本的审阅清单"/></label>
  <button disabled={pending||!text.trim()||!target.enabled} onClick={()=>void inspect()}>{pending?'正在核对…':'预览修订与版本'}</button>
  {error&&<p role="alert" className="message error">{error}</p>}
  {preview&&draft&&<div className="reviewed-preview">
   <p>适用候选 <code>{draft.commit_sha}</code> · 修订 {draft.patches.length} 个用例，其他用例逐字保留。</p>
   {draft.patches.map(p=><details key={p.index}><summary>{p.criterion_id} · {oldTests[p.index]?.name||`用例 ${p.index+1}`}</summary><p>{p.reason}</p><h3>原脚本（保留历史）</h3><pre>{oldTests[p.index]?.script}</pre><h3>修订脚本（需要重新执行）</h3><pre>{p.script}</pre></details>)}
   {draft.issues.map(i=><p key={i.index}>原阻断项 {i.index+1}：{i.rationale}</p>)}
   <p className="note">清单摘要 <code>{preview.confirmation}</code></p>
   <p>{preview.effect}。会产生独立审查模型费用，沿用原运行预算。</p>
   <label className="reviewed-confirm"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>已逐项对照批准需求核对，保留全部验收要求和旧失败证据</label>
   <button className="primary" disabled={!confirmed||pending||!target.enabled} onClick={()=>void submit()}>按此清单重新验收</button>
  </div>}
 </details>
}
