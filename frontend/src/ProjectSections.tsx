import type { ReactNode } from 'react'

export const projectSections = [
  { id: 'overview', title: '成果概览', description: '查看当前进度、待办事项，以及可预览和下载的交付成果。' },
  { id: 'documents', title: '需求与审批', description: '产品需求说明做什么，技术架构说明怎么做。需要你确认的版本会标注“待你审批”。' },
  { id: 'development', title: '开发协作', description: '查看 Agent 的任务分工、依赖、代码集成和修复过程；这里的任务完成不等于项目已验收。' },
  { id: 'quality', title: '质量验收', description: '构建检查能否打包，QA 验证功能，Reviewer 审查代码。报告只适用于标明的代码版本。' },
  { id: 'activity', title: '执行记录', description: '按时间追溯运行过程，排查失败原因。事件名称和原始数据供需要时核对。' },
] as const

export type ProjectSectionId = typeof projectSections[number]['id']

export function ProjectSection({ id, active, children }: { id: ProjectSectionId; active: ProjectSectionId; children: ReactNode }) {
  const section = projectSections.find(item => item.id === id)!
  return <section hidden={active !== id} aria-label={section.title} className="project-category">
    <header className="category-intro"><h2>{section.title}</h2><p>{section.description}</p></header>
    {children}
  </section>
}

const eventNames: Record<string, string> = {
  'run.recovery_opened': '已打开这一轮的恢复入口，原进度保留',
  'run.stop_confirmed': '已确认停止本轮执行',
  'integration.failed': '代码合并后的构建检查未通过',
  'planning.revision_requested': '已请求修正架构任务信息',
  'planning.revision_checkpoint_reentered': '已重新进入架构生成流程',
  'project.created': '项目已创建', 'run.queued': '运行已排队', 'approval.decided': '审批决定已保存',
  'run.cancel_requested': '已请求取消运行', 'agent.started': 'Agent 开始执行', 'task.finished': '任务执行已结束',
  'model.failed': '模型调用失败', 'candidate.build_started': '开始构建候选版本',
  'candidate.build_finished': '候选版本构建已结束', 'candidate.build_unknown': '构建结果待核对',
  'delivery.published': '交付成果已发布', 'task.worktree_exported': '任务代码已导出',
  'task.baseline': '任务代码基线已记录',
  'integration.merged': '任务代码已集成', 'task.conflict_resolution_seeded': '已准备冲突解决任务',
  'integration.replan_retained_verified_base': '重规划已保留核验过的代码基线',
  'model.schema_correction': '正在纠正模型输出格式', 'document.created': '文档新版本已生成',
  'agent.completed': 'Agent 本步处理已结束', 'approval.required': '等待你审批文档',
  'quality.started': '开始质量验证', 'quality.finished': '质量验证已结束', 'quality.unknown': '质量验证结果待核对',
  'quality.review_rejected': '审查的问题位置需要重新核对',
  'quality.supplement_requested': '已安排对原交付版本追加检查，历史成果保留',
  'quality.supplement_checkpoint_reentered': '从原轮进入追加验收，不重做需求分析',
  'quality.tool_started': '验收工具开始执行', 'quality.tool_finished': '验收工具执行已结束',
  'quality.asset_revision_requested': '已请求修订测试资产',
  'quality.asset_revision_checkpoint_reentered': '已重新进入测试资产验证流程',
  'run.known_development_reconciled': '已核对已知开发结果',
  'run.known_development_checkpoint_reentered': '已重新进入开发检查点',
  'run.recovery_required': '运行需要恢复处理', 'run.stopped': '运行已停止', 'run.paused': '运行已暂停',
  'plan.repair_created': '已创建修复计划', 'task.prepared': '任务环境已准备',
  'tool.started': '开发工具开始执行', 'tool.finished': '开发工具执行已结束',
  'tool.rejected': '工具请求被拒绝', 'tool.unknown': '工具执行结果待核对',
  'supervisor.assessed': '主管已完成评估', 'supervisor.decision_rejected': '主管建议未通过规则检查',
  'supervisor.failure_assessed': '主管已评估执行失败', 'supervisor.planned': '主管已制定计划',
  'supervisor.quality_assessed': '主管已评估质量结果',
  'quality.correction_accepted': '已核对代码和测试问题，开始修复',
  'quality.procedure_prepared': '测试步骤修订草案已保存，等待审查',
  'quality.api_prepared': '测试参数修正已保存，等待审查和复测',
  'quality.plan_rejected': '修复方案未通过审查，原因已反馈给规划',
  'quality.environment_diagnosed': '已确认测试环境缺少中文字体，准备修正后复测',
  'quality.procedure_rejected': '修订草案改动了页面，已拒绝并重新检查',
  'correction.rechecking': '进度已更新，正在核对继续位置',
  'quality.coverage_prepared': '补充测试草案已保存，等待执行',
  'correction.plan_revision_requested': '修复方案还有遗漏，正在补齐并重新检查',
  'correction.review_feedback_requeued': '已保留审查意见，重新安排修复方案',
  'correction.approved_policy_recheck': '已按你确认的规则重新检查修复方案',
  'policy.developer_selfcheck_correction_approved': '已记录新增自检的修正规则',
  'correction.queued': '发现问题，已安排自动处理',
  'diagnosis.started': '发现异常，正在检查出错步骤',
  'diagnosis.updated': '已更新问题原因和处理进度',
  'correction.analyzing': '正在分析出错原因和处理办法',
  'correction.checking': '正在检查能否继续',
  'correction.continuing': '检查通过，继续原任务',
  'correction.needs_attention': '自动处理需要进一步协助',
  'progress.strategy_authorized': '已保存新的处理办法',
  'recovery.check_requested': '已安排继续前的检查',
  'recovery.check_completed': '继续前的检查已结束',
  'recovery.resume_requested': '已安排继续任务',
  'task.continuation_prepared': '已接续保存的工作',
  'run.failure_recorded': '已记录问题和处理建议',
}
export function eventTitle(kind: string, data?: Record<string, unknown>) {
  if (kind === 'tool.rejected') {
    if (data?.operation === 'unsupported_tool') return '开发助手请求了不存在的工具，操作未执行'
    const names: Record<string, string> = { write_files: '写入代码文件', run_command: '运行检查命令', read_file: '读取文件', list_files: '查看项目文件', git: '保存代码版本' }
    const operation = typeof data?.operation === 'string' ? data.operation : ''
    return `${names[operation] || '工具操作'}未执行：请求格式或检查方式不符合要求`
  }
  return eventNames[kind] || '执行事件（展开查看详情）'
}

