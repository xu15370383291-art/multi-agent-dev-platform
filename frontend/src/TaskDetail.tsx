import { useEffect, useRef, useState } from 'react'
import { api, type Profile, type Run } from './api'

type Attempt={id:string;attempt:number;status:string;parent_execution_id:string|null;profile_revision_id:string;recovery_action_id:string|null}
type Tool={id:string;tool_call_id:string;operation:string;status:string;created_at:string;finished_at:string|null;snapshot_hash:string|null;request?:Record<string,unknown>;result:Record<string,unknown>}
type Detail={execution:{id:string;task_id:string;attempt:number;status:string;role:string;prd_id:string;architecture_id:string;parent_execution_id:string|null;recovery_action_id:string|null;base_commit_sha:string|null;snapshot_hash:string|null;result:Record<string,unknown>;tool_calls:number;max_tool_calls:number|null};run:Run;model:Profile;specification:Record<string,unknown>;attempts:Attempt[];tools:Tool[];integrations:{id:string;status:string;base_sha:string;target_sha:string|null;result:Record<string,unknown>}[]}
const labels:Record<string,string>={prepared:'已准备',running:'执行中',succeeded:'执行成功',failed:'失败',unknown:'结果待核对',cancelled:'已取消',blocked:'受阻',merged:'已集成',conflict:'合并冲突'}
const operations:Record<string,string>={read_file:'读取文件',write_file:'写入文件',write_files:'批量写入',run_command:'运行检查命令',git:'Git操作',prepare_dependencies:'准备固定依赖'}

