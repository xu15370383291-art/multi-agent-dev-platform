import type { Approval, Doc, Run } from './api'
import { DocumentMarkdown } from './DocumentMarkdown'
import { DocumentComparison } from './DocumentComparison'

type ApprovalRecord = {id:string;document_id:string;status:string;decision:string|null;feedback:string}
export function RunDocuments({run,documents,approvals,history,readOnly,busy,feedback,onFeedback,onDecision}: {
 run?:Run;documents:Doc[];approvals:Approval[];history:ApprovalRecord[];readOnly:boolean;busy:boolean;
 feedback:Record<string,string>;onFeedback:(id:string,value:string)=>void;
 onDecision:(approval:Approval,doc:Doc,decision:'approve'|'request_changes')=>void;
}) {
 const docs=documents.filter(doc=>doc.run_id===run?.id)
 const latest=docs.filter(doc=>!docs.some(other=>other.kind===doc.kind&&other.version>doc.version))
 const older=docs.filter(doc=>!latest.includes(doc))
 const render=(doc:Doc)=>{
  const approval=!readOnly&&approvals.find(a=>a.document_id===doc.id&&a.run_id===run?.id)
  const record=history.find(row=>row.document_id===doc.id)
  const previous=docs.filter(row=>row.kind===doc.kind&&row.version<doc.version).sort((a,b)=>b.version-a.version)[0]
  const isLatest=latest.includes(doc)
  return <article className="panel document" key={doc.id}>
   <div className="section-head"><h2>{doc.kind==='prd'?'产品需求文档':'技术架构'} <span className="count">v{doc.version}</span></h2><span className={'badge'+(approval?' attention':'')}>{approval?'待你审批':!isLatest?'历史版本':record?.decision==='approve'?'已批准':record?.decision==='request_changes'?'已要求修改':'本轮最新版本'}</span></div>
   <p>{doc.kind==='prd'?'用途：确认功能范围和验收标准。':'用途：确认技术方案、模块划分和实现约束。'}</p>
   {record&&!approval&&<p>审批状态：{record.status==='cancelled'?'已取消':record.status==='superseded'?'已被新版本替代':record.decision==='approve'?'已批准':record.decision==='request_changes'?'已要求修改':'尚无决定'}{record.feedback&&<><br/>修改意见：{record.feedback}</>}</p>}
   <details open={!!approval}><summary>{approval?'阅读待审批正文':'查看文档正文'}</summary><DocumentMarkdown>{doc.markdown}</DocumentMarkdown></details>
   {previous&&<DocumentComparison document={doc} previous={previous}/>}
   <details><summary>技术信息 · 版本与结构化内容</summary><pre>{JSON.stringify({hash:doc.content_hash,...doc},null,2)}</pre></details>
   {approval&&isLatest&&<div className="approval"><label>修改意见<textarea aria-label="修改意见" rows={2} value={feedback[approval.id]||''} onChange={e=>onFeedback(approval.id,e.target.value)} placeholder="要求修改时必填"/></label><div className="buttons">{(['approve','request_changes'] as const).map(decision=><button key={decision} className={decision==='approve'?'primary':''} disabled={busy||(decision==='request_changes'&&!feedback[approval.id]?.trim())} onClick={()=>onDecision(approval,doc,decision)}>{decision==='approve'?'批准此版本':'要求修改'}</button>)}</div></div>}
  </article>
 }
 return <div aria-label="所选轮次的文档">
  {!docs.length&&<div className="panel"><p>本轮尚无需求或架构文档。历史文档请切换上方轮次查看。</p></div>}
  {latest.sort((a,b)=>a.kind==='prd'?-1:b.kind==='prd'?1:0).map(render)}
  {!!older.length&&<details className="panel"><summary>本轮的历史版本 · {older.length} 份</summary><p>保留修改和审批记录；开发使用已经批准的对应版本。</p>{older.sort((a,b)=>b.version-a.version).map(render)}</details>}
 </div>
}
