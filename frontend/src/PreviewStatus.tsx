import { useState } from 'react'
import { api } from './api'

export function PreviewStatus({deliveryId}:{deliveryId:string}){
  const [busy,setBusy]=useState(false),[message,setMessage]=useState('')
  return <div className="preview-check"><button disabled={busy} onClick={async()=>{setBusy(true);try{const result=await api<{available:boolean;message:string}>(`/deliveries/${deliveryId}/preview-status`);setMessage(result.message)}catch(error){setMessage(error instanceof Error?error.message:'预览连接检查失败')}finally{setBusy(false)}}}>{busy?'正在检查预览…':'检查预览连接'}</button>{message&&<p role="status">{message}</p>}</div>
}
