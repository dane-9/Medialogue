import type { SVGProps } from 'react'

type IconName = 'film' | 'tv' | 'download' | 'alert' | 'search' | 'archive' | 'sliders' | 'settings' | 'menu' | 'activity' | 'play' | 'pause' | 'check' | 'chevron' | 'plus' | 'refresh' | 'logout' | 'external' | 'grid' | 'list' | 'folder' | 'database' | 'server' | 'shield' | 'clock' | 'arrow' | 'close' | 'spark' | 'eye' | 'eye-off'

const paths: Record<IconName, JSX.Element> = {
  film: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M7 4v16M17 4v16M3 9h4M17 9h4M3 15h4M17 15h4" /></>,
  tv: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m8 2 4 3 4-3M8 12h.01M12 12h.01M16 12h.01M8 16h8" /></>,
  download: <><path d="M12 3v12m0 0 4-4m-4 4-4-4" /><path d="M5 20h14" /></>,
  alert: <><path d="m12 3 9 17H3L12 3Z" /><path d="M12 9v4m0 3h.01" /></>,
  search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></>,
  archive: <><path d="M4 7h16v13H4zM3 4h18v3H3zM10 11h4" /></>,
  sliders: <><path d="M4 6h16M4 12h16M4 18h16" /><circle cx="8" cy="6" r="2" /><circle cx="15" cy="12" r="2" /><circle cx="11" cy="18" r="2" /></>,
  settings: <><path d="M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z" /><path d="m4.9 4.9 1.4 1.4m11.4-1.4-1.4 1.4M12 2v2m0 16v2M2 12h2m16 0h2m-4.3 7.1-1.4-1.4M6.3 17.7l-1.4 1.4" /></>,
  menu: <><path d="M4 6h16M4 12h16M4 18h16" /></>,
  activity: <><path d="M3 12h4l2-7 4 14 2-7h6" /></>,
  play: <path d="m8 5 11 7-11 7V5Z" />,
  pause: <><path d="M8 5v14M16 5v14" /></>,
  check: <path d="m5 12 4 4L19 6" />,
  chevron: <path d="m9 18 6-6-6-6" />,
  plus: <><path d="M12 5v14M5 12h14" /></>,
  refresh: <><path d="M20 11a8 8 0 1 0 1 4" /><path d="M20 5v6h-6" /></>,
  logout: <><path d="M10 17l5-5-5-5M15 12H3M21 3v18" /></>,
  external: <><path d="M14 3h7v7M21 3l-9 9" /><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" /></>,
  grid: <><rect x="4" y="4" width="6" height="6" /><rect x="14" y="4" width="6" height="6" /><rect x="4" y="14" width="6" height="6" /><rect x="14" y="14" width="6" height="6" /></>,
  list: <><path d="M9 6h12M9 12h12M9 18h12M4 6h.01M4 12h.01M4 18h.01" /></>,
  folder: <><path d="M3 6a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6Z" /></>,
  database: <><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7" /></>,
  server: <><rect x="4" y="3" width="16" height="7" rx="1" /><rect x="4" y="14" width="16" height="7" rx="1" /><path d="M8 7h.01M8 18h.01" /></>,
  shield: <><path d="M12 3 20 6v6c0 5-3.4 8-8 9-4.6-1-8-4-8-9V6l8-3Z" /><path d="m9 12 2 2 4-4" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  arrow: <><path d="M5 12h14M13 6l6 6-6 6" /></>,
  close: <><path d="m6 6 12 12M18 6 6 18" /></>,
  eye: <><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z" /><circle cx="12" cy="12" r="3" /></>,
  'eye-off': <><path d="M3 3l18 18" /><path d="M10.6 6.2A9.9 9.9 0 0 1 12 5c6.4 0 10 7 10 7a17.6 17.6 0 0 1-3.4 4.3M6.5 7.6C3.9 9.2 2 12 2 12s3.6 7 10 7a9.9 9.9 0 0 0 4.1-.9" /><path d="M9.9 10.1a3 3 0 0 0 4.1 4.2" /></>,
  spark: <><path d="m12 3-1.5 5.5L5 10l5.5 1.5L12 17l1.5-5.5L19 10l-5.5-1.5L12 3Z" /><path d="m19 16-.6 2.4L16 19l2.4.6L19 22l.6-2.4L22 19l-2.4-.6L19 16Z" /></>,
}

export function Icon({ name, size = 18, strokeWidth = 1.8, ...props }: { name: IconName; size?: number; strokeWidth?: number } & SVGProps<SVGSVGElement>) {
  return <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" {...props}>{paths[name]}</svg>
}
