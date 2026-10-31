import type { Doc } from './api'

export function documentChanges(oldText:string,newText:string){
  const old=oldText.split('\n'),next=newText.split('\n')
  if(old.length*next.length>250000)return null
  const table=Array.from({length:old.length+1},()=>new Uint32Array(next.length+1))
  for(let i=old.length-1;i>=0;i--)for(let j=next.length-1;j>=0;j--)table[i][j]=old[i]===next[j]?table[i+1][j+1]+1:Math.max(table[i+1][j],table[i][j+1])
  const changes:{kind:'same'|'added'|'removed';line:string}[]=[]
  let i=0,j=0
  while(i<old.length||j<next.length){
    if(i<old.length&&j<next.length&&old[i]===next[j]){changes.push({kind:'same',line:old[i++]});j++}
    else if(j<next.length&&(i===old.length||table[i][j+1]>=table[i+1][j]))changes.push({kind:'added',line:next[j++]})
    else changes.push({kind:'removed',line:old[i++]})
  }
  return changes
}

export function DocumentComparison({document,previous}:{document:Doc;previous:Doc}){
  const changes=documentChanges(previous.markdown,document.markdown)
  return <details className="document-comparison"><summary>与 v{previous.version} 比较 · 新增与移除内容</summary><p>同一运行、同类文档：v{previous.version} → v{document.version}。修改后的版本需要独立审批。</p>{changes?<ol className="document-diff" aria-label="文档版本差异">{changes.map((row,index)=><li className={row.kind} key={index}><span aria-label={row.kind==='added'?'新增':row.kind==='removed'?'移除':'保留'}>{row.kind==='added'?'+':row.kind==='removed'?'−':' '}</span><code>{row.line||' '}</code></li>)}</ol>:<><p>文档较长，显示两版全文供对照。</p><h3>v{previous.version}</h3><pre>{previous.markdown}</pre><h3>v{document.version}</h3><pre>{document.markdown}</pre></>}</details>
}