export function TaskDetail({projectId,executionId,refreshKey,onClose}:{projectId:string;executionId:string;refreshKey:string;onClose:()=>void}){
  const dialog=useRef<HTMLDialogElement>(null)
  const returnFocus=useRef(document.activeElement as HTMLElement|null)
  const [detail,setDetail]=useState<Detail|null>(null)
  const [error,setError]=useState('')
  const [selected,setSelected]=useState(executionId)
  const [toolId,setToolId]=useState('')
  const [tool,setTool]=useState<Tool|null>(null)
  const [tab,setTab]=useState<'summary'|'attempts'|'tools'|'versions'>('summary')
  useEffect(()=>{const element=dialog.current; element?.showModal();return()=>{element?.close();if(returnFocus.current?.isConnected)returnFocus.current.focus()}},[])
  useEffect(()=>{setSelected(executionId);setToolId('');setTool(null);setDetail(null)},[executionId,projectId])
  useEffect(()=>{
    let active=true
    setError('')
    api<Detail>(`/projects/${projectId}/task-executions/${selected}`).then(value=>{if(active)setDetail(value)}).catch(error=>{if(active)setError(error.message)})
    return()=>{active=false}
  },[projectId,selected,refreshKey])
  useEffect(()=>{
    if(!toolId){setTool(null);return}
    let active=true
    api<Tool>(`/projects/${projectId}/task-executions/${selected}/tools/${toolId}`).then(value=>{if(active)setTool(value)}).catch(error=>{if(active)setError(error.message)})
    return()=>{active=false}
  },[projectId,selected,toolId,refreshKey])
  const historical=detail?.attempts.some(row=>row.parent_execution_id===selected)
  return <dialog ref={dialog} className="task-drawer" aria-labelledby="execution-heading" onCancel={event=>{event.preventDefault();onClose()}}>
    <header className="drawer-heading"><div><span className="eyebrow">执行凭据</span><h2 id="execution-heading">{detail?`${detail.execution.task_id} · 第 ${detail.execution.attempt} 次执行`:'任务执行详情'}</h2></div><button aria-label="关闭任务详情" onClick={onClose}>关闭 ×</button></header>
    {error&&<p className="failure" role="alert">{error}</p>}
    {!detail&&!error&&<p role="status">正在读取任务与工具记录…</p>}
    {detail&&<>
      <p className="drawer-state">{historical?'历史尝试 · 已有后续续接':detail.execution.status==='running'&&detail.run.status!=='running'?'执行已停止，等待核对或继续':labels[detail.execution.status]||detail.execution.status}<span>工具调用 {detail.execution.max_tool_calls===null?`${detail.execution.tool_calls} 次 · 按进展执行`:`${detail.execution.tool_calls}/${detail.execution.max_tool_calls}`}</span></p>
      <nav className="drawer-tabs" aria-label="任务详情分类">{([['summary','任务与阻塞'],['attempts','尝试历史'],['tools','工具与日志'],['versions','集成与版本']] as const).map(([id,title])=><button key={id} aria-pressed={tab===id} onClick={()=>setTab(id)}>{title}</button>)}</nav>
      {tab==='summary'&&<div className="detail-facts"><h3>{String(detail.specification.title||'批准任务')}</h3><p>{String(detail.specification.goal||'详见批准架构')}</p><dl><dt>负责角色</dt><dd>{detail.execution.role==='web_developer'?'Web 开发':'游戏开发'}</dd><dt>固定模型</dt><dd>{detail.model.name} · {detail.model.model_id} · 修订 {detail.model.revision}</dd><dt>所属运行</dt><dd><code>{detail.run.id}</code></dd><dt>依赖任务</dt><dd>{(detail.specification.depends_on as string[]|undefined)?.join('、')||'无'}</dd><dt>验收编号</dt><dd>{(detail.specification.acceptance_ids as string[]|undefined)?.join('、')||'见批准文档'}</dd></dl><details><summary>任务范围与接口</summary><pre>{JSON.stringify(detail.specification,null,2)}</pre></details><details><summary>任务结果与未验证项</summary><pre>{JSON.stringify(detail.execution.result,null,2)}</pre></details><p className="note">执行成功表示开发任务完成；项目交付还须通过当前候选的构建、QA与独立审查。</p></div>}
      {tab==='attempts'&&<ol className="attempt-spine">{detail.attempts.map(row=><li key={row.id}><button aria-pressed={selected===row.id} onClick={()=>{setDetail(null);setSelected(row.id);setToolId('')}}>第 {row.attempt} 次 · {labels[row.status]||row.status}</button><p>{row.parent_execution_id?'从前一尝试保留工作续接':'首次开发尝试'}{row.recovery_action_id&&' · 已关联恢复核对'}</p><code>{row.id}</code></li>)}</ol>}
      {tab==='tools'&&<div className="tool-journal"><p className="note">仅显示真实持久记录；日志在工具结果保存后可查看。超时、截断和结果待核对会单独标注。</p>{detail.tools.length===0?<p>尚无工具调用。</p>:<ol>{detail.tools.map(row=><li key={row.id}><button aria-pressed={toolId===row.id} onClick={()=>{setTool(null);setToolId(row.id)}}><span>{operations[row.operation]||row.operation}</span><small>{labels[row.status]||row.status}</small></button><time>{new Date(row.created_at).toLocaleTimeString('zh-CN')}</time></li>)}</ol>}{toolId&&!tool&&<p role="status">正在读取工具结果…</p>}{tool&&<article className="tool-output"><h3>{operations[tool.operation]||tool.operation}</h3><p>结果：{labels[tool.status]||tool.status} · 退出码：{String(tool.result.exit_code??'不适用')}</p>{tool.result.timed_out===true&&<p className="failure">工具已超时，不能将输出视为成功。</p>}{tool.result.truncated===true&&<p role="status">输出已达到上限，以下日志已截断。</p>}{['stdout','stderr','error_code'].map(key=>typeof tool.result[key]==='string'&&<section key={key}><h4>{key==='stdout'?'标准输出':key==='stderr'?'错误输出':'错误码'}</h4><pre>{String(tool.result[key])||'无输出'}</pre></section>)}<details><summary>请求摘要与原始结果</summary><pre>{JSON.stringify(tool,null,2)}</pre></details></article>}</div>}
      {tab==='versions'&&<div className="detail-facts"><dl><dt>批准需求</dt><dd><code>{detail.execution.prd_id}</code></dd><dt>批准架构</dt><dd><code>{detail.execution.architecture_id}</code></dd><dt>基础提交</dt><dd><code>{detail.execution.base_commit_sha||'尚未建立'}</code></dd><dt>源码快照</dt><dd><code>{detail.execution.snapshot_hash||'尚无快照'}</code></dd></dl><h3>此任务的集成记录</h3>{detail.integrations.length?detail.integrations.map(row=><article key={row.id}><p>{labels[row.status]||row.status}</p><code>{row.base_sha} → {row.target_sha||'尚无有效集成提交'}</code><details><summary>集成证据与冲突详情</summary><pre>{JSON.stringify(row.result,null,2)}</pre></details></article>):<p>尚未形成集成记录。</p>}</div>}
    </>}
  </dialog>
}
