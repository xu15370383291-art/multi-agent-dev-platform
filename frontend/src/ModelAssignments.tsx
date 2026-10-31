import { useEffect, useState } from 'react'
import { api, type Binding, type Profile, type Project } from './api'

const names:Record<string,string>={supervisor:'项目主管',product:'产品经理',architect:'架构师',web_developer:'Web 开发',game_developer:'游戏开发',qa:'质量测试',reviewer:'代码审查'}
type Resolution={role:string;source:{label:string}|null;next_execution_profile:Profile|null;execution_snapshots:{execution_id:string;status:string;profile:Profile}[]}

export function ModelAssignments({project,runId,profiles,bindings,onChange}:{project:Project;runId?:string;profiles:Profile[];bindings:Binding[];onChange:(work:()=>Promise<void>)=>Promise<void>}){
  const [rows,setRows]=useState<Resolution[]>([])
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  useEffect(()=>{
    let active=true
    setError('');setRows([])
    api<Resolution[]>(`/projects/${project.id}/model-resolution${runId?'?'+new URLSearchParams({run_id:runId}):''}`).then(data=>{if(active)setRows(data)}).catch(error=>{if(active)setError(error.message)})
    return()=>{active=false}
  },[project.id,runId,bindings,profiles])
  const save=async(role:string,value:string)=>{
    setBusy(true)
    try{await onChange(async()=>{await api('/model-bindings',{method:'PUT',body:JSON.stringify({project_id:project.id,role,profile_id:value||null})})})}finally{setBusy(false)}
  }
  return <section className="panel project-models" aria-label="项目模型继承与执行快照"><div className="section-head"><h2>当前项目的模型分配</h2><span className="badge">{project.name}</span></div><p>项目角色覆盖优先，其次项目默认、全局角色和全局默认。已有执行保留固定修订；需要改变正在执行的角色，请使用工作台的暂停后切换入口。</p>
    <label className="binding"><span>项目默认模型</span><select aria-label="项目默认模型" disabled={busy} value={bindings.find(row=>row.scope===project.id&&row.role==='default')?.profile_id||''} onChange={event=>void save('default',event.target.value)}><option value="">继承全局分配</option>{profiles.map(profile=><option key={profile.id} value={profile.id}>{profile.name} · 修订 {profile.revision}</option>)}</select></label>
    {error&&<p role="alert" className="failure">配置来源读取失败：{error}</p>}
    <div className="model-resolution-grid">{Object.entries(names).map(([role,name])=>{const actual=rows.find(row=>row.role===role);return <article className="resolution-row" key={role}><label className="binding"><span>{name}</span><select aria-label={`${name}项目模型`} disabled={busy} value={bindings.find(row=>row.scope===project.id&&row.role===role)?.profile_id||''} onChange={event=>void save(role,event.target.value)}><option value="">继承项目 / 全局分配</option>{profiles.map(profile=><option key={profile.id} value={profile.id}>{profile.name}</option>)}</select></label>{actual&&<><p>下次解析：{actual.next_execution_profile?`${actual.next_execution_profile.name} · ${actual.next_execution_profile.model_id} · 修订 ${actual.next_execution_profile.revision}`:'尚未配置'}<small>来源：{actual.source?.label||'无有效分配'}</small></p>{actual.execution_snapshots.map(snapshot=><p className="fixed-profile" key={snapshot.execution_id}>已保存的执行配置：{snapshot.profile.model_id} · 修订 {snapshot.profile.revision}<br/><code>{snapshot.execution_id}</code></p>)}</>}</article>})}</div>
  </section>
}
