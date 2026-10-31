import { useEffect, useRef, useState } from 'react'
import { api, ApiError, type Agent, type Profile, type Run } from './api'
import { PauseNotice, type UserNotice } from './PauseNotice'
import { ProgressStrategyForm } from './ProgressStrategyForm'
import { PolicyMigrationForm } from './PolicyMigrationForm'
import './reliability.css'

type Check = { id: string; status: string; expected_epoch: number; checkpoint_id: string | null;
  result: { blockers?: string[]; reconciled_tool_ids?: string[]; reconciled_domains?: {kind:string;id:string}[] } }
type Diagnostics = { run_id: string; phase: string; status: string; epoch?:number;
  user_notice?:UserNotice|null;
  auto_correction?:{status:string;message:string;cause?:string}|null;
  failure: { id: string; message: string; next_step: string; code: string } | null;
  usage: { model_calls: number; max_model_calls: number|null; active_seconds: number; max_seconds: number|null; budget_exhausted: boolean; default_task_tool_limit?:number|null };
  progress?: {mode:string;scopes:{scope:string;state_hash:string;execution_id:string|null;role:string;sequence:number;turns_without_progress:number;blocked_reason:string|null;last_evidence:{kind:string;id:string;status:string}|null;cycle:boolean}[]};
  pause_requested: boolean; cancel_requested: boolean; latest_check: Check | null;
  unsettled_operations: {kind:string;id:string;status:string}[];
  unknown_model_requests?: {id:string;role:string;status:string;profile_revision_id:string;usage_known:boolean;calls_model:false}[];
  actions: Record<'inspect'|'resume', { enabled: boolean; calls_model: boolean; blocked_reason: string | null }> }
type Switch = { id:string;role:string;status:string;target_profile_revision_id:string;new_execution_id:string|null }
type Task = {id:string;role:string;status:string;attempt:number;task_id:string;parent_execution_id?:string|null}
const reasons: Record<string,string> = {
  review_diagnosis_no_progress:'独立诊断仍返回相同无效证据，已停止重复调用；请先核对具体缺口和有效新依据，旧草稿与未知用量保留。',
  review_diagnosis_required:'无效审查回复已形成独立诊断材料，当前不能继续原审查。需独立归因后，再按证据修订或补测；原业务结论仍未完成。',
  review_diagnosis_evidence_invalid:'审查诊断与当前版本或原始回执不匹配，已停止采用旧诊断；请先核对证据，系统没有重试模型。',
  round_recovery_requires_idle:'这一轮仍在执行或停止中，请等当前操作结束后再打开恢复。',
  checkpoint_missing_or_foreign:'未找到这一轮可用的进度记录；原文件仍保留，需要先核对存储，不能从头假装恢复。',
  quality_operation_receipt_required:'中断的验收步骤缺少可核对的原执行回执，系统没有重跑或假定成功；原记录保留。',
  quality_report_version_mismatch:'验收回执与当前源码、测试环境或已保存结果不一致，系统已阻止错误续接。',
  quality_model_result_unknown:'上次质量分析的模型请求未取得完整结果。先免费核对；仍无法确认时，需要明确选择原模型重试或切换模型，再核对并继续。新请求可能产生费用，旧未知记录保留。',
  recovery_storage_unavailable:'读取或保存恢复回执时发生存储错误；已有结果保留，请检查存储可用性后重新核对，不会重跑业务操作。',
  integration_failure_evidence_required:'合并检查曾经失败，但目前无法核对完整结果。系统不会重复合并，需先检查对应记录。',
  progress_repeated_request:'相同状态下重复请求模型，需要核对已有证据和下一步策略。',
  progress_repeated_action:'同一输入下多次执行了相同无效操作，已停止重复执行。',
  progress_state_cycle:'任务在相同状态之间往返，未形成有效进展。',
  progress_stalled:'最近的尝试没有推动任务完成，需要先确定新的处理办法。',
  progress_branch_blocked:'可疑分支已隔离，当前没有其他可继续的独立任务。',
  original_run_budget_exhausted:'原运行预算已耗尽。可以核对已有结果，继续执行需要先处理预算问题。',
  original_task_budget_exhausted:'原任务的工具或循环步数已耗尽。',
  trusted_operation_reconciliation_incomplete:'部分操作缺少完整可信回执，仍需核实。',
  unsettled_operations_require_receipts:'仍有结果不明的操作，不能重复执行。',
  checkpoint_missing:'未找到持久检查点，暂时无法续接。', checkpoint_unavailable:'检查点存储暂时不可用。',
  recovery_adapters_required:'系统暂时无法自动继续这个步骤，需要先查明上方问题并确定处理办法。',
  legacy_developer_session_missing:'旧运行缺少开发会话记录，不能推测进度后续接。',
  recovery_epoch_changed:'运行已变化，请重新核对。', recovery_failure_changed:'失败记录已变化，请重新核对。',
  idle_pause_required:'尚未确认安全暂停，请等待当前操作停止。', coordinator_still_owned:'后台仍在释放执行资源。',
  operation_process_not_quiescent:'执行容器尚未确认停止。', quality_reconciliation_required:'质量结果尚未完成对账。',
  operation_reconciliation_required:'代码合并或生成应用的结果还需检查。', verified_recovery_action_required:'先点击“检查能否继续”，检查通过后再继续。',
  terminal_run:'此运行已经结束。', product_failure_requires_original_repair:'产品问题需要通过原修复流程处理。',
  recovery_baseline_changed:'运行记录在核对后发生变化，请重新核对执行结果。',
  recovery_action_stale:'核对结果已经过期，请重新核对。',
  recovery_checkpoint_changed:'工作流位置已变化，请重新核对。',
  switch_target_revision_stale:'目标配置已有新版本，请重新选择已验证配置，再核对执行结果。',
  switch_target_capabilities_invalid:'目标配置尚未通过所需能力测试，请到模型配置重新验证。',
  switch_conflicting_request:'该角色已有待处理的切换请求，请先核对当前请求。',
}
const checkNames:Record<string,string> = {queued:'等待核对',inspecting:'正在核对',ready:'可以继续',blocked:'仍需处理',applying:'正在续接',succeeded:'已续接',failed:'续接未完成',cancelled:'已取消'}
const roleNames:Record<string,string> = {supervisor:'项目主管',product:'产品经理',architect:'架构师',web_developer:'Web 开发',game_developer:'游戏开发',qa:'质量测试',reviewer:'代码审查'}

