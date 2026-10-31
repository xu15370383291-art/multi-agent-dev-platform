import {useState} from 'react'

export function ProgressStrategyForm({executionId,stateHash,evidenceId,disabled,onSubmit}:{executionId:string;stateHash:string;evidenceId:string;disabled:boolean;onSubmit:(body:unknown)=>void}) {
 const [reason,setReason]=useState(''),[command,setCommand]=useState(''),[confirmed,setConfirmed]=useState(false)
 return <details><summary>核对后登记新策略（高级）</summary><p>先核对上述真实回执，说明为什么下一步与原失败不同。只登记策略，不执行命令；重新核对后才能手动继续。</p>
  <label>新策略依据<textarea value={reason} maxLength={2000} disabled={disabled} onChange={e=>{setReason(e.target.value);setConfirmed(false)}} /></label>
  <label>下一步检查命令<input value={command} maxLength={8000} disabled={disabled} onChange={e=>{setCommand(e.target.value);setConfirmed(false)}} /></label>
  <label><input type="checkbox" checked={confirmed} disabled={disabled} onChange={e=>setConfirmed(e.target.checked)} />已核对执行证据，确认这是一项新的检查策略，保留原失败和全部验收要求</label>
  <button disabled={disabled||reason.trim().length<10||!command.trim()||!confirmed} onClick={()=>onSubmit({execution_id:executionId,expected_state_hash:stateHash,evidence_id:evidenceId,reason:reason.trim(),next_command:command.trim()})}>登记新策略 · 不自动续接</button>
 </details>
}
