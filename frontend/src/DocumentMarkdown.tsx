import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { List, PhrasingContent, Root, Table } from 'mdast'

// Transform the parsed view, never the stored/approved Markdown or its hash.
function tablesAsLists() {
  return (tree: Root) => {
    function walk(parent: { children?: unknown[] }) {
      if (!parent.children) return
      parent.children = parent.children.map(value => {
        const node = value as { type: string; children?: unknown[] }
        if (node.type !== 'table') { walk(node); return node }
        const table = node as Table
        const [header, ...rows] = table.children
        const list: List = {
          type: 'list', ordered: false, spread: false,
          children: rows.map(row => {
            const children: PhrasingContent[] = []
            row.children.forEach((cell, index) => {
              if (index) children.push({ type: 'text', value: index === 1 && row.children.length === 2 ? '：' : '；' })
              if (index > 0 && row.children.length > 2) {
                children.push({ type: 'strong', children: header.children[index]?.children || [{ type: 'text', value: `第 ${index + 1} 列` }] }, { type: 'text', value: '：' })
              }
              if (index === 0) children.push({ type: 'strong', children: cell.children })
              else children.push(...cell.children)
            })
            return { type: 'listItem', spread: false, children: [{ type: 'paragraph', children }] }
          }),
        }
        return list
      })
    }
    walk(tree)
  }
}

export function DocumentMarkdown({ children }: { children: string }) {
  return <div className="markdown"><Markdown remarkPlugins={[remarkGfm, tablesAsLists]}>{children}</Markdown></div>
}
