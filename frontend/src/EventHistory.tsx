import { useEffect, useRef, useState } from 'react'
import { api, ApiError, type Event } from './api'
import { eventTitle, eventExplanation } from './ProjectSections'

type Page = { items: Event[]; next_cursor: string | null }
export function EventHistory({projectId,runId,initial}: {projectId:string;runId?:string;initial:Event[]}) {
  const [rows,setRows]=useState<Event[]>(initial)
  const [cursor,setCursor]=useState<string|null>(null)
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const generation=useRef(0)
  const pending=useRef(false)
  const load=async(older:boolean,version:number)=>{
    if(pending.current)return
    pending.current=true;setBusy(true);setError('')
    const params=new URLSearchParams({page_size:'100'})
    if(runId)params.set('run_id',runId)
    if(older&&cursor)params.set('cursor',cursor)
    try {
      let result:Page|Event[]|undefined
      try{
        const next=await api<Page>(`/projects/${projectId}/events/history?${params}`)
        if(next&&Array.isArray(next.items))result=next
      }catch(error){if(!(error instanceof ApiError&&error.status===404))throw error}
      result=result||await api<Page|Event[]>(`/projects/${projectId}/events?${params}`)
      if(version!==generation.current)return
      const items=(Array.isArray(result)?result:result.items).filter(item=>!runId||item.run_id===runId)
      setRows(previous=>[...new Map((older?[...previous,...items]:items).map(item=>[item.id,item])).values()])
      setCursor(Array.isArray(result)?null:result.next_cursor)
    } catch(e){if(version===generation.current)setError(e instanceof Error?e.message:'历史记录加载失败')}
    finally {if(version===generation.current){pending.current=false;setBusy(false)}}
  }
  useEffect(()=>{
    const version=++generation.current
    pending.current=false;setRows(initial.filter(e=>!runId||e.run_id===runId));setCursor(null)
    void load(false,version)
    return()=>{generation.current++;pending.current=false}
    // History is a cursor snapshot; new events are fetched explicitly without losing reading position.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[projectId,runId])
  return <div className="event-history">
    <button disabled={busy} onClick={()=>void load(false,generation.current)}>{initial.some(event=>(!runId||event.run_id===runId)&&!rows.some(row=>row.id===event.id))?'有新记录 · 查看最新':'刷新最新记录'}</button>
    {error&&<p className="failure" role="alert">{error}</p>}
    {!rows.length&&!busy?<p>尚无执行事件。</p>:<ol className="timeline">{rows.map(event=><li key={event.id}>
      <time>{new Date(event.created_at).toLocaleString('zh-CN')}</time><strong>{eventTitle(event.kind, event.data)}</strong>
      {eventExplanation(event.kind, event.data) && <p>{eventExplanation(event.kind, event.data)}</p>}
      <details><summary>事件详情 · {event.kind}</summary><p>所属运行：{event.run_id||'项目级事件'}</p><pre>{JSON.stringify(event.data,null,2)}</pre></details>
    </li>)}</ol>}
    {cursor&&<button disabled={busy} onClick={()=>void load(true,generation.current)}>加载更早记录</button>}
    {busy&&<p role="status">正在查询持久历史…</p>}
  </div>
}
