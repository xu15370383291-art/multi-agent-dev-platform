import { StrictMode, useCallback, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { RunDocuments } from './RunDocuments'
import { EventHistory } from './EventHistory'
import { ReliabilityPanel } from './ReliabilityPanel'
import { useProjectStream } from './useProjectStream'
import { TaskDetail } from './TaskDetail'
import { ModelAssignments } from './ModelAssignments'
import { QualityEvidence } from './QualityEvidence'
import { interruptedQualityLabel } from './qualityStatus'
import { PreviewStatus } from './PreviewStatus'
import { OperationsPanel } from './OperationsPanel'
import { ReviewedTestCorrection } from './ReviewedTestCorrection'
import { api, ApiError, type Adapter, type Agent, type Approval, type Binding, type Candidate, type Delivery, type QualityReport, type Doc, type Event, type Profile, type Project, type Run } from './api'
import './style.css'
import './lightpath.css'
import './workbench.css'
import { Icon } from './Icon'
import { WorkflowCanvas } from './WorkflowCanvas'
import { EventDock } from './EventDock'
import { ProjectSection, projectSections, type ProjectSectionId } from './ProjectSections'
import { CollaborationPanel, type Collaboration, type Plan } from './Collaboration'

const roleNames: Record<string, string> = { supervisor: '项目主管', product: '产品经理', architect: '架构师',
  web_developer: 'Web 开发', game_developer: '游戏开发', qa: '质量测试', reviewer: '代码审查' }
const statusNames: Record<string, string> = { queued: '等待执行', running: '执行中', waiting_approval: '等待审批',
  paused: '已暂停', cancelling: '正在取消', cancelled: '已取消', failed: '执行失败', succeeded: '已完成' }
const phaseNames: Record<string, string> = { quality_correction: '正在核对问题、修正测试并安排修复', auto_correction: '正在分析问题并自动修正', initial_plan: '初步规划', supervisor_assessment: '安排后续工作', integration: '合并开发成果', reviewer_initial: '检查代码与功能', prd_approval: '确认需求方案',
  product: '生成需求文档', architect: '生成技术架构', architecture_approval: '架构审批',
  web_developer: '正在开发应用', game_developer: '正在开发游戏', development_pending: '开发已暂停', candidate_build: '生成可测试版本', quality_pending: '等待功能测试', qa: '测试实际使用效果', reviewer: '检查交付质量', delivery_gate: '检查是否可以交付', delivered: '已交付' }
const thinkingNames = { provider_default: '供应商默认', enabled: '开启', disabled: '关闭' }
type RunAction = {enabled:boolean;blocked_reason:string|null;calls_model:boolean;architecture_id?:string;candidate_id?:string;qa_report_id?:string;reviewer_report_id?:string}
type ApprovalRecord={id:string;document_id:string;status:string;decision:string|null;feedback:string}

function projectState(items:Run[]){return items.find(run=>['running','queued','cancelling'].includes(run.status))||items[0]}
type WorkbenchSnapshot = {cursor:string;project:Project;runs:Run[];current_run_id:string|null;documents:Doc[];approvals:Approval[];events:Event[];plans:Plan[];collaboration:Collaboration|null;candidates:Candidate[];quality_reports:QualityReport[];deliveries:Delivery[];actions?:Record<string,RunAction>;approval_history?:ApprovalRecord[]}

function App() {
  const [page, setPage] = useState<'projects' | 'models' | 'operations'>('projects')
  const [projects, setProjects] = useState<Project[]>([])
  const [projectStates, setProjectStates] = useState<Record<string, Run | undefined>>({})
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [bindings, setBindings] = useState<Binding[]>([])
  const [agents, setAgents] = useState<Agent[]>([])
  const [selected, setSelected] = useState('')
  const [detailSection, setDetailSection] = useState<ProjectSectionId>('overview')
  const [allEvents, setAllEvents] = useState(false)
  const [runs, setRuns] = useState<Run[]>([])
  const [currentRunId, setCurrentRunId] = useState('')
  const [projectLoading, setProjectLoading] = useState(false)
  const runChoices = useRef<Record<string, string>>({})
  const [docs, setDocs] = useState<Doc[]>([])
  const [approvals, setApprovals] = useState<Approval[]>([])
  const [events, setEvents] = useState<Event[]>([])
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [plans, setPlans] = useState<Plan[]>([])
  const [collaboration, setCollaboration] = useState<Collaboration | null>(null)
  const [reports, setReports] = useState<QualityReport[]>([])
  const [qualityFeedback, setQualityFeedback] = useState('')
  const [deliveries, setDeliveries] = useState<Delivery[]>([])
  const [error, setError] = useState('')
  const [projectError,setProjectError]=useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [connected, setConnected] = useState(false)
  const [creating, setCreating] = useState(false)
  const [detailExecution,setDetailExecution]=useState('')
  const [runActions,setRunActions]=useState<Record<string,RunAction>|null>(null)
  const [approvalHistory,setApprovalHistory]=useState<ApprovalRecord[]>([])
  const [editing, setEditing] = useState<Profile | null>(null)
  const [feedback, setFeedback] = useState<Record<string, string>>({})
  const [modelOptions, setModelOptions] = useState<string[]>([])
  const [formUrl, setFormUrl] = useState('')
  const [formModel, setFormModel] = useState('')
  const [thinkingMode, setThinkingMode] = useState<Profile['thinking_mode']>('provider_default')
  const [formAdapter, setFormAdapter] = useState<Adapter | null>(null)
  const [adapterError, setAdapterError] = useState('')
  useEffect(() => {
    setFormAdapter(null); setAdapterError('')
    if (!formUrl || !formModel) return
    const controller = new AbortController()
    const timer = setTimeout(() => {
      api<Adapter>('/model-adapters/resolve', { method: 'POST', signal: controller.signal,
        body: JSON.stringify({ base_url: formUrl, model_id: formModel }) })
        .then(adapter => { if (!controller.signal.aborted) setFormAdapter(adapter) })
        .catch(e => { if (!controller.signal.aborted) setAdapterError(e.message) })
    }, 200)
    return () => { clearTimeout(timer); controller.abort() }
  }, [formUrl, formModel])
  useEffect(() => {
    setFormUrl(editing?.base_url || ''); setFormModel(editing?.model_id || '')
    setThinkingMode(editing?.thinking_mode || 'provider_default')
  }, [editing])
  const runKeys = useRef<Record<string, string>>({})
  const selection = useRef('')
  const previousProject=useRef('')
  const sectionChoices=useRef<Record<string,ProjectSectionId>>({})
  const projectGeneration = useRef(0)
  const reloadGeneration = useRef(0)
  selection.current = selected

  const reload = useCallback(async () => {
    const generation = ++reloadGeneration.current
    const [p, m, a, b] = await Promise.all([api<Project[]>('/projects'), api<Profile[]>('/model-profiles'),
      api<Agent[]>('/agents'), api<Binding[]>('/model-bindings')])
    if (generation !== reloadGeneration.current) return
    setProjects(p); setProfiles(m); setAgents(a); setBindings(b); setConnected(true)
  }, [])

  const projectIds = projects.map(item => item.id).join(',')
  useEffect(() => {
    const shelf = document.querySelector<HTMLElement>('.project-shelf')
    const item = shelf?.querySelector<HTMLElement>('.project-item.selected')
    if (!shelf || !item) return
    const container = shelf.getBoundingClientRect(), active = item.getBoundingClientRect()
    if (active.left < container.left) shelf.scrollLeft += active.left - container.left - 6
    else if (active.right > container.right) shelf.scrollLeft += active.right - container.right + 6
  }, [selected, projects])
  useEffect(() => {
    let active = true
    const ids = projectIds.split(',').filter(Boolean)
    Promise.allSettled(ids.map(async id => [id, projectState(await api<Run[]>(`/projects/${id}/runs`))] as const)).then(results => {
      if (!active) return
      setProjectStates(previous => ({ ...previous, ...Object.fromEntries(results.flatMap(result => result.status === 'fulfilled' ? [result.value] : [])) }))
    })
    return () => { active = false }
  }, [projectIds])

  const loadProject = useCallback(async (id: string) => {
    if (!id || selection.current!==id) return null
    const generation=++projectGeneration.current
    let snapshot:WorkbenchSnapshot|null=null
    try{
      const value=await api<WorkbenchSnapshot>(`/projects/${id}/workbench${runChoices.current[id]?'?'+new URLSearchParams({run_id:runChoices.current[id]}):''}`)
      if(value?.project?.id===id&&Array.isArray(value.runs))snapshot=value
    }catch(error){if(!(error instanceof ApiError&&error.status===404))throw error}
    if(snapshot){
      if(selection.current!==id||generation!==projectGeneration.current)return null
      const chosen=snapshot.current_run_id
      setCurrentRunId(chosen||'');setProjectStates(previous=>({...previous,[id]:projectState(snapshot!.runs)}))
      setProjects(previous=>previous.map(project=>project.id===id?snapshot!.project:project))
      setRuns(snapshot.runs);setDocs(snapshot.documents.filter(doc=>doc.run_id===chosen).sort((a,b)=>b.version-a.version))
      setApprovals(snapshot.approvals.filter(row=>row.run_id===chosen));setEvents(snapshot.events);setCandidates(snapshot.candidates)
      setReports(snapshot.quality_reports);setDeliveries(snapshot.deliveries);setPlans(snapshot.plans);setCollaboration(snapshot.collaboration)
      setRunActions(snapshot.actions||null)
      setApprovalHistory(snapshot.approval_history||[])
      setProjectLoading(false)
      return snapshot.cursor
    }
    const [r, d, a, e] = await Promise.all([api<Run[]>(`/projects/${id}/runs`),
      api<Doc[]>(`/projects/${id}/documents`), api<Approval[]>(`/projects/${id}/approvals/pending`),
      api<Event[]>(`/projects/${id}/events`)])
    const chosen = r.find(run => run.id === runChoices.current[id]) || r[0]
    const c = chosen ? await api<Candidate[]>(`/runs/${chosen.id}/candidates`) : []
    const [q, v] = chosen ? await Promise.all([api<QualityReport[]>(`/runs/${chosen.id}/quality-reports`), api<Delivery[]>(`/runs/${chosen.id}/deliveries`)]) : [[], []]
    const [planData, collaborationData] = chosen ? await Promise.all([api<Plan[]>(`/runs/${chosen.id}/plans`), api<Collaboration>(`/runs/${chosen.id}/collaboration`)]) : [[], null]
    if (selection.current !== id || generation!==projectGeneration.current || (runChoices.current[id] && chosen?.id !== runChoices.current[id])) return null
    setCurrentRunId(chosen?.id || '')
    setProjectStates(previous => ({ ...previous, [id]: projectState(r) }))
    setRuns(r); setDocs(d.filter(doc=>doc.run_id===chosen?.id).sort((a,b)=>b.version-a.version)); setApprovals(a.filter(row=>row.run_id===chosen?.id)); setEvents(e); setCandidates(c)
    setReports(q); setDeliveries(v); setPlans(planData); setCollaboration(collaborationData)
    setRunActions(null)
    setApprovalHistory([])
    setProjectLoading(false)
    return null
  }, [])
  const refreshFromStream=useCallback(async(id:string)=>{
    try{const cursor=await loadProject(id);if(selection.current===id)setProjectError('');return cursor}catch(error){if(selection.current===id){setProjectError(error instanceof Error?error.message:'项目状态查询失败');setProjectLoading(false)}throw error}
  },[loadProject])
  const live=useProjectStream(page==='projects'?selected:'',refreshFromStream)

  useEffect(() => { reload().catch(e => { setError(e.message); setConnected(false) }) }, [reload])
  useEffect(() => {
    if(previousProject.current)sectionChoices.current[previousProject.current]=detailSection
    previousProject.current=selected
    setDetailSection(sectionChoices.current[selected]||'overview'); setAllEvents(false); setQualityFeedback('')
    setDetailExecution('')
    setRunActions(null)
    setApprovalHistory([])
    setProjectError('')
    setCurrentRunId(''); setRuns([]); setDocs([]); setApprovals([]); setEvents([]); setCandidates([])
    setReports([]); setDeliveries([]); setPlans([]); setCollaboration(null)
    if (!selected) return
    setProjectLoading(true)
  }, [selected, loadProject])

  async function action(work: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('')
    try { await work(); await reload(); await loadProject(selection.current) }
    catch (e) { setError(e instanceof Error ? e.message : '操作失败') }
    finally { setBusy(false) }
  }

  const project = projects.find(p => p.id === selected)
  const current = runs.find(run => run.id === currentRunId) || runs[0]
  const qualityTarget=(reuse=false)=>{
    if(runActions){
      const target=runActions[reuse?'quality_recheck':'quality_revision']
      if(!target?.enabled||!target.candidate_id||!target.qa_report_id||!target.reviewer_report_id)throw new Error(target?.blocked_reason||'当前报告尚不满足此操作条件，请刷新核对。')
      return {candidate_id:target.candidate_id,qa_report_id:target.qa_report_id,reviewer_report_id:target.reviewer_report_id}
    }
    const qa=[...reports].reverse().find(row=>row.role==='qa'),reviewer=qa&&[...reports].reverse().find(row=>row.role==='reviewer'&&row.candidate_id===qa.candidate_id&&row.attempt===qa.attempt)
    if(!qa||!reviewer)throw new Error('需要同一候选及验证轮次的QA和独立审查报告。')
    return {candidate_id:qa.candidate_id,qa_report_id:qa.id,reviewer_report_id:reviewer.id}
  }
  const latest = runs[0]
  const hasExecutingRound = runs.some(run=>['queued','running','cancelling'].includes(run.status))
  const viewingHistory = !!current && current.id !== latest?.id && !current.recovery_open
  const activeApprovals = viewingHistory||!current||['cancelled','failed','succeeded','cancelling'].includes(current.status)?[]:approvals.filter(approval => approval.run_id === current.id)
  const lastModelFailure = events.find(e => e.kind === 'model.failed' && e.run_id === current?.id)?.data.diagnostic as {message?: string; error_code?: string} | undefined
  const selectRun=(id:string)=>{
    if(id===current?.id||!project)return
    projectGeneration.current++;setProjectLoading(true)
    runChoices.current[project.id]=id;setCurrentRunId(id)
    setDocs([]);setApprovals([]);setApprovalHistory([]);setRunActions(null);setEvents([])
    setCandidates([]);setReports([]);setDeliveries([]);setPlans([]);setCollaboration(null)
    setQualityFeedback('');setDetailExecution('');setError('');setNotice('')
    loadProject(project.id).catch(e=>{setError(e.message);setProjectLoading(false)})
  }
  const runPicker=(label:string)=><label className="run-picker">{label}<select aria-label={label} disabled={busy||projectLoading} value={current?.id||''} onChange={e=>selectRun(e.target.value)}>{runs.map((run,index)=><option key={run.id} value={run.id}>{index===0?'最新一轮':`历史第 ${runs.length-index} 轮`} · {statusNames[run.status]||run.status}{run.created_at?` · ${new Date(run.created_at).toLocaleString('zh-CN')}`:''} · {run.id.slice(0,8)}</option>)}</select></label>
  const defaultBinding = bindings.find(b => b.scope === 'global' && b.role === 'default')
  const defaultModel = profiles.find(p => p.id === defaultBinding?.profile_id)
  const planningReady = ['supervisor', 'product', 'architect', project?.project_type === 'web_app' ? 'web_developer' : 'game_developer', 'qa', 'reviewer'].every(role => {
    const binding = [[selected, role], [selected, 'default'], ['global', role], ['global', 'default']]
      .map(([scope, name]) => bindings.find(item => item.scope === scope && item.role === name)).find(Boolean)
    return profiles.find(profile => profile.id === binding?.profile_id)?.capabilities.structured?.status === 'passed'
  })

  return <div className="shell">
    <aside className="sidebar" aria-label="工具栏">
      <a className="brand" href="#" aria-label="协作工坊首页" onClick={e => { e.preventDefault(); setPage('projects') }}><svg viewBox="0 0 40 40" aria-hidden="true"><path d="M18 5L4 20l11 11L29 16 M25 9l11 11-14 15 M13 26L27 11" /></svg></a>
      <nav aria-label="主导航">
        <button aria-label="项目工作台" title="项目工作台" className={page === 'projects' ? 'nav active' : 'nav'} onClick={() => setPage('projects')}><Icon name="flow" /><span>项目</span></button>
        <button aria-label="模型配置" title="模型配置" className={page === 'models' ? 'nav active' : 'nav'} onClick={() => setPage('models')}><Icon name="settings" /><span>模型</span></button>
        <button aria-label="运行维护" title="运行维护" className={page === 'operations' ? 'nav active' : 'nav'} onClick={() => setPage('operations')}><Icon name="activity" /><span>维护</span></button>
      </nav>
      <nav className="tool-shortcuts" aria-label="项目快捷入口">{([
        ['documents', 'file', '打开需求与审批'], ['development', 'project', '打开开发协作'], ['quality', 'quality', '打开质量验收'], ['activity', 'activity', '打开执行记录'],
      ] as const).map(([section, icon, name]) => <button key={section} className="nav" aria-label={name} title={name} disabled={!selected} onClick={() => { setPage('projects'); setDetailSection(section) }}><Icon name={icon} /></button>)}</nav>
      <div className="sidebar-bottom"><span className={connected ? 'dot green' : 'dot'} /><span>本机</span></div>
    </aside>

    <main>
      <header className="topbar"><div className="console-brand"><strong>协作工坊</strong><span>/</span><h1>生产控制台</h1></div>
        <select className="top-project-picker" aria-label="当前项目" value={selected} onChange={e => { setSelected(e.target.value); setPage('projects') }}><option value="">选择项目</option>{projects.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
        <div className="top-utilities"><span className="connection"><span className={connected ? 'dot green' : 'dot'} />{connected ? '本机服务已连接' : '服务尚未连接'}</span>
          {current && <span className={'budget-meter' + (current.max_model_calls!==null&&current.model_calls >= current.max_model_calls ? ' is-exhausted' : '')} title="所选运行的模型调用用量"><span>模型调用</span><code>{current.max_model_calls===null?`${current.model_calls} 次 · 按进展执行`:`${current.model_calls} / ${current.max_model_calls}`}</code>{current.max_model_calls!==null&&<meter min={0} max={Math.max(1, current.max_model_calls)} value={current.model_calls} aria-label="模型调用用量" />}</span>}</div><button className="primary create-project" disabled={busy} onClick={() => { setPage('projects'); setCreating(!creating) }}>{creating ? '收起表单' : '＋ 新建项目'}</button></header>
      <div className="content">
        {error && <div role="alert" className="message error">{error}<button onClick={() => action(reload)}>重新连接</button></div>}
        {projectError&&<div role="alert" className="message error">项目状态读取失败：{projectError}<button onClick={()=>void refreshFromStream(selected).catch(()=>{})}>重新读取项目状态</button></div>}
        {notice && <div role="status" className="message notice">{notice}</div>}
        {page === 'projects' ? <>
          {!defaultModel && <div className="setup"><div><strong>先接入你的模型</strong><p>填写服务地址和 API Key，验证后即可启动需求与架构流程。</p></div>
            <button onClick={() => setPage('models')}>配置模型 ↗</button></div>}
          {creating && <form className="panel form" onSubmit={e => {
            e.preventDefault(); const data = new FormData(e.currentTarget)
            action(async () => { const created = await api<Project>('/projects', { method: 'POST', body: JSON.stringify(Object.fromEntries(data)) });
              setSelected(created.id); setCreating(false); setNotice('项目已创建，尚未启动运行。') })
          }}><h2>新建项目</h2><div className="form-row"><label>项目名称<input name="name" required maxLength={100} placeholder="例如：我的 2048 游戏" /></label>
            <label>项目类型<select name="project_type"><option value="browser_game">浏览器小游戏</option><option value="web_app">Web 软件</option></select></label></div>
            <label>描述你的需求<textarea name="requirement" required minLength={10} maxLength={20000} rows={4} placeholder="希望解决什么问题？需要哪些功能？有哪些约束？" /></label>
            <button className="primary" disabled={busy}>创建项目</button></form>}
          <div className="workspace-grid"><section className="project-list" aria-label="我的项目"><div className="shelf-label">项目 <span className="count">{projects.length}</span></div><div className="project-shelf">
            {projects.length === 0 ? <div className="empty"><div className="empty-mark">▧</div><h3>第一个项目，从这里开始</h3><p>创建一个 Web 软件或浏览器小游戏项目。</p><button onClick={() => setCreating(true)}>创建第一个项目 →</button></div> :
              projects.map(p => <button key={p.id} className={'project-item ' + (selected === p.id ? 'selected' : '')} onClick={() => setSelected(p.id)}>
                <span className={'project-dot ' + (projectStates[p.id]?.status || 'unstarted')} /><span><strong>{p.name}</strong><small>{projectStates[p.id] ? statusNames[projectStates[p.id]!.status] || projectStates[p.id]!.status : p.project_type === 'browser_game' ? '浏览器小游戏' : 'Web 软件'}</small></span></button>)}
          </div></section><section className="project-detail">
            {!project ? <div className="panel quiet"><div className="eyebrow">项目工作台</div><h2>每一步，都有记录。</h2><p>选择项目后，在这里查看文档、审批事项与执行事件。</p><div className="note">已接通规划审批、任务依赖与隔离并行、源码修复、固定候选 QA、独立审查及交付门槛。每个版本的实际结果以报告为准。</div></div> : <>
              <nav className="project-sections" aria-label="项目内容分类">{projectSections.map(item =>
                <button key={item.id} aria-pressed={detailSection === item.id} onClick={() => setDetailSection(item.id)}>{item.title}{item.id === 'documents' && activeApprovals.length > 0 && <span className="count">{activeApprovals.length} 待审批</span>}</button>)}</nav>
              <div className="console-body"><div className="console-main">
              <div className="stream-status" role="status" aria-label="项目数据连接"><span>{live.connection}</span>{live.updatedAt&&<small>状态更新于 {live.updatedAt}</small>}</div>
              <ProjectSection id="overview" active={detailSection}>
                <WorkflowCanvas docs={docs} approvals={activeApprovals} plans={plans} collaboration={collaboration} run={current} candidates={candidates} reports={reports} deliveries={deliveries} onNavigate={setDetailSection} />

              {deliveries.map(delivery => <article className="panel delivery-card" key={delivery.id}>
                <div className="section-head"><h2>项目交付</h2><span className="badge">门槛已通过</span></div>
                <p>代码版本 <code style={{overflowWrap:'anywhere'}}>{delivery.manifest.commit_sha}</code></p>
                <p>包含源码、生产构建、启动说明、版本清单、测试资产和验收报告。</p>
                <div className="buttons"><a href={delivery.manifest.preview_url} target="_blank" rel="noopener noreferrer">{project.project_type === 'web_app' ? '打开应用预览 ↗' : '打开游戏预览 ↗'}</a><a href={`/api/v1/deliveries/${delivery.id}/download`} download>下载交付 ZIP</a></div>
                <details><summary>查看验收明细（{delivery.manifest.acceptance.length} 项）</summary><p>AC 编号对应需求中的验收标准；同一要求可以有多项检查。</p><ul>{delivery.manifest.acceptance.map((item,index) => <li key={index}>{item.criterion_id} · {item.name} · {item.status === 'passed' ? '通过' : item.status}</li>)}</ul></details>
                <p className="acceptance-summary">验收检查 {delivery.manifest.acceptance.length} 项 · 通过 {delivery.manifest.acceptance.filter(item => item.status === 'passed').length} 项 · 其他结果 {delivery.manifest.acceptance.filter(item => item.status !== 'passed').length} 项</p>
                <PreviewStatus deliveryId={delivery.id} />
                <details><summary>交付清单与摘要</summary><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{JSON.stringify(delivery, null, 2)}</pre></details>
              </article>)}
                {!projectLoading && deliveries.length === 0 && <div className="panel"><h2>尚无可交付成果</h2><p>{!current ? '项目已创建。点击“启动需求分析”开始，随后需要你分别审批需求和架构。' : current.status === 'paused' || current.status === 'failed' ? '当前运行尚未通过交付门槛。查看上方状态，以及质量验收和执行记录中的原因。' : current.status === 'cancelled' ? '本次运行已取消，没有生成交付成果。' : '当前运行尚未生成交付成果。可到需求与审批、开发协作和质量验收查看进展。'}</p></div>}
              </ProjectSection>
              <ProjectSection id="documents" active={detailSection}>
                <div className="panel run-document-context"><h2>文档所属轮次</h2>{runPicker('文档所属轮次')}<p>{viewingHistory?'正在查看历史轮次，仅供查阅。':current?.id!==latest?.id?'正在处理历史轮次，已打开恢复。':'正在查看最新一轮。'}这里的文档、审批、开发、测试和交付均对应所选轮次。</p><p className="note">从暂停处继续会保留本轮文档；新开一轮会重新分析需求。两者不会混在一起。</p></div>
                {!projectLoading&&<RunDocuments run={current} documents={docs} approvals={approvals} history={approvalHistory} readOnly={viewingHistory||!current||['cancelled','failed','succeeded','cancelling'].includes(current.status)} busy={busy} feedback={feedback} onFeedback={(id,value)=>setFeedback({...feedback,[id]:value})} onDecision={(approval,doc,decision)=>void action(async()=>{
                  await api(`/approvals/${approval.id}/decision`,{method:'POST',body:JSON.stringify({decision,feedback:feedback[approval.id]||'',document_version:doc.version})})
                  setNotice('审批决定已保存，等待本轮工作流继续。')
                })}/>}
              </ProjectSection>
              <ProjectSection id="development" active={detailSection}>
                {!projectLoading && !collaboration?.current_plan_id && <div className="panel"><p>尚无任务协作计划。需求和架构批准后，开发任务会显示在这里。</p></div>}
                <CollaborationPanel plans={plans} data={collaboration} onInspect={setDetailExecution} />
              </ProjectSection>
              <ProjectSection id="quality" active={detailSection}>
                {!projectLoading && candidates.length === 0 && reports.length === 0 && <div className="panel"><p>尚无候选版本或质量报告。开发并集成代码后，才能构建和验收。</p></div>}
              {candidates.map(candidate => <article className="panel" key={candidate.id}>
                <div className="section-head"><h2>候选版本</h2><span className="badge">{{ build_passed: '构建通过', build_failed: '构建失败', building: '构建中', unknown: '构建结果待核对' }[candidate.status] || candidate.status}</span></div>
                <p>代码版本 <code style={{ overflowWrap: 'anywhere' }}>{candidate.commit_sha}</code></p>
                <p className="note">构建结果仅适用于此版本；QA、审查与交付状态见对应报告。</p>
                {candidate.build_result.error_code && <p className="failure">{candidate.build_result.error_code}</p>}
                <details><summary>构建日志</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{[candidate.build_result.stdout, candidate.build_result.stderr].filter(Boolean).join('\n') || '暂无命令输出'}</pre></details>
              </article>)}
              {reports.filter(report => !reports.some(other => other.candidate_id === report.candidate_id && (other.attempt || 1) > (report.attempt || 1))).map(report => <article className="panel" key={report.id}>
                <div className="section-head"><h2>{report.role === 'qa' ? 'QA 浏览器验收' : report.role === 'reviewer_initial' ? 'Reviewer 源码初审（非最终验收）' : 'Reviewer 独立审查'}</h2><span className="badge">{interruptedQualityLabel(report,current) || { passed: '通过', failed: '未通过', running: '执行中', unknown: '结果待核对' }[report.status] || report.status}</span></div>
                <p>{report.role === 'qa' ? '用途：实际运行测试，检查功能、交互和需求中的验收标准。' : report.role === 'reviewer_initial' ? '用途：提前审查源码；初审通过仍需等待 QA 和最终独立审查。' : '用途：结合源码和当前 QA 证据，独立判断是否达到交付要求。'}</p>
                <p className="note">{report.candidate_id===candidates.at(-1)?.id?'所选运行的最新候选':'历史候选的证据，不能作为当前版本通过依据'} · 候选 <code>{report.candidate_id}</code></p>
                <p>适用版本 <code style={{overflowWrap:'anywhere'}}>{report.commit_sha}</code></p>
                <p className="note">第 {report.attempt || 1} 次验证{report.supersedes_id ? ' · 保留旧版失败证据' : ''}</p>
                <QualityEvidence projectId={project.id} report={report} />
                <details><summary>查看报告与测试证据</summary><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{JSON.stringify(report, null, 2)}</pre></details>
              </article>)}
              {reports.some(report => reports.some(other => other.candidate_id === report.candidate_id && (other.attempt || 1) > (report.attempt || 1))) && <details className="panel"><summary>历史质量验证（保留原结果）</summary>{reports.filter(report=>reports.some(other=>other.candidate_id===report.candidate_id&&(other.attempt||1)>(report.attempt||1))).map(report=><details key={report.id}><summary>{report.role} · 第 {report.attempt||1} 次 · {report.status} · {report.commit_sha.slice(0,8)}</summary><QualityEvidence projectId={project.id} report={report}/></details>)}<pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{JSON.stringify(reports.filter(report => reports.some(other => other.candidate_id === report.candidate_id && (other.attempt || 1) > (report.attempt || 1))), null, 2)}</pre></details>}
              {!viewingHistory && current?.status === 'paused' && current.phase === 'delivery_gate' && reports.some(r => r.status === 'failed') && <div className="panel">
                <h2>测试脚本修订</h2><p>仅修订已知测试脚本错误；源码、候选与批准基线保持不变，沿用原运行预算。旧报告保留，修订后重新运行全部用例及独立审查。产品断言失败或源码阻断问题不能通过此入口绕过。</p>
                <label>测试脚本修订意见<textarea rows={3} value={qualityFeedback} onChange={e => setQualityFeedback(e.target.value)} placeholder="说明测试 API 错误及需要补足的验收要求" /></label>
                <button disabled={busy || qualityFeedback.trim().length < 12 || (!!runActions&&!runActions.quality_revision?.enabled)} onClick={() => action(async () => {
                  const target=qualityTarget()
                  await api(`/candidates/${target.candidate_id}/quality-revisions`, {method:'POST',body:JSON.stringify({qa_report_id:target.qa_report_id,reviewer_report_id:target.reviewer_report_id,feedback:qualityFeedback})})
                  setNotice('新测试资产验证已入队，旧报告保留，原运行预算不变。'); setQualityFeedback('')
                })}>修订测试并重新验证</button>
                <button disabled={busy || qualityFeedback.trim().length < 12 || (!!runActions&&!runActions.quality_recheck?.enabled)} onClick={() => action(async () => {
                  const target=qualityTarget(true)
                  await api(`/candidates/${target.candidate_id}/quality-revisions`, {method:'POST',body:JSON.stringify({qa_report_id:target.qa_report_id,reviewer_report_id:target.reviewer_report_id,feedback:qualityFeedback,reuse_tests:true})})
                  setNotice('原测试重验已入队，脚本逐字保留，重新执行并独立审查，原预算不变。'); setQualityFeedback('')
                })}>原测试重新验证</button>
                {runActions&&!runActions.quality_revision?.enabled&&<p className="note">{runActions.quality_revision?.blocked_reason}</p>}
                <p className="note">重新验证可能产生模型费用；原测试重验还需核对测试环境是否已经更新。</p>
                <p className="note">修复测试环境后可重验原测试：仅接受已知用例超时、已更换验证镜像的情况，不修改脚本；仍须全部执行通过并重新独立审查。未知工具结果和产品断言失败不接受。</p>
              </div>}
              {runActions?.reviewed_quality_correction&&<ReviewedTestCorrection key={`${runActions.reviewed_quality_correction.candidate_id}:${runActions.reviewed_quality_correction.qa_report_id}:${runActions.reviewed_quality_correction.reviewer_report_id}`} target={runActions.reviewed_quality_correction} reports={reports} onRun={action}/>}
              </ProjectSection>
              <ProjectSection id="activity" active={detailSection}>
                <div className="panel"><h2>执行时间线</h2><label>记录范围<select value={allEvents ? 'all' : 'current'} onChange={e => setAllEvents(e.target.value === 'all')}><option value="current">所选运行</option><option value="all">整个项目（含历史运行）</option></select></label>
                <p>原始数据用于追溯，不需要填写或逐条阅读。切换范围可查看历史记录。</p>
                {(allEvents || current) && <EventHistory key={project.id + (allEvents ? "all" : current?.id)} projectId={project.id} runId={allEvents ? undefined : current?.id} initial={events} />}</div>
              </ProjectSection>
              </div><aside className="inspector" aria-label="当前运行与操作"><div className="inspector-caption">{activeApprovals.length ? '当前需要你处理' : '运行控制'}</div>
                {activeApprovals.length > 0 && <div className="panel next-action"><h2>需要你确认</h2><p>有 {activeApprovals.length} 份文档待审批。阅读并批准后，流程才会继续。</p><button onClick={() => setDetailSection('documents')}>前往审批</button></div>}
              <div className="panel run-control"><div className="section-head"><h2>{project.name}</h2>{current && <span className={'badge run-badge status-' + current.status}>{statusNames[current.status] || current.status}</span>}</div>
                {runs.length>0&&runPicker('运行记录')}
                {viewingHistory && <p className="note">正在查看历史运行。下方成果、任务和质量报告均属于所选轮次；可通过“打开此轮恢复”继续处理这一轮。</p>}
                {current?.recovery_open&&current.id!==latest?.id&&<p className="note">正在处理已打开恢复的历史轮次。所有操作只影响这一轮，其他轮次保留；同项目执行会依次排队。</p>}
                {projectLoading && <p role="status">正在加载所选运行的记录…</p>}
                <details open={!current}><summary>查看最初提交的需求</summary><p className="requirement">{project.requirement}</p></details>
                {current && <div className="run-state"><strong>{phaseNames[current.phase] || '正在处理项目'}</strong><span>{current.status==='paused'?'当前步骤已暂停，请查看下方原因':current.status==='cancelled'?'本轮已取消；已有文档与记录保留，仅供查阅':current.status==='failed'?'本轮执行失败，请查看执行记录中的原因':current.status==='cancelling'?'正在停止本轮任务，请等待完成核对':current.status==='succeeded'?'成果已准备好':current.status==='waiting_approval'?'等待你的确认':'系统正在处理，请留意任务进度'}</span>
                  {current.reason && current.status !== 'paused' && <p>{current.reason}</p>}</div>}
                {current && <ReliabilityPanel key={current.id} run={current} profiles={profiles} agents={agents} readOnly={viewingHistory} diagnostic={lastModelFailure?.message} onDetails={() => setDetailSection('activity')} onChanged={() => { void loadProject(project.id) }} />}
                <div className="buttons">
                  {!viewingHistory&&current?.status==='paused'&&current.reason==='multi_task_scope_and_interfaces_required'&&runActions?.planning_revision?.blocked_reason&&<p className="note">{runActions.planning_revision.blocked_reason}</p>}
                  {!viewingHistory&&current?.status==='paused'&&['developer_check_evidence_required','execution_failed: ValidationError'].includes(current.reason||'')&&runActions?.known_development?.blocked_reason&&<p className="note">{runActions.known_development.blocked_reason}</p>}
                  {!viewingHistory && current?.status === 'paused' && current.reason === 'multi_task_scope_and_interfaces_required' && <button disabled={busy || projectLoading || (!!runActions&&!runActions.planning_revision?.enabled)} onClick={() => action(async () => {
                    const architecture = docs.find(doc => doc.run_id === current.id && doc.kind === 'architecture' && (!runActions||doc.id===runActions.planning_revision?.architecture_id))
                    if (!architecture) throw new Error('未找到对应架构版本，请刷新后重试。')
                    await api(`/runs/${current.id}/revise-invalid-architecture`, { method: 'POST', body: JSON.stringify({ architecture_id: architecture.id }) })
                    setNotice('架构修正已入队，沿用原运行预算。生成新版本后需要你重新审批。')
                  })}>修正架构并重新提交审批</button>}
                  {!viewingHistory && current?.status === 'paused' && ['game_developer', 'web_developer'].includes(current.phase) && ['execution_failed: ValidationError', 'developer_check_evidence_required'].includes(current.reason || '') && <button disabled={busy || (!!runActions&&!runActions.known_development?.enabled)} onClick={() => action(async () => {
                    await api(`/runs/${current.id}/resume-known-development`, {method:'POST'}); setNotice('已核对已知工具结果与快照，保留原预算继续开发。')
                  })}>核对后继续开发</button>}
                  {!hasExecutingRound && <details className="run-launch" open={!current}><summary>{current ? '新开一轮 · 从需求分析开始' : '开始项目'}</summary>
                    <p>{current ? '此操作创建独立的新一轮，从需求分析开始，并消耗模型费用。它不会接着原来的断点继续；原轮次的文档、代码和失败记录保留，可切换轮次查看。' : '先生成需求文档，再由你确认范围。'}</p>
                  {current&&<p className="note">不必取消旧轮次。旧轮次仍可切换查看和单独恢复；同项目的执行会依次排队。</p>}
                  <button className={current ? '' : 'primary'} disabled={busy || !planningReady || hasExecutingRound}
                  onClick={() => action(async () => { const key = runKeys.current[project.id] ||= crypto.randomUUID();
                    await api(`/projects/${project.id}/runs`, { method: 'POST', headers: { 'Idempotency-Key': key } });
                    delete runKeys.current[project.id]; delete runChoices.current[project.id]; setNotice('运行已入队，由后台执行器处理。') })}>{current ? '新开一轮（重新分析需求）' : '启动需求分析'}</button>
                  </details>}
                  {!viewingHistory && current && !['cancelled', 'failed', 'succeeded'].includes(current.status) && <button disabled={busy} onClick={() => action(async () => {
                    await api(`/runs/${current.id}/cancel`, { method: 'POST' }); setNotice('取消请求已提交；活动模型请求可能需要等待超时。') })}>取消运行</button>}</div>
              </div>
                <details className="agent-directory"><summary>协作团队 · {agents.length} 个角色</summary><div className="team">{agents.map(agent => <div className="agent" key={agent.role} title={agent.purpose}><span className={'avatar ' + (agent.connected ? 'ready' : '')}>{roleNames[agent.role]?.slice(0, 1)}</span><span>{roleNames[agent.role]}<small>{agent.connected ? '已接入流程' : '待接入'}</small></span></div>)}</div></details>
              </aside></div>
              <EventDock events={events} runId={current?.id} onOpen={() => setDetailSection('activity')} />
            </>}
          </section></div>
        </> : page==='operations'?<OperationsPanel projects={projects}/>:<>
          <div className="page-title"><div><div className="eyebrow">连接你的推理能力</div><h1>模型由你选择。</h1><p>维护多份连接，为整个团队或指定角色分配模型。</p></div></div>
          <div className="model-layout"><section className="panel"><h2>{editing ? `编辑 ${editing.name}` : '添加模型连接'}</h2>
            <form className="form" key={editing?.id || 'new'} onSubmit={e => { e.preventDefault(); const form = e.currentTarget; const data = Object.fromEntries(new FormData(form));
              action(async () => { const payload = { ...data, thinking_budget: data.thinking_budget ? Number(data.thinking_budget) : null, max_output_tokens: Number(data.max_output_tokens), timeout_seconds: Number(data.timeout_seconds), api_key: data.api_key || undefined, expected_revision: editing?.revision };
                await api('/model-profiles' + (editing ? '/' + editing.id : ''), { method: editing ? 'PATCH' : 'POST', body: JSON.stringify(payload) });
                form.reset(); setEditing(null); setFormUrl(''); setFormModel(''); setThinkingMode('provider_default'); setModelOptions([]); setNotice('模型配置已保存，密钥存放于 Windows 凭据管理器。请测试能力后再启动项目。') }) }}>
              <label>配置名称<input name="name" required maxLength={80} defaultValue={editing?.name} placeholder="例如：我的开发模型" /></label>
              <label>接口类型<select name="protocol"><option value="openai_chat_completions">OpenAI 兼容 Chat Completions</option></select></label>
              <label>Base URL<input name="base_url" type="url" required value={formUrl} onChange={e => setFormUrl(e.target.value)} placeholder="https://your-provider.example/v1" /></label>
              <label>模型型号<input name="model_id" list="model-options" required maxLength={150} value={formModel} onChange={e => setFormModel(e.target.value)} placeholder="填写服务商提供的模型 ID" /></label>
              <div className="form-row"><label>最大输出 Token（含思考）<input name="max_output_tokens" type="number" min={256} max={65536} required defaultValue={editing?.max_output_tokens || 4096} /></label>
              <label>请求超时（秒）<input name="timeout_seconds" type="number" min={10} max={600} required defaultValue={editing?.timeout_seconds || 90} /></label></div>
              <p className="note">思考模型可能先消耗输出预算再返回工具结果；预算不足会明确失败。修改后需重新测试能力，新执行保存配置版本。</p>
              {formAdapter?.supports_thinking_budget && thinkingMode !== 'disabled' && <label>思考 Token 上限（可选）<input name="thinking_budget" type="number" min={128} max={32768} defaultValue={editing?.thinking_budget || ''} placeholder="留空使用供应商默认；长思考可设置如 2048" /></label>}
              {formAdapter && <p className="note">已识别适配：{formAdapter.label}</p>}
              {adapterError && <p role="alert" className="failure">{adapterError}</p>}
              {formAdapter?.supports_thinking && <label>思考模式<select name="thinking_mode" value={thinkingMode} onChange={e => setThinkingMode(e.target.value as Profile['thinking_mode'])}>
                <option value="provider_default">供应商默认（保留模型默认行为）</option><option value="enabled">开启</option><option value="disabled">关闭</option>
              </select><small>开启时可能增加耗时与用量；不支持该开关的型号会在能力检测时报错。可保存不同配置分配给不同角色。</small></label>}
              <datalist id="model-options">{modelOptions.map(id => <option key={id} value={id} />)}</datalist>
              <label>API Key<input name="api_key" type="password" autoComplete="new-password" required={!editing} placeholder={editing ? '留空则保留已有密钥' : '仅在本机后端受保护地保存'} /></label>
              <div className="buttons"><button className="primary" disabled={busy || !formAdapter}>保存配置</button>{editing && <button type="button" onClick={() => { setEditing(null); setModelOptions([]) }}>取消编辑</button>}</div>
            </form></section>
            <section><div className="panel"><h2>已保存的连接 <span className="count">{profiles.length}</span></h2>
              {profiles.length === 0 && <p>还没有模型连接。保存后可测试对话、工具调用和结构化输出。</p>}
              {profiles.map(profile => <div className="model-item" key={profile.id}><div className="section-head"><strong>{profile.name}</strong><span className="badge">v{profile.revision}{defaultBinding?.profile_id === profile.id ? ' · 默认' : ''}</span></div>
                <p className="mono">{profile.model_id}</p><p className="url">{profile.base_url}</p><p>思考模式：{thinkingNames[profile.thinking_mode] || '供应商默认'}</p><div className="capabilities">{(['chat', 'tools', 'structured'] as const).map((key, index) =>
                  <span className={profile.capabilities[key]?.status === 'passed' ? 'passed' : ''} key={key}>{['对话', '工具调用', '结构化'][index]} · {profile.capabilities[key]?.status === 'passed' ? '通过' : profile.capabilities[key]?.status === 'failed' ? '失败' : '未验证'}</span>)}</div>
                <p>最大输出 {profile.max_output_tokens || 4096} Token · 超时 {profile.timeout_seconds || 90} 秒{profile.thinking_budget ? ` · 思考上限 ${profile.thinking_budget} Token` : ''}</p>
                {profile.adapter && <p>适配：{profile.adapter.label}</p>}
                {profile.capabilities_stale && <p className="note">适配或调用参数已更新，旧检测结果已失效，请重新测试能力。</p>}
                <div className="buttons"><button disabled={busy} onClick={() => action(async () => { await api(`/model-profiles/${profile.id}/validate`, { method: 'POST' }); setNotice('能力检查完成，请查看各项结果。') })}>测试能力</button>
                  <button disabled={busy} onClick={() => action(async () => { await api('/model-bindings', { method: 'PUT', body: JSON.stringify({ profile_id: profile.id }) }); setNotice('默认模型已切换，新执行将使用此配置。') })}>设为默认</button>
                  <button onClick={() => { setEditing(profile); setModelOptions([]) }}>编辑</button>
                  <button disabled={busy} onClick={() => action(async () => { const result = await api<{ models: string[] }>(`/model-profiles/${profile.id}/models`, { method: 'POST' });
                    setEditing(profile); setModelOptions(result.models); setNotice(`已读取 ${result.models.length} 个模型，也可手动填写。`) })}>读取型号</button></div>
                {Object.entries(profile.capabilities).filter(([, value]) => value.error_type).map(([key, value]) => <p key={key} className="failure">{key}: {value.message || value.error_type}</p>)}
              </div>)}<p className="note">测试会向所填服务发送固定内容，并产生实际 API 调用。运行中的请求保持原配置，新执行使用最新选择。</p>
            </div><div className="panel"><h2>角色模型分配</h2><p>未单独指定时使用全局默认模型。</p>{Object.entries(roleNames).map(([role, name]) => <label className="binding" key={role}><span>{name}</span>
              <select aria-label={`${name}模型`} disabled={busy || !profiles.length} value={bindings.find(b => b.scope === 'global' && b.role === role)?.profile_id || ''}
                onChange={e => { const profile_id = e.target.value || null; action(async () => { await api('/model-bindings', { method: 'PUT', body: JSON.stringify({ profile_id, role }) }) }) }}>
                <option value="">继承默认模型</option>{profiles.map(p => <option value={p.id} key={p.id}>{p.name}</option>)}</select></label>)}</div></section></div>
        </>}
        {page==='models'&&project&&<ModelAssignments project={project} runId={current?.id} profiles={profiles} bindings={bindings} onChange={action} />}
        <footer><span><i className="dot green" /> LOCAL WORKSPACE · 个人工作空间</span><span>版本化计划 / 隔离执行 / 可追溯交付</span></footer>
      </div>
      {busy && <div className="working" role="status">正在处理，请稍候…</div>}
    </main>
    {detailExecution&&project&&<TaskDetail projectId={project.id} executionId={detailExecution} refreshKey={String(live.revision)} onClose={()=>setDetailExecution('')} />}
  </div>
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)
