export type IconName = 'project' | 'file' | 'flow' | 'quality' | 'activity' | 'settings' | 'box' | 'arrow' | 'plus' | 'fit' | 'minus'

const paths: Record<IconName, string> = {
  project: 'M3 6h6l2 2h10v12H3z M3 6V4h6l2 2',
  file: 'M6 3h8l4 4v14H6z M14 3v5h4 M9 12h6 M9 16h6',
  flow: 'M12 7v4 M5 16v-5h14v5 M9 3h6v4H9z M2 16h6v5H2z M16 16h6v5h-6z',
  quality: 'M12 3l8 3v6c0 5-8 9-8 9s-8-4-8-9V6z M8 12l3 3 5-6',
  activity: 'M4 5h16v15H4z M7 2v5 M17 2v5 M7 12h3l2-3 2 6 2-3h2',
  settings: 'M9 3h6l1 4 4 2v6l-4 2-1 4H9l-1-4-4-2V9l4-2z M9 12a3 3 0 1 0 6 0 3 3 0 1 0-6 0',
  box: 'M3 7l9-5 9 5-9 5z M3 7v10l9 5 9-5V7 M12 12v10',
  arrow: 'M4 12h16 M14 6l6 6-6 6',
  plus: 'M12 4v16 M4 12h16',
  minus: 'M4 12h16',
  fit: 'M8 3H3v5 M16 3h5v5 M3 16v5h5 M16 21h5v-5 M8 8h8v8H8z',
}

export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>
}