export function ReliabilityPanel({run,profiles,agents,readOnly,onChanged,diagnostic,onDetails}: {
  run:Run;profiles:Profile[];agents:Agent[];readOnly:boolean;onChanged:()=>void;diagnostic?:string;onDetails:()=>void
}) {
  const [data,setData] = useState<Diagnostics|null>(null)
  const [switches,setSwitches] = useState<Switch[]>([])
  const [tasks,setTasks] = useState<Task[]>([])
  const [role,setRole] = useState('web_developer')
  const [target,setTarget] = useState('')
  const [execution,setExecution] = useState('')
  const [busy,setBusy] = useState(false)
  const [error,setError] = useState('')
  const [diagnosticError,setDiagnosticError] = useState('')
  const [notice,setNotice] = useState('')
  const gate = useRef(false)
  const selection = useRef(run.id); selection.current=run.id
  const keys = useRef<Record<string,{signature:string;key:string}>>({})
  useEffect(()=>{
    let live=true
    setData(null);setSwitches([]);setTasks([]);setError('');setDiagnosticError('');setNotice('');setTarget('');setExecution('')
    const load=async()=>{
      try {
        const [d,s,t]=await Promise.all([api<Diagnostics>(`/runs/${run.id}/diagnostics`),
          api<Switch[]>(`/runs/${run.id}/model-switches`),api<Task[]>(`/runs/${run.id}/tasks`)])
        if(live){setData(d?.run_id===run.id&&d?.actions?.inspect&&d?.actions?.resume?d:null);setSwitches(Array.isArray(s)?s:[]);setTasks(Array.isArray(t)?t:[]);setDiagnosticError('')}
      } catch {if(live)setDiagnosticError('运行诊断暂时不可用，请稍后刷新。')}
    }
    void load();const timer=setInterval(()=>void load(),3000)
    return()=>{live=false;clearInterval(timer)}
  },[run.id,run.status])
  const terminal=['succeeded','cancelled','failed'].includes(run.status)
  const activeTasks=tasks.filter(t=>t.role===role&&['prepared','running','unknown'].includes(t.status)&&!tasks.some(c=>c.parent_execution_id===t.id))
  const eligible=profiles.filter(p=>p.revision_id&&p.capabilities.structured?.status==='passed'&&
    (!role.endsWith('developer')||p.capabilities.tools?.status==='passed'))
  const doAction=async(kind:string,body:unknown)=>{
    if(gate.current||(readOnly&&kind!=='open-recovery'))return
    const runId=run.id
    gate.current=true;setBusy(true);setError('');setNotice('')
    const signature=JSON.stringify([runId,data?.latest_check?.id||null,body])
    if(keys.current[kind]?.signature!==signature)keys.current[kind]={signature,key:crypto.randomUUID()}
    const key=keys.current[kind].key
    let acknowledged=false
    try {
      const result=await api<Partial<Switch>>(`/runs/${runId}/${kind}`,{method:'POST',headers:{'Idempotency-Key':key},body:JSON.stringify(body)})
      acknowledged=true
      if(kind==='model-switches'&&result.id&&selection.current===runId)setSwitches(previous=>[...previous.filter(item=>item.id!==result.id),result as Switch])
      if(selection.current===runId){setNotice(kind==='open-recovery'?'已打开本轮恢复，原文档和代码保留；请先检查能否继续。':kind==='invocation-policy'?'此运行已改为按进展执行，历史消耗保留；请重新核对后手动继续。':kind==='progress-strategy'?'新策略已登记；请重新核对执行结果后手动继续。':kind==='recovery-checks'?'正在检查能否继续，不消耗模型额度。':kind==='resume'?'已安排继续任务，后续使用模型会产生费用。':kind==='pause'?'正在暂停，请等待当前步骤停止。':'模型切换已保存，检查通过并继续后生效。');onChanged()}
      const latest=await api<Diagnostics>(`/runs/${runId}/diagnostics`)
      if(selection.current===runId)setData(latest)
    } catch(e){if(selection.current===runId){
      if(acknowledged)setError('操作已确认提交，但最新状态暂时无法读取。请稍后核对运行状态。')
      else if(e instanceof TypeError||e instanceof ApiError&&[502,503,504].includes(e.status)){
        setError('未收到操作确认，结果暂时未知。请先核对当前运行状态；当前上下文重试会沿用原请求标识。')
        // A missing HTTP acknowledgement never authorizes another POST.
        try {const latest=await api<Diagnostics>(`/runs/${runId}/diagnostics`);if(selection.current===runId)setData(latest)}catch { /* polling retains the original operation identity */ }
        if(selection.current===runId)onChanged()
      }else {const message=e instanceof Error?e.message:'操作失败，请刷新后重试。';setError(reasons[message]||message)}
    }}
    finally{gate.current=false;setBusy(false)}
  }
  if(terminal||readOnly)return <section className="reliability-panel" aria-label="执行恢复与模型切换">{run.status==='succeeded'?<p>本轮已完成，成果和验收记录保留。</p>:<><h3>继续处理这一轮</h3><p>保留这轮文档、审批、代码和调用记录。先打开恢复并核对失败位置，再从未完成的步骤继续；不会新建一轮或自动取消其他轮次。</p><button disabled={busy||run.status==='cancelling'} onClick={()=>void doAction('open-recovery',{})}>打开此轮恢复 · 不调用模型</button>{error&&<p role="alert" className="failure">{error}</p>}</>}</section>
  const check=data?.latest_check
  const unresolvedTask=tasks.some(t=>['blocked','unknown'].includes(t.status)&&!tasks.some(child=>child.parent_execution_id===t.id))
  const correction=['pause_requested','cancel_requested'].includes(run.reason||'')||run.status==='running'&&data?.auto_correction?.status==='blocked'&&!unresolvedTask?null:data?.auto_correction
  const autoActive=!!correction&&['queued','analyzing','proposed','checking','reviewing_plan'].includes(correction.status)
  return <section className="reliability-panel" aria-label="执行恢复与模型切换">
    {run.status==='paused'&&<p className="note">从暂停处继续：保留本轮已确认的文档、代码和测试记录，检查通过后接着未完成的步骤处理，不会重做已完成的需求分析。</p>}
    {run.status==='paused'&&autoActive?<div className="pause-notice" role="status"><h3>正在自动处理这个问题</h3><p>{correction?.cause||data?.user_notice?.explanation}</p><p>{correction?.message}</p>
      {!!data?.user_notice?.findings.length&&<details><summary>查看已发现的问题</summary><ul>{data.user_notice.findings.map(item=><li key={item}>{item}</li>)}</ul></details>}
      <p>已有成果已保存，暂时不需要你重复点击继续；检查通过后系统会继续任务。你仍可以暂停或取消。</p></div>:
    (!data?.failure||data?.user_notice)&&<PauseNotice run={run} notice={data?.user_notice} diagnostic={diagnostic} onDetails={onDetails} />}
    <h3>任务状态与处理</h3>
    {correction?.cause&&data?.user_notice?.code!=='task_evidence_diagnosis'&&!(run.status==='paused'&&autoActive)&&<p className="note"><strong>此前发现：</strong>{correction.cause}</p>}
    {correction&&data?.user_notice?.code!=='task_evidence_diagnosis'&&!(run.status==='paused'&&autoActive)&&<p role="status" className="note"><strong>自动处理：</strong>{correction.message}</p>}
    {data?.failure&&!data.user_notice&&<div className="recovery-reason"><strong>{data.failure.message}</strong><p>{data.failure.next_step}</p></div>}
    <p className="note">{autoActive?'系统正在处理，暂时无需你操作。':run.status==='paused'?'已有成果已保存，处理好上方问题后可以检查是否能够继续。':run.status==='waiting_approval'?'正在等待你确认方案。':'任务正在推进，完成后会在成果中提供预览和下载。'}</p>
    {data?.usage&&<details><summary>用量与运行详情（高级）</summary><p className="note">{data.usage.max_model_calls===null?`已调用 ${data.usage.model_calls} 次 · 按任务进展执行`:`已用 ${data.usage.model_calls}/${data.usage.max_model_calls} 次调用`} · {data.usage.max_seconds===null?`活动累计 ${Math.ceil(data.usage.active_seconds)} 秒 · 按进展执行`:`活动时间 ${Math.ceil(data.usage.active_seconds)}/${data.usage.max_seconds} 秒`}{data.usage.default_task_tool_limit===null?" · 工具调用按进展执行，无累计次数上限":data.usage.default_task_tool_limit?` · 默认每任务最多 ${data.usage.default_task_tool_limit} 次工具操作`:""}</p>
    {data.failure&&<p>诊断：{data.failure.code} · {data.failure.message}</p>}</details>}
    {data?.progress?.mode==='progress_guarded'&&<details><summary>任务进展与重复检查（高级）</summary><p>调用次数持续累计，不按总次数停止。仅修改文字、重复检查或切模型不会自动清空停滞历史。</p>{data.progress.scopes.map(s=><div key={s.scope}><strong>{roleNames[s.role]||s.role}</strong><p>{s.blocked_reason?reasons[s.blocked_reason]||'该任务需要核对':s.cycle?'检测到状态往返，需要核对':'正在按执行证据观察进展'}</p>{s.last_evidence&&<p>最近执行证据：<code>{s.last_evidence.id}</code></p>}{run.status==='paused'&&s.execution_id&&s.last_evidence&&<ProgressStrategyForm key={s.state_hash} executionId={s.execution_id} stateHash={s.state_hash} evidenceId={s.last_evidence.id} disabled={busy||readOnly} onSubmit={body=>void doAction('progress-strategy',body)} />}</div>)}</details>}
    {run.status==='paused'&&!!data?.unsettled_operations?.length&&<p>有 {data.unsettled_operations.length} 个步骤的完成情况还需确认。检查不会重做这些步骤，已有成果会保留。</p>}
    {run.status==='paused'&&!!data?.unknown_model_requests?.length&&<div className="recovery-check" role="status"><strong>模型请求结果尚未确认</strong><p>以下请求已有调用意图记录，但没有完整结果；不能确认供应商是否已处理，也不能当作尚未开始。查看和免费核对不会发出新模型请求。</p><ul>{data.unknown_model_requests.map(request=><li key={request.id}>{roleNames[request.role]||request.role} · 请求 <code>{request.id}</code> · {request.usage_known?'已知用量保留':'用量未知，不计作零消耗'}</li>)}</ul><p>原模型显式重试可接续经核对的未完成验收，保留已验证片段；更换模型会建立新的验收批次。两者都须先核对再继续，可能产生模型费用。</p></div>}
    {data?.pause_requested&&run.status!=='paused'&&<p role="status">正在停止，确认静止后才能继续。</p>}
    {error&&<p role="alert" className="failure">{error}</p>}
    {diagnosticError&&<p role="alert" className="failure">{diagnosticError}</p>}
    {run.status==='paused'&&(data?.usage.max_model_calls!=null||data?.usage.max_seconds!=null)&&typeof data.epoch==='number'&&<PolicyMigrationForm epoch={data.epoch} calls={data.usage.model_calls} limit={data.usage.max_model_calls} disabled={busy||readOnly} onSubmit={body=>void doAction('invocation-policy',body)} />}
    {notice&&<p role="status">{notice}</p>}
    <div className="buttons">
      {(['running','queued','waiting_approval'].includes(run.status)||autoActive)&&<button disabled={busy||readOnly||!!data?.pause_requested} onClick={()=>void doAction('pause',{})}>{run.status==='paused'?'暂停自动处理':'暂停运行'}</button>}
      {run.status==='paused'&&<button disabled={busy||readOnly||autoActive||!data?.actions.inspect.enabled} onClick={()=>void doAction('recovery-checks',{expected_failure_id:data?.failure?.id||null})}>检查能否继续 · 不消耗模型额度</button>}
      {run.status==='paused'&&<button className="primary" disabled={busy||readOnly||autoActive||!data?.actions.resume.enabled||!check?.checkpoint_id} onClick={()=>void doAction('resume',{
        expected_recovery_id:check?.id,expected_epoch:check?.expected_epoch,expected_checkpoint_id:check?.checkpoint_id,
      })}>继续执行 · 可能产生模型费用</button>}
    </div>
    {data?.actions.resume.blocked_reason&&run.status==='paused'&&!autoActive&&<p className="note">{reasons[data.actions.resume.blocked_reason]||'当前条件尚不允许继续，请先核对执行结果。'}</p>}
    {check&&<div className="recovery-check" role="status"><strong>检查结果：{checkNames[check.status]||'处理中'}</strong>
      {check.result.blockers?.map(reason=><p key={reason}>{reasons[reason]||'有一项执行证据尚待核实，请查看技术详情。'}</p>)}
      {!!check.result.reconciled_tool_ids?.length&&<p>已补记 {check.result.reconciled_tool_ids.length} 个工具结果，没有重跑命令。</p>}
      <details><summary>检查详情（高级）</summary><p>动作 {check.id}</p><ul>{check.result.blockers?.map(r=><li key={r}><code>{r}</code></li>)}</ul></details>
    </div>}
    <details><summary>暂停后重试或切换模型</summary><p>选择原配置表示显式请求原模型重试；选择其他配置表示更换模型。保存决定只请求安全暂停，不发起模型调用；免费核对通过并点击继续后才会执行，原失败和未知结果保留。</p>
      <label>切换角色<select value={role} disabled={busy||readOnly} onChange={e=>{setRole(e.target.value);setTarget('');setExecution('')}}>
        {agents.map(a=><option key={a.role} value={a.role}>{roleNames[a.role]||a.role}</option>)}</select></label>
      {!!activeTasks.length&&<label>续接任务<select value={execution} onChange={e=>setExecution(e.target.value)} disabled={busy||readOnly}><option value="">选择具体任务</option>{activeTasks.map(t=><option key={t.id} value={t.id}>{t.task_id} · 尝试 {t.attempt}</option>)}</select></label>}
      <label>目标模型配置<select value={target} onChange={e=>setTarget(e.target.value)} disabled={busy||readOnly}><option value="">选择已验证配置</option>{eligible.map(p=><option key={p.revision_id} value={p.revision_id}>{p.name} · {p.model_id} · v{p.revision}</option>)}</select></label>
      <button disabled={busy||readOnly||!target||!!activeTasks.length&&!execution||!!switches.find(s=>s.role===role&&s.status==='requested')} onClick={()=>void doAction('model-switches',{role,target_profile_revision_id:target,expected_execution_id:execution||null})}>保存切换并请求暂停</button>
      {!!switches.length&&<ul>{switches.map(s=><li key={s.id}>{roleNames[s.role]||s.role}：{s.status==='requested'?'已保存，待安全续接':s.status==='applied'?'已应用新配置':s.status==='cancelled'?'已取消':s.status==='failed'?'目标配置失效，请重新选择':'处理中'}{s.new_execution_id&&' · 已关联新执行尝试'}</li>)}</ul>}
    </details>
  </section>
}
