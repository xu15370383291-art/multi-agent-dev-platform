export type Plan = { id: string; version: number; tasks: {
  id: string; task_id: string; role: string; status: string; integration_status: string; integrated_sha: string | null;
  specification: { title?: string; depends_on?: string[]; scope?: string[]; interfaces?: string[]; acceptance_ids?: string[] };
  parent_task_id: string | null; parent_candidate_id: string | null; defect_refs: unknown[];
}[] }

export type Collaboration = {
  status?: string;
  budget?: { model_calls: number; max_model_calls: number|null };
  current_plan_id: string | null; current_plan_version: number | null; waiting_reason: string | null;
  partition: Record<string, string[]>;
  executions: { id: string; task_id: string; plan_task_id: string; attempt: number; role: string; status: string;
    model: { model_id: string; profile_id: string; revision: number; thinking_mode: string } }[];
  integrations: { id: string; execution_id: string; status: string; base_sha: string; target_sha: string | null;
    result: { conflicts?: string[]; resolves_integration_id?: string } }[];
  repair_history: { plan_id: string; version: number; candidate_id?: string; integration_id?: string; task_id: string; reason: string; defects: unknown[] }[];
  coverage: { candidate_id: string; commit_sha: string; qa_report_id: string | null;
    results: { criterion_id: string; name: string; status: string; assertions: number; error?: string }[] }[];
  invocations?: { id: string; role: string; status: string; model: { model_id: string; revision: number } }[];
}

const labels: Record<string, string> = { pending:'待依赖', ready:'已准入', running:'执行中', succeeded:'执行成功',
  failed:'失败', unknown:'结果待核对', blocked:'已阻塞', cancelled:'已取消', merged:'已集成', conflict:'合并冲突',
  integrating:'集成中', awaiting_resolution:'等待冲突解决', prepared:'已准备' }

export function CollaborationPanel({ plans, data, onInspect }: { plans: Plan[]; data: Collaboration | null; onInspect?:(id:string)=>void }) {
  if (!data?.current_plan_id) return null
  const plan = plans.find(p => p.id === data.current_plan_id)
  return <section className="panel collaboration" aria-label="任务协作">
    <div className="section-head"><h2>任务协作</h2><span className="badge">计划 v{data.current_plan_version}</span></div>
    {data.waiting_reason && <p role="status">等待原因：{data.waiting_reason}</p>}
    {data.budget && data.budget.max_model_calls!==null && data.budget.model_calls >= data.budget.max_model_calls && <p role="status">已达到模型调用上限：{data.budget.model_calls}/{data.budget.max_model_calls}。运行不会自动增加额度。</p>}
    <p className="note">就绪：{data.partition.ready?.join('、') || '无'} · 等待：{data.partition.waiting?.join('、') || '无'} · 阻塞：{data.partition.blocked?.join('、') || '无'}。执行成功后仍须集成和当前候选验收。</p>
    {plan?.tasks.map(task => <article className="task-record" key={task.id}>
      <h3>{task.task_id} · {task.specification.title || task.role}</h3>
      <p>角色：{task.role === 'web_developer' ? 'Web 开发' : '游戏开发'}</p>
      <p>执行：{task.status === 'running' && data.status && data.status !== 'running' ? '已暂停，等待核对或继续' : labels[task.status] || task.status} · 集成：{labels[task.integration_status] || task.integration_status}</p>
      <p>依赖：{task.specification.depends_on?.join('、') || '无'} · 验收：{task.specification.acceptance_ids?.join('、') || '见批准文档'}</p>
      <details><summary>范围、接口与关联</summary><pre>{JSON.stringify({scope:task.specification.scope,interfaces:task.specification.interfaces,parent_task_id:task.parent_task_id,parent_candidate_id:task.parent_candidate_id,defects:task.defect_refs},null,2)}</pre></details>
      {data.executions.filter(e => e.plan_task_id === task.id).map(execution => <div key={execution.id}><p>第 {execution.attempt} 次执行 · {labels[execution.status] || execution.status}<br/><code>{execution.model.model_id}</code> · 配置版本 {execution.model.revision} · {execution.model.thinking_mode}</p>{onInspect&&<button className="task-open" onClick={()=>onInspect(execution.id)}>查看 {task.task_id} 第 {execution.attempt} 次执行详情</button>}</div>)}
    </article>)}
    {data.integrations.length > 0 && <details><summary>集成与冲突历史（{data.integrations.length}）</summary>{data.integrations.map(item => <div className="task-record" key={item.id}><p>{labels[item.status] || item.status}</p><code>{item.base_sha} → {item.target_sha || '尚无有效集成版本'}</code>{item.result.conflicts?.length ? <p className="failure">冲突文件：{item.result.conflicts.join('、')}</p> : null}{item.result.resolves_integration_id && <p>已解决集成：{item.result.resolves_integration_id}</p>}</div>)}</details>}
    {data.repair_history.length > 0 && <details><summary>修复历史（{data.repair_history.length}）</summary>{data.repair_history.map(item => <article className="task-record" key={item.plan_id}><h3>计划 v{item.version} · {item.task_id}</h3><p>{item.reason}</p><p>原候选：{item.candidate_id || '集成冲突'}{item.integration_id && ` · ${item.integration_id}`}</p><pre>{JSON.stringify(item.defects,null,2)}</pre></article>)}</details>}
    <details><summary>候选验收覆盖矩阵</summary>{data.coverage.map(item => <article className="task-record" key={item.candidate_id}><p><code>{item.commit_sha}</code></p>{item.results.length ? item.results.map((result,index) => <p key={index}>{result.criterion_id} · {result.name}：{result.status === 'passed' && result.assertions > 0 ? '通过' : '未通过'}（{result.assertions} 断言）{result.error && ` · ${result.error}`}</p>) : <p>尚无浏览器验收证据</p>}</article>)}</details>
    <details><summary>历史计划与执行快照</summary><pre>{JSON.stringify({plans,executions:data.executions},null,2)}</pre></details>
    <details><summary>全部角色模型调用快照</summary>{data.invocations?.map(item => <p key={item.id}>{item.role} · {item.model.model_id} · 配置版本 {item.model.revision} · {item.status === 'succeeded' ? '调用成功（结论见报告）' : labels[item.status] || item.status}</p>)}</details>
  </section>
}
