import type { Event } from './api'
import { eventTitle, eventExplanation } from './ProjectSections'
import { Icon } from './Icon'

export function EventDock({ events, runId, onOpen }: { events: Event[]; runId?: string; onOpen: () => void }) {
  const rows = events.filter(item => item.run_id === runId).slice(0, 3)
  return <details className="event-dock" open><summary><Icon name="activity" size={17} />执行记录 <span>最近 {rows.length} 条</span></summary><div className="dock-content">
    {rows.length ? rows.map(item => <div className="dock-row" key={item.id}><i className={/failed|unknown|paused|rejected/.test(item.kind) ? 'attention' : ''} /><time>{new Date(item.created_at).toLocaleTimeString('zh-CN', { hour12: false })}</time><span>{eventTitle(item.kind, item.data)}{eventExplanation(item.kind, item.data) && <small style={{display:'block',lineHeight:1.7}}>{eventExplanation(item.kind, item.data)}</small>}</span></div>) : <p>所选运行尚无执行事件。开始运行后会在这里记录。</p>}
    <button onClick={onOpen}>查看全部记录 <Icon name="arrow" size={14} /></button>
  </div></details>
}
