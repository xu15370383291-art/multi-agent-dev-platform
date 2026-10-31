import {useEffect,useState} from 'react'
import {api} from './api'

type Resources={projects:{project_id:string;bytes:number|null;files?:number;status:string}[];filesystem_free_bytes:number|null;database_bytes:number;retention:string;limits:{tool_output_bytes:number;execution_workspace_mb:number;execution_archive_bytes:number;execution_total_slots:number}}
const size=(value:number|null)=>value===null?'暂时无法读取':value>=1024**3?`${(value/1024**3).toFixed(2)} GB`:`${(value/1024**2).toFixed(1)} MB`
export function OperationsPanel({projects}:{projects:{id:string;name:string}[]}){
 const [data,setData]=useState<Resources|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false)
 const [updated,setUpdated]=useState('')
 async function refresh(signal?:AbortSignal){
  setBusy(true);setError('')
  try{const value=await api<Resources>('/operations/resources',{signal});if(!signal?.aborted){setData(value);setUpdated(new Date().toLocaleTimeString('zh-CN'))}}
  catch(e){if(!signal?.aborted)setError(e instanceof Error?e.message:'读取失败，请检查本机服务')}
  finally{if(!signal?.aborted)setBusy(false)}
 }
 useEffect(()=>{const controller=new AbortController();void refresh(controller.signal);return()=>controller.abort()},[])
 return <section className="operations" aria-labelledby="operations-heading">
  <div className="page-title"><div><div className="eyebrow">本机运行维护</div><h1 id="operations-heading">运行与存储</h1><p>查看空间与保留规则。读取不会启动任务或调用模型。</p></div></div>
  <section className="panel"><div className="section-head"><h2>空间用量</h2><button disabled={busy} onClick={()=>void refresh()}>{busy?'正在读取…':'重新检查用量'}</button></div>
   {error&&<p role="alert" className="message error">无法读取用量：{error}。确认后端服务正常后，点击重新检查。</p>}
   {data&&<><dl className="operations-readings"><div><dt>工作区所在磁盘可用空间</dt><dd>{size(data.filesystem_free_bytes)}</dd></div><div><dt>数据库整体占用</dt><dd>{size(data.database_bytes)}</dd></div><div><dt>同时执行的容器上限</dt><dd>{data.limits.execution_total_slots} 个</dd></div></dl>
    <p className="note">数据库用量包含验收等其他数据空间，不是单个项目的大小。文件统计为读取时的近似值；重新检查才更新。</p>
    <ul className="operations-projects">{data.projects.map(item=><li key={item.project_id}><span>{projects.find(p=>p.id===item.project_id)?.name||'项目 '+item.project_id}<small>{item.status==='measured'?`${item.files} 个文件`:'目录缺失或存在不安全链接，未计算'}</small></span><strong>{size(item.bytes)}</strong></li>)}</ul>
    {!data.projects.length&&<p>尚无项目文件。</p>}<p className="note">检查时间：{updated}</p></>}
  </section>
  <section className="panel operations-policy"><h2>什么会保留，什么可以清理</h2>
   <p><strong>历史与成果一直保留。</strong>源码、审批、执行记录、日志、报告、交付包、数据库和备份都不会自动过期删除。</p>
   <p>只有已结束运行中由平台登记、且没有恢复或交付引用的临时目录，才能列入清理清单。清单需要逐次确认，执行前会重新核验；运行中、待审批、已暂停和结果未知的项目受保护。</p>
   <p className="note">此页面只读，不提供一键删除项目、数据库或全局容器清理。停止预览不影响交付 ZIP。</p>
  </section>
  <section className="panel"><h2>启动与异常处理</h2><ol className="operations-help"><li>先从 Windows 开始菜单启动 Docker Desktop，等待引擎就绪，再使用项目启动入口。</li><li>若出现暂停，进入项目的运行控制查看原因。连接故障、预算耗尽和未知执行结果需要分别处理；不要反复新建运行。</li><li>备份与恢复使用配套维护工具，在停止写入后执行；恢复到新数据库和目录，不覆盖现有项目，也不会自动继续任务。</li><li>Docker 显示空容器或空数据时，先核对原数据盘；不要恢复出厂设置、删除数据库卷或注销 WSL。</li></ol></section>
 </section>
}
