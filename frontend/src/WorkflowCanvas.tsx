import { useEffect, useId, useRef, useState } from 'react'
import type { Approval, Candidate, Delivery, Doc, QualityReport, Run } from './api'
import type { Collaboration, Plan } from './Collaboration'
import { Icon, type IconName } from './Icon'
import type { ProjectSectionId } from './ProjectSections'
import { interruptedQualityLabel } from './qualityStatus'

type GraphNode = { id: string; title: string; code: string; status: string; tone: string; icon: IconName; section: ProjectSectionId; x: number; y: number; dependencies?: string[] }
const stateLabels: Record<string, string> = { pending: '等待依赖', ready: '已准入', prepared: '已准备', running: '执行中', succeeded: '执行成功 · 待集成', failed: '执行失败', unknown: '结果待核对', blocked: '已阻塞', cancelled: '已取消' }

export function WorkflowCanvas({ docs, approvals, plans, collaboration, run, candidates, reports, deliveries, onNavigate }: {
  docs: Doc[]; approvals: Approval[]; plans: Plan[]; collaboration: Collaboration | null; run?: Run;
  candidates: Candidate[]; reports: QualityReport[]; deliveries: Delivery[]; onNavigate: (section: ProjectSectionId) => void;
}) {
  const [zoom, setZoom] = useState(1)
  const [width, setWidth] = useState(900)
  const viewport = useRef<HTMLDivElement>(null)
  const marker = useId().replace(/:/g, '')
  useEffect(() => {
    const element = viewport.current
    if (!element) return
    const observer = new ResizeObserver(entries => { if (entries[0].contentRect.width) setWidth(entries[0].contentRect.width) })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  const currentDocs = docs.filter(doc => doc.run_id === run?.id)
  const latestDoc = (kind: string) => currentDocs.filter(doc => doc.kind === kind).sort((a, b) => b.version - a.version)[0]
  const prd = latestDoc('prd'), architecture = latestDoc('architecture')
  const plan = plans.find(item => item.id === collaboration?.current_plan_id)
  const isPending = (doc?: Doc) => !!doc && approvals.some(item => item.document_id === doc.id)
  const stages: GraphNode[] = [
    { id: 'prd', title: '产品需求', code: 'REQ', icon: 'file', section: 'documents', x: 90, y: 45,
      status: isPending(prd) ? '待审批' : architecture || plan ? '已确认' : prd ? '等待确认结果' : '尚未生成', tone: isPending(prd) ? 'attention' : architecture || plan ? 'success' : 'muted' },
    { id: 'architecture', title: `技术架构${architecture ? ` v${architecture.version}` : ''}`, code: 'ARCH', icon: 'flow', section: 'documents', x: 390, y: 45,
      status: isPending(architecture) ? '待审批' : plan ? '已准入' : architecture ? '等待准入检查' : '尚未生成', tone: isPending(architecture) ? 'attention' : plan ? 'success' : 'muted' },
  ]
  const taskData = plan ? plan.tasks.map(task => ({ id: task.task_id, title: task.specification.title || task.task_id, dependencies: task.specification.depends_on || [], status: task.status, integration: task.integration_status }))
    : (architecture?.payload?.tasks || []).map(task => ({ id: task.id, title: task.title, dependencies: task.depends_on || [], status: 'draft', integration: '' }))
  const byId = new Map(taskData.map(task => [task.id, task]))
  const levels = new Map<string, number>()
  const depth = (id: string, visiting = new Set<string>()): number => {
    if (levels.has(id)) return levels.get(id)!
    if (visiting.has(id)) return 0
    const seen = new Set(visiting).add(id)
    const parents = byId.get(id)?.dependencies.filter(parent => byId.has(parent)) || []
    const level = parents.length ? 1 + Math.max(...parents.map(parent => depth(parent, seen))) : 0
    levels.set(id, level)
    return level
  }
  taskData.forEach(task => depth(task.id))
  const maxDepth = Math.max(0, ...levels.values())
  let cursor = 205
  for (const level of [...new Set(levels.values())].sort((a, b) => a - b)) {
    const row = taskData.filter(task => levels.get(task.id) === level)
    row.forEach((task, index) => {
      const merged = task.integration === 'merged'
      const conflict = ['conflict', 'awaiting_resolution'].includes(task.integration)
      const staleRunning = task.status === 'running' && run?.status !== 'running'
      stages.push({ id: `task:${task.id}`, title: task.title, code: task.id, icon: 'settings', section: 'development',
        status: task.status === 'draft' ? '架构草案 · 尚未派发' : staleRunning ? '执行状态待核对' : conflict ? '等待解决集成冲突' : merged ? '已集成' : stateLabels[task.status] || task.status,
        tone: staleRunning || task.status === 'unknown' ? 'attention' : conflict || ['failed', 'blocked'].includes(task.status) ? 'danger' : merged ? 'success' : task.status === 'running' ? 'active' : 'muted',
        dependencies: task.dependencies, x: 70 + (maxDepth === 0 ? index % 3 : level % 3) * 285, y: 205 + (maxDepth === 0 ? Math.floor(index / 3) : Math.floor(level / 3) * 3 + index) * 145 })
    })
    cursor = Math.max(cursor, ...stages.filter(node => node.id.startsWith('task:')).map(node => node.y + 145))
  }
  if (!taskData.length) {
    stages.push({ id: 'development', title: '开发任务', code: 'DEV', status: '尚无任务计划', tone: 'muted', icon: 'settings', section: 'development', x: 355, y: cursor })
    cursor += 145
  }
  const delivery = deliveries[0]
  const candidateId = delivery?.candidate_id || candidates.at(-1)?.id
  const candidateSha = delivery?.manifest.commit_sha || candidates.at(-1)?.commit_sha
  const report = (role: string) => reports.filter(item => item.candidate_id === candidateId && item.commit_sha === candidateSha && item.role === role).sort((a, b) => (b.attempt || 1) - (a.attempt || 1))[0]
  const qa = report('qa'), reviewer = report('reviewer')
  const qualityStatus = (result?: QualityReport) => {
    const interrupted = interruptedQualityLabel(result, run)
    if (interrupted) return { status: interrupted, tone: 'attention' }
    return { status: result ? ({ passed: '检查通过', failed: '检查未通过', running: '验证中', unknown: '结果待核对' }[result.status] || result.status) : '尚无验收报告', tone: result?.status === 'passed' ? 'success' : result?.status === 'failed' ? 'danger' : result ? 'attention' : 'muted' }
  }
  stages.push(
    { id: 'qa', title: 'QA 浏览器验收', code: 'QA', icon: 'quality', section: 'quality', x: 70, y: cursor + 45, ...qualityStatus(qa) },
    { id: 'reviewer', title: '独立代码审查', code: 'REVIEW', icon: 'file', section: 'quality', x: 355, y: cursor + 45, ...qualityStatus(reviewer) },
    { id: 'delivery', title: '项目交付', code: 'DELIVERY', icon: 'box', section: 'overview', x: 640, y: cursor + 45, status: delivery ? '已交付 · 可预览' : '尚未交付', tone: delivery ? 'success' : 'muted' },
  )
  const edges: [string, string][] = [['prd', 'architecture']]
  if (taskData.length) {
    for (const task of taskData) {
      if (!task.dependencies.length) edges.push(['architecture', `task:${task.id}`])
      for (const parent of task.dependencies) if (byId.has(parent)) edges.push([`task:${parent}`, `task:${task.id}`])
      if (!taskData.some(other => other.dependencies.includes(task.id))) edges.push([`task:${task.id}`, 'qa'])
    }
  } else edges.push(['architecture', 'development'], ['development', 'qa'])
  edges.push(['qa', 'reviewer'], ['reviewer', 'delivery'])
  const height = cursor + 200
  const scale = Math.min(1, width / 900) * zoom
  const open = (node: GraphNode) => {
    onNavigate(node.section)
    if (node.id === 'delivery') requestAnimationFrame(() => document.querySelector('.delivery-card')?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'nearest' }))
  }
  return <section className="workflow-canvas" data-live={run?.status === 'running' ? 'true' : 'false'} aria-label="项目编排画布">
    <div className="canvas-toolbar"><div><Icon name="flow" size={17} /><strong>任务编排</strong><span>{plan ? `计划 v${plan.version}` : '流程与架构草案'}</span></div>
      <div className="canvas-controls"><button aria-label="缩小编排画布" disabled={zoom <= .65} onClick={() => setZoom(Math.max(.65, zoom - .15))}><Icon name="minus" size={15} /></button><output aria-label="画布缩放比例">{Math.round(zoom * 100)}%</output><button aria-label="放大编排画布" disabled={zoom >= 1.6} onClick={() => setZoom(Math.min(1.6, zoom + .15))}><Icon name="plus" size={15} /></button><button aria-label="适应画布" onClick={() => setZoom(1)}><Icon name="fit" size={16} /></button></div>
    </div>
    <p className="canvas-hint">{plan ? '箭头表示上游依赖；执行、集成与验收分别记录。' : '阶段流程示意；架构任务在正式准入前均为草案。'} 点击节点查看对应内容。</p>
    <div className="graph-window" ref={viewport}><div className="graph-size" style={{ width: 900 * scale, height: height * scale }}><div className="graph-stage" style={{ width: 900, height, transform: `scale(${scale})` }}>
      <svg className="graph-wires" width="900" height={height} aria-hidden="true"><defs><marker id={marker} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10z" /></marker></defs>{edges.map(([from, to]) => {
        const a = stages.find(node => node.id === from)!, b = stages.find(node => node.id === to)!
        const horizontal = a.y === b.y || (a.id.startsWith('task:') && b.id.startsWith('task:') && b.x > a.x)
        const x1 = a.x + (horizontal ? 220 : 110), y1 = a.y + (horizontal ? 52 : 104)
        const x2 = b.x + (horizontal ? 0 : 110), y2 = b.y + (horizontal ? 52 : 0)
        const d = horizontal ? `M${x1} ${y1}C${(x1 + x2) / 2} ${y1},${(x1 + x2) / 2} ${y2},${x2} ${y2}` : `M${x1} ${y1}C${x1} ${(y1 + y2) / 2},${x2} ${(y1 + y2) / 2},${x2} ${y2}`
        const live = run?.status === 'running' && b.tone === 'active'
        return <g key={`${from}:${to}`} className={live ? 'wire is-live' : a.tone === 'success' && b.tone === 'success' ? 'wire is-complete' : 'wire'}>
          <path className="wire-route" data-source={from} data-target={to} d={d} markerEnd={`url(#${marker})`} />
          {live && <path className="wire-signal" d={d} pathLength={100} />}
          <circle className="wire-port" cx={x1} cy={y1} r={3} /><circle className="wire-port" cx={x2} cy={y2} r={3} />
        </g>
      })}</svg>
      {stages.map(node => <button className={`graph-node tone-${node.tone}`} key={node.id} data-node={node.id} style={{ left: node.x, top: node.y }} onClick={() => open(node)} title={`查看${node.title}详情`}><span className="node-code">{node.code}</span><span className="node-title"><Icon name={node.icon} size={24} /><strong>{node.title}</strong></span><span className="node-status"><i />{node.status}</span></button>)}
    </div></div></div>
    <div className="graph-mobile" aria-label="移动端任务流程">{stages.map(node => <button key={node.id} className={`mobile-node tone-${node.tone}`} onClick={() => open(node)}><Icon name={node.icon} /><span><strong>{node.title}</strong><small>{node.status}</small>{node.dependencies?.length ? <small>依赖：{node.dependencies.join('、')}</small> : null}</span><Icon name="arrow" size={16} /></button>)}</div>
    <div className="canvas-legend"><span><i className="legend-success" />已确认 / 已集成</span><span><i className="legend-attention" />待处理 / 待核对</span><span><i />尚未执行</span></div>
  </section>
}
