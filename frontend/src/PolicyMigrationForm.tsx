import {useState} from 'react'

export function PolicyMigrationForm({epoch,calls,limit,disabled,onSubmit}:{epoch:number;calls:number;limit:number|null;disabled:boolean;onSubmit:(body:unknown)=>void}){
 const [reason,setReason]=useState(''),[confirmed,setConfirmed]=useState(false)
 return <details><summary>将此旧运行改为按进展执行</summary><p>保留已用 {calls} 次调用、活动时间和原额度审计记录。取消模型、工具累计次数及累计时长上限，按实际进展执行；保留重复检测、资源隔离和验收要求，不会自动续接。</p>
  <label>调整原因<textarea value={reason} disabled={disabled} maxLength={2000} onChange={e=>{setReason(e.target.value);setConfirmed(false)}} /></label>
  <label><input type="checkbox" checked={confirmed} disabled={disabled} onChange={e=>setConfirmed(e.target.checked)} />确认只调整此运行，保留历史并启用进展保护</label>
  <button disabled={disabled||reason.trim().length<10||!confirmed} onClick={()=>onSubmit({expected_epoch:epoch,expected_model_calls:calls,expected_max_model_calls:limit,reason:reason.trim()})}>登记策略变更 · 不自动续接</button>
 </details>
}
