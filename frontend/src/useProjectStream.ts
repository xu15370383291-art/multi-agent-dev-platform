import { useEffect, useRef, useState } from 'react'

export function useProjectStream(projectId:string, refresh:(id:string)=>Promise<string|null|undefined>) {
  const [connection,setConnection]=useState('等待选择项目')
  const [updatedAt,setUpdatedAt]=useState('')
  const [revision,setRevision]=useState(0)
  const callback=useRef(refresh);callback.current=refresh
  useEffect(()=>{
    if(!projectId){setConnection('等待选择项目');return}
    let active=true,source:EventSource|null=null,cursor:string|null=null,failures=0,busy=false,dirty=false,lastSeen=Date.now()
    let retry:ReturnType<typeof setTimeout>|undefined,debounce:ReturnType<typeof setTimeout>|undefined
    let fallback:ReturnType<typeof setInterval>|undefined
    const seen=new Set<string>()
    const queryMode=()=>{if(!fallback)fallback=setInterval(()=>{void sync().then(next=>{if(active&&next&&!source&&!retry)connect()})},4000)}
    const sync=async():Promise<string|null|undefined>=>{
      if(busy){dirty=true;return null}
      busy=true
      try{
        const next=await callback.current(projectId)
        if(active){setUpdatedAt(new Date().toLocaleTimeString('zh-CN'));setRevision(value=>value+1);if(!cursor&&next)cursor=next}
        return next
      }catch{if(active){setConnection(navigator.onLine?'状态查询暂时不可用，正在重试':'浏览器已离线 · 等待恢复连接');queryMode()}return null}
      finally{busy=false;if(dirty&&active){dirty=false;void sync()}}
    }
    const queueRefresh=()=>{if(!debounce)debounce=setTimeout(()=>{debounce=undefined;void sync()},150)}
    const connect=()=>{
      if(!active||!cursor)return
      source?.close()
      setConnection(failures?'实时连接重连中 · 查询同步可用':'正在连接实时事件')
      const current=new EventSource(`/api/v1/projects/${projectId}/events/stream?${new URLSearchParams({cursor})}`)
      source=current
      lastSeen=Date.now()
      source.addEventListener('connected',()=>{if(!active||source!==current)return;lastSeen=Date.now();failures=0;setConnection('实时事件已连接');if(fallback){clearInterval(fallback);fallback=undefined}})
      source.addEventListener('project_event',event=>{
        if(!active||source!==current)return
        lastSeen=Date.now()
        try{
          const message=event as MessageEvent,body=JSON.parse(message.data)
          if(typeof body.id!=='string'||!message.lastEventId)throw new Error('invalid event')
          cursor=message.lastEventId
          if(!seen.has(body.id)){seen.add(body.id);if(seen.size>1000)seen.delete(seen.values().next().value!);queueRefresh()}
        }catch{reconnect()}
      })
      source.addEventListener('watermark',event=>{if(active&&source===current){lastSeen=Date.now();cursor=(event as MessageEvent).lastEventId||cursor}})
      source.addEventListener('heartbeat',()=>{if(active&&source===current)lastSeen=Date.now()})
      source.addEventListener('stream_problem',()=>{if(source===current)reconnect()})
      source.onerror=()=>{if(source===current)reconnect()}
    }
    const reconnect=()=>{
      if(!active)return
      source?.close();source=null
      if(retry)return
      failures++;setConnection('实时连接重连中 · 查询同步可用');queryMode()
      retry=setTimeout(async()=>{
        retry=undefined
        try{
          const response=await fetch(`/api/v1/projects/${projectId}/events/changes?${new URLSearchParams(cursor?{cursor}:{})}`)
          if(response.status===409){cursor=null;await sync()}
        }catch{/* periodic state query remains active */}
        if(active)connect()
      },Math.min(15000,1000*2**Math.min(failures,4))*(0.85+Math.random()*0.3))
    }
    const offline=()=>{reconnect();setConnection('浏览器已离线 · 等待恢复连接')}
    const foreground=()=>{if(document.visibilityState==='visible'){queueRefresh();if(!source&&cursor&&!retry)connect()}}
    setConnection('正在读取项目状态');setUpdatedAt('')
    void sync().then(next=>{if(!active)return;if(next)connect();else{setConnection('查询同步模式');queryMode()}})
    const liveness=setInterval(()=>{if(active&&source&&Date.now()-lastSeen>40000)reconnect()},5000)
    document.addEventListener('visibilitychange',foreground)
    window.addEventListener('online',foreground)
    window.addEventListener('offline',offline)
    return()=>{active=false;clearInterval(liveness);source?.close();clearTimeout(retry);clearTimeout(debounce);clearInterval(fallback);document.removeEventListener('visibilitychange',foreground);window.removeEventListener('online',foreground);window.removeEventListener('offline',offline)}
  },[projectId])
  return {connection,updatedAt,revision}
}