export function eventExplanation(kind: string, data?: Record<string, unknown>) {
  if (kind === 'quality.review_rejected') return data?.repeated
    ? '审查仍在引用不存在的文件或行号，系统已停止重复提交，原审查和测试结果保留。'
    : '审查引用的文件或行号不正确，系统已保存草稿，并把真实文件范围反馈给审查助手重新核对。'
  if (kind === 'model.failed') {
    const diagnostic = data?.diagnostic as Record<string, unknown> | undefined
    const code = diagnostic?.error_code
    if (code === 'quota_or_rate_limit') return '模型服务限制了请求，但未说明是额度不足还是请求过于频繁。不能据此判断余额用完；已有成果保留。'
    if (code === 'provider_quota_exhausted') return '模型服务报告该模型的可用额度不足。请检查该模型的额度和计费设置。'
    if (code === 'provider_rate_limited') return '模型服务暂时限制了请求速度。稍后检查再继续；这不代表代码出错。'
  }
  if (kind !== 'tool.rejected') return ''
  if (data?.operation === 'unsupported_tool') return '系统已将可用工具及正确调用方式返回给开发助手；没有执行该请求，也不需要你授予新权限。'
  const errors = Array.isArray(data?.errors) ? data.errors : []
  const has = (field: string, type: string) => errors.some(error => error && typeof error === 'object' && Array.isArray(error.loc) && error.loc[0] === field && error.type === type)
  if (data?.operation === 'write_files') {
    if (has('files', 'list_type')) return '开发助手把文件清单填成了文本，系统无法按文件逐项保存，因此未执行。助手需要改用正确的文件清单后重试。'
    if (has('files', 'missing')) return '开发助手没有按要求提供文件清单，因此未写入任何文件。助手需要修正操作格式后重试。'
  }
  if (data?.operation === 'run_command') {
    if (errors.some(error => error?.type === 'value_error' && typeof error.msg === 'string' && error.msg.includes('test_command_masks_failure'))) return '这次检查命令可能掩盖真实的失败结果，因此未执行。助手需要直接运行测试或构建，保留完整结果。'
  }
  return '开发助手提供的操作参数未通过检查，因此没有执行。这是助手的操作用法问题，不是要求你授权，也不代表项目代码已损坏。'
}
