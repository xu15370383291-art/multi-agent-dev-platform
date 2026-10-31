import type { QualityReport, Run } from './api'

export function interruptedQualityLabel(report?: QualityReport, run?: Run): string | null {
  if(report?.status !== 'running' || run?.status === 'running') return null
  const check = report.role === 'qa' ? '验收' : '审查'
  return run?.status === 'cancelled' ? `已取消 · ${check}未完成` : '执行已中断 · 结果待核对'
}
