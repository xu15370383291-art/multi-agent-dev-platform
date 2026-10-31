import { useState } from 'react'
import type { Run } from './api'

export type UserNotice = {title:string;explanation:string;next_step:string;findings:string[];code:string}

export function PauseNotice({ run, diagnostic, notice, onDetails }: { run: Run; diagnostic?: string; notice?:UserNotice|null; onDetails: () => void }) {
  const [detailsOpen, setDetailsOpen] = useState(false)
  if (run.status !== 'paused') return null
  const reason = run.reason || ''
  let title = '运行已暂停，需要处理后才能继续'
  let explanation = '当前步骤未能完成，已有成果已保存。正在读取具体原因；如果原因暂时无法读取，请稍后刷新。'
  let next = '查看问题详情，查明原因后再继续。'
  if (reason === 'multi_task_scope_and_interfaces_required') {
    title = '架构任务信息不完整，开发尚未开始'
    explanation = '架构被拆分为多个任务，但部分任务没有填写允许修改的文件范围或模块接口。开发前检查未通过，因此暂停。直接重复执行不会补齐这些信息。'
    next = '点击“修正架构并重新提交审批”，补齐任务信息后阅读并批准新版本。保留原审批和已用额度。'
  } else if (reason === 'source_regression_suite_limit_requires_human') {
    title = '新增回归用例超出剩余测试容量'
    explanation = '原用例已保留。新增测试提案需要重新核对容量和必要覆盖，系统不会删除旧用例来继续。'
    next = '核对测试提案与批准需求；仅在规划已修正、执行核对就绪后继续。模型调用剩余额度与测试用例容量分别计算。'
  } else if (/budget|call_limit/.test(reason)) {
    title = '运行预算不足，已停止继续执行'
    explanation = '模型调用次数、活动时长或预留额度触及本轮上限。自动重试仍会消耗预算，系统不会自行提高上限。'
    next = '核对当前模型调用用量和执行记录，评估剩余工作后再决定如何继续。'
  } else if (/unknown|reconcil|recovery_required/.test(reason)) {
    title = '执行结果需要核对，暂时不能重试'
    explanation = '系统还无法确认上一次执行是否完成。为避免重复执行工具或覆盖代码，已暂停后续任务。'
    next = '先使用“核对执行结果”，仅在核对就绪后继续；缺少可信回执时保持暂停。'
  } else if (/capabilities/.test(reason)) {
    title = '模型能力检查未通过'
    explanation = '当前角色配置的模型尚未通过所需能力测试，不能继续执行。'
    next = '到模型配置查看对应模型的能力测试结果；修正配置后仍需使用适用的恢复入口。'
  } else if (diagnostic) {
    title = '模型调用失败，流程已暂停'
    next = '查看下方服务返回的说明，检查模型配置、额度或网络；目前不支持所有失败类型的一键恢复。'
  }
  return <div className="pause-notice" role="status"><h3>{notice?.title||title}</h3><p>{notice?.explanation||explanation}</p>
    {!!notice?.findings.length&&<ul>{notice.findings.map(item=><li key={item}>{item}</li>)}</ul>}
    <p><strong>接下来：</strong>{notice?.next_step||next}</p>
    {!notice&&diagnostic && <p className="failure">服务说明：{diagnostic}</p>}
    <button aria-expanded={detailsOpen} onClick={()=>setDetailsOpen(!detailsOpen)}>查看问题详情</button>
    {detailsOpen&&<section aria-label="暂停原因详情"><h4>为什么停在这里</h4><p>{notice?.explanation||explanation}</p>
      {notice?.findings.length?<ul>{notice.findings.map(item=><li key={item}>{item}</li>)}</ul>:<p>当前尚无更详细的有效诊断；不能据此判断源码或模型额度出了问题。</p>}
      <h4>接下来怎样处理</h4><p>{notice?.next_step||next}</p><button onClick={onDetails}>查看相关执行记录</button></section>}
    <details><summary>技术原因（排查用）</summary><code>{notice?.code||reason || '未提供原因码'}</code></details>
  </div>
}
