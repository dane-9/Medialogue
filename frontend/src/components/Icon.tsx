import type { SVGProps } from 'react'

type IconName = 'download' | 'alert' | 'search' | 'archive' | 'sliders' | 'settings' | 'menu' | 'activity' | 'play' | 'pause' | 'check' | 'chevron' | 'plus' | 'refresh' | 'logout' | 'external' | 'grid' | 'list' | 'folder' | 'database' | 'server' | 'clock' | 'arrow' | 'close' | 'spark' | 'eye' | 'eye-off' | 'copy' | 'clapper' | 'tv-screen' | 'caret' | 'quality-profile' | 'custom-format' | 'new-media' | 'info' | 'lock' | 'unlock' | 'blocklist' | 'plex' | 'tmdb' | 'qbittorrent' | 'backup' | 'security' | 'indexers' | 'upgrade-media' | 'chevrons-up' | 'chevrons-down' | 'stop' | 'transfer' | 'play-solid' | 'checking' | 'shuffle' | 'moving' | 'tracker' | 'queue-top' | 'queue-bottom' | 'queue-up' | 'queue-down' | 'share-ratio' | 'tag' | 'category' | 'monitored' | 'monitored-off' | 'radio-selected' | 'open-in'

const paths: Record<IconName, JSX.Element> = {
  // Queue moves: the full mark (outline chevron above a solid arrow) means
  // top/bottom, the solid arrow alone means one step up/down. The down variants
  // are the same artwork turned around.
  'queue-top': <g fill="currentColor" stroke="none">
    <path fillRule="evenodd" clipRule="evenodd" d="M4.43 13.488a.75.75 0 0 0 1.058.081L12 7.988l6.512 5.581a.75.75 0 1 0 .976-1.138l-7-6a.75.75 0 0 0-.976 0l-7 6a.75.75 0 0 0-.081 1.057" />
    <path d="M5 17.75a.75.75 0 0 1-.488-1.32l7-6a.75.75 0 0 1 .976 0l7 6A.75.75 0 0 1 19 17.75z" />
  </g>,
  'queue-bottom': <g fill="currentColor" stroke="none" transform="rotate(180 12 12)">
    <path fillRule="evenodd" clipRule="evenodd" d="M4.43 13.488a.75.75 0 0 0 1.058.081L12 7.988l6.512 5.581a.75.75 0 1 0 .976-1.138l-7-6a.75.75 0 0 0-.976 0l-7 6a.75.75 0 0 0-.081 1.057" />
    <path d="M5 17.75a.75.75 0 0 1-.488-1.32l7-6a.75.75 0 0 1 .976 0l7 6A.75.75 0 0 1 19 17.75z" />
  </g>,
  'queue-up': <path fill="currentColor" stroke="none" d="M5 17.75a.75.75 0 0 1-.488-1.32l7-6a.75.75 0 0 1 .976 0l7 6A.75.75 0 0 1 19 17.75z" />,
  'queue-down': <path fill="currentColor" stroke="none" transform="rotate(180 12 12)" d="M5 17.75a.75.75 0 0 1-.488-1.32l7-6a.75.75 0 0 1 .976 0l7 6A.75.75 0 0 1 19 17.75z" />,
  // The mask id is fixed: every instance draws the same artwork, so a repeated
  // id resolves to an identical mask rather than the wrong one.
  'share-ratio': <>
    <defs>
      <mask id="medialogue-share-ratio">
        <g fill="none">
          <rect width="36" height="36" x="6" y="6" fill="#fff" stroke="#fff" strokeLinejoin="round" strokeWidth="4" rx="3" />
          <path fill="#000" fillRule="evenodd" clipRule="evenodd" d="M24 22.5a2.5 2.5 0 1 0 0-5a2.5 2.5 0 0 0 0 5m0 8a2.5 2.5 0 1 0 0-5a2.5 2.5 0 0 0 0 5" />
          <path stroke="#000" strokeLinecap="round" strokeLinejoin="round" strokeWidth="4" d="M15.5 17v14m17-14v14" />
        </g>
      </mask>
    </defs>
    <path fill="currentColor" stroke="none" d="M0 0h48v48H0z" mask="url(#medialogue-share-ratio)" />
  </>,
  // Mirrored horizontally, so the point faces the other way: scale(-1 1) flips
  // about x=0, and the translate brings the 24-wide artwork back into view.
  tag: <path
    fill="currentColor"
    stroke="none"
    transform="translate(24 0) scale(-1 1)"
    d="M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8 8a2 2 0 0 0 2.828 0l7.172-7.172a2 2 0 0 0 0-2.828zM7 9a2 2 0 1 1 .001-4.001A2 2 0 0 1 7 9"
  />,
  category: <path
    fill="currentColor"
    stroke="none"
    d="M4 11h6a1 1 0 0 0 1-1V4a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1m10 0h6a1 1 0 0 0 1-1V4a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1M4 21h6a1 1 0 0 0 1-1v-6a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1m13 0c2.206 0 4-1.794 4-4s-1.794-4-4-4s-4 1.794-4 4s1.794 4 4 4"
  />,
  tracker: <g>
    <path d="M12 13.5a1.5 1.5 0 1 0 0-3a1.5 1.5 0 0 0 0 3" />
    <path d="M12 6a6 6 0 1 0 4.243 1.757" />
    <path d="M12 2C6.477 2 2 6.477 2 12s4.477 10 10 10s10-4.477 10-10a9.97 9.97 0 0 0-2.929-7.071L12 12" />
  </g>,
  'chevrons-up': <path d="M18 11.5s-4.419-6-6-6s-6 6-6 6m12 7s-4.419-6-6-6s-6 6-6 6" />,
  // Same artwork as chevrons-up, turned to point the other way.
  'chevrons-down': <path transform="rotate(180 12 12)" d="M18 11.5s-4.419-6-6-6s-6 6-6 6m12 7s-4.419-6-6-6s-6 6-6 6" />,
  stop: <path fill="currentColor" stroke="none" d="M24 6H8a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2" />,
  // Split into two paths so each arrow carries its own colour: the upload arrow
  // (pointing up) blue, the download arrow (pointing down) green.
  transfer: <>
    <path fill="var(--accent-text)" stroke="none" fillRule="evenodd" clipRule="evenodd" d="M6.793 3.007a1 1 0 0 1 1.414 0l3.5 3.5a1 1 0 0 1-1.414 1.415L8.5 6.129v9.585a1 1 0 1 1-2 0V6.13L4.707 7.922a1 1 0 1 1-1.414-1.415z" />
    <path fill="var(--ok)" stroke="none" fillRule="evenodd" clipRule="evenodd" d="M16.5 8.714a1 1 0 0 1 1 1V19.3l1.793-1.793a1 1 0 0 1 1.414 1.415l-3.5 3.5a1 1 0 0 1-1.414 0l-3.5-3.5a1 1 0 0 1 1.414-1.415L15.5 19.3V9.714a1 1 0 0 1 1-1" />
  </>,
  'play-solid': <path fill="currentColor" stroke="none" d="M3 2.803a1 1 0 0 1 1.5-.865l9 5.195a1 1 0 0 1 0 1.733l-9 5.196a1 1 0 0 1-1.5-.866z" />,
  checking: <path fill="currentColor" stroke="none" d="M21.074 12.154a.75.75 0 0 1 .672.82c-.49 4.93-4.658 8.776-9.724 8.776c-2.724 0-5.364-.933-7.238-2.68L3 20.85a.75.75 0 0 1-.75-.75v-3.96c0-.714.58-1.29 1.291-1.29h3.97a.75.75 0 0 1 .75.75l-2.413 2.407c1.558 1.433 3.78 2.243 6.174 2.243c4.29 0 7.817-3.258 8.232-7.424a.75.75 0 0 1 .82-.672m-18.82-1.128c.49-4.93 4.658-8.776 9.724-8.776c2.724 0 5.364.933 7.238 2.68L21 3.15a.75.75 0 0 1 .75.75v3.96c0 .714-.58 1.29-1.291 1.29h-3.97a.75.75 0 0 1-.75-.75l2.413-2.408c-1.558-1.432-3.78-2.242-6.174-2.242c-4.29 0-7.817 3.258-8.232 7.424a.75.75 0 1 1-1.492-.148" />,
  shuffle: <path fill="currentColor" stroke="none" d="M3 7.25a.75.75 0 0 0-.75.75a.75.75 0 0 0 .75.75c2.139 0 3.386.46 4.277 1.107c.336.245.626.524.891.83c.248-.397.521-.835.853-1.289a6.6 6.6 0 0 0-.86-.755C6.988 7.79 5.36 7.25 3 7.25m15.25 5.248a.75.75 0 0 0-.531.22a.75.75 0 0 0 0 1.063l1.468 1.469H17c-2.139 0-3.386-.46-4.277-1.107a5.3 5.3 0 0 1-.891-.83c-.248.397-.521.835-.853 1.289c.257.265.538.521.86.755c1.172.852 2.8 1.393 5.161 1.393h2.188l-1.47 1.469a.75.75 0 0 0 0 1.062a.75.75 0 0 0 1.063 0l2.75-2.75a.75.75 0 0 0 .194-.468L21.75 16a1 1 0 0 0-.025-.129v-.064l-.02-.034a.75.75 0 0 0-.174-.304l-2.75-2.75a.75.75 0 0 0-.531-.221m0-7.998a.75.75 0 0 0-.531.219a.75.75 0 0 0 0 1.062l1.468 1.469H17c-2.361 0-3.989.54-5.16 1.393c-1.171.851-1.852 1.959-2.477 2.959s-1.194 1.892-2.086 2.54S5.14 15.25 3 15.25a.75.75 0 0 0-.75.75a.75.75 0 0 0 .75.75c2.361 0 3.989-.54 5.16-1.393c1.171-.851 1.852-1.959 2.477-2.959s1.194-1.892 2.086-2.54S14.86 8.75 17 8.75h2.188l-1.47 1.469a.75.75 0 0 0 0 1.062a.75.75 0 0 0 1.063 0l2.75-2.75a.75.75 0 0 0 .194-.469L21.75 8a1 1 0 0 0-.025-.129a1 1 0 0 0-.02-.098a.75.75 0 0 0-.174-.304l-2.75-2.75a.75.75 0 0 0-.531-.219" />,
  moving: <path fill="currentColor" stroke="none" d="M1.635 29.658h4.674c.806 10.684 9.372 19.273 20.033 20.079v4.605c0 .921.714 1.658 1.635 1.658c.944 0 1.658-.737 1.658-1.658v-4.605c10.661-.806 19.227-9.395 20.033-20.08h4.674A1.65 1.65 0 0 0 56 28c0-.921-.737-1.635-1.658-1.635h-4.674c-.806-10.684-9.372-19.273-20.033-20.079V1.658C29.635.737 28.92 0 27.977 0c-.921 0-1.635.737-1.635 1.658v4.628c-10.661.806-19.227 9.395-20.033 20.08H1.635C.714 26.365.1 27.078.1 28s.614 1.658 1.535 1.658m26.342-10.753c.944 0 1.658-.737 1.658-1.658V10.04c8.658.782 15.381 7.575 16.142 16.325h-7.024c-.92 0-1.658.714-1.658 1.635s.737 1.658 1.658 1.658h7.024c-.76 8.727-7.484 15.52-16.142 16.303v-7.208c0-.92-.714-1.635-1.658-1.635c-.921 0-1.635.714-1.635 1.635v7.208c-8.658-.783-15.381-7.576-16.141-16.303h7.023c.92 0 1.658-.737 1.658-1.658s-.737-1.635-1.658-1.635H10.2c.76-8.75 7.483-15.543 16.141-16.325v7.207c0 .92.714 1.658 1.635 1.658" />,
  'upgrade-media': <>
    <path
      fill="currentColor"
      stroke="none"
      d="M3.5 13h9a.75.75 0 0 1 .102 1.493l-.102.007h-9a.75.75 0 0 1-.102-1.493zh9z"
    />
    <path
      fill="currentColor"
      stroke="none"
      transform="matrix(1 0 0 -1 0 12.78)"
      d="M7.898 1.007L8 1a.75.75 0 0 1 .743.648l.007.102v7.688l2.255-2.254a.75.75 0 0 1 .977-.072l.084.072a.75.75 0 0 1 .072.977l-.072.084L8.53 11.78a.75.75 0 0 1-.976.073l-.084-.073l-3.536-3.535a.75.75 0 0 1 .977-1.133l.084.072L7.25 9.44V1.75a.75.75 0 0 1 .648-.743L8 1z"
    />
  </>,
  download: <path
    fill="currentColor"
    stroke="none"
    d="M3.5 13h9a.75.75 0 0 1 .102 1.493l-.102.007h-9a.75.75 0 0 1-.102-1.493zh9zM7.898 1.007L8 1a.75.75 0 0 1 .743.648l.007.102v7.688l2.255-2.254a.75.75 0 0 1 .977-.072l.084.072a.75.75 0 0 1 .072.977l-.072.084L8.53 11.78a.75.75 0 0 1-.976.073l-.084-.073l-3.536-3.535a.75.75 0 0 1 .977-1.133l.084.072L7.25 9.44V1.75a.75.75 0 0 1 .648-.743L8 1z"
  />,
  alert: <path
    fill="currentColor"
    stroke="none"
    fillRule="evenodd"
    d="m21.268 21.053l-18.536.001a1 1 0 0 1-.866-1.5L11.132 3.5a1 1 0 0 1 1.732 0l9.27 16.053a1 1 0 0 1-.866 1.5M11.248 9.545l.116 5.666h1.272l.117-5.666zm.75 8.572c.48 0 .855-.369.855-.832s-.375-.826-.856-.826a.83.83 0 0 0-.85.826c0 .463.375.832.85.832z"
  />,
  lock: <path
    fill="currentColor"
    stroke="none"
    d="M12 2C9.243 2 7 4.243 7 7v3H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-1V7c0-2.757-2.243-5-5-5M9 7c0-1.654 1.346-3 3-3s3 1.346 3 3v3H9zm4 10.723V20h-2v-2.277a1.993 1.993 0 0 1 .567-3.677A2 2 0 0 1 14 16a1.99 1.99 0 0 1-1 1.723"
  />,
  // Prohibited sign. Drawn on its own 16px grid with its own stroke width,
  // so it keeps the supplied proportions at every size.
  blocklist: <g fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5">
    <circle cx="8" cy="8" r="6.25" />
    <line x1="4.25" x2="12.25" y1="11.75" y2="3.75" />
  </g>,
  unlock: <path
    fill="currentColor"
    stroke="none"
    d="M18 10H9V7c0-1.654 1.346-3 3-3s3 1.346 3 3h2c0-2.757-2.243-5-5-5S7 4.243 7 7v3H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2m-7.939 5.499A2.002 2.002 0 0 1 14 16a1.99 1.99 0 0 1-1 1.723V20h-2v-2.277a1.99 1.99 0 0 1-.939-2.224"
  />,
  backup: <path
    fill="currentColor"
    stroke="none"
    d="M7.44 4a2.25 2.25 0 0 0-2.025 1.27L2.73 10.821c.465-.206.98-.321 1.521-.321h15.5a3.7 3.7 0 0 1 1.52.321L18.586 5.27A2.25 2.25 0 0 0 16.559 4zm12.31 8A2.25 2.25 0 0 1 22 14.25v2.5A2.25 2.25 0 0 1 19.75 19H4.25A2.25 2.25 0 0 1 2 16.75v-2.5A2.25 2.25 0 0 1 4.25 12zm-1.25 4.5a1 1 0 1 0 0-2a1 1 0 0 0 0 2"
  />,
  security: <path
    fill="currentColor"
    stroke="none"
    d="M27.988 51.297c.375 0 .961-.14 1.57-.445C42.66 43.469 47.185 40.375 47.185 31.96V14.289c0-2.414-1.055-3.187-3-4.008c-2.743-1.125-11.532-4.289-14.25-5.25c-.633-.187-1.266-.328-1.946-.328c-.656 0-1.289.14-1.922.328c-2.718.985-11.507 4.149-14.25 5.25c-1.945.797-3 1.594-3 4.008v17.672c0 8.414 4.547 11.484 17.625 18.89c.61.305 1.172.446 1.547.446"
  />,
  indexers: <path
    fill="currentColor"
    stroke="none"
    d="M3 19q-.425 0-.712-.288T2 18t.288-.712T3 17h8q.425 0 .713.288T12 18t-.288.713T11 19zm0-5q-.425 0-.712-.288T2 13t.288-.712T3 12h3q.425 0 .713.288T7 13t-.288.713T6 14zm0-5q-.425 0-.712-.288T2 8t.288-.712T3 7h3q.425 0 .713.288T7 8t-.288.713T6 9zm11 7q-2.075 0-3.537-1.463T9 11t1.463-3.537T14 6t3.538 1.463T19 11q0 .725-.213 1.438t-.637 1.312l3.15 3.15q.275.275.275.7t-.275.7t-.7.275t-.7-.275l-3.15-3.15q-.6.425-1.312.638T14 16m0-2q1.25 0 2.125-.875T17 11t-.875-2.125T14 8t-2.125.875T11 11t.875 2.125T14 14"
  />,
  plex: <path
    fill="currentColor"
    stroke="none"
    d="M4 2c-1.11 0-2 .89-2 2v16c0 1.11.89 2 2 2h16c1.11 0 2-.89 2-2V4c0-1.11-.89-2-2-2zm4.56 4h3.5l3.44 6l-3.44 6h-3.5L12 12z"
  />,
  tmdb: <>
    <defs>
      <mask id="medialogue-tmdb-cutout" maskUnits="userSpaceOnUse" x="0" y="0" width="400" height="220">
        <rect x="0" y="0" width="400" height="220" rx="24" ry="24" fill="#fff" />
        <text
          x="200"
          y="146"
          fill="#000"
          stroke="none"
          fontSize="105"
          fontWeight="700"
          letterSpacing="2"
          textAnchor="middle"
          fontFamily="'Helvetica Neue', Arial, sans-serif"
        >TMDB</text>
      </mask>
    </defs>
    <rect x="0" y="0" width="400" height="220" rx="24" ry="24" fill="currentColor" stroke="none" mask="url(#medialogue-tmdb-cutout)" />
  </>,
  qbittorrent: <path
    fill="currentColor"
    stroke="none"
    d="M352.8 187.6c-17.5 0-30.1 5.1-37.8 15.1s-11.5 26.8-11.5 50.6 4 40.8 11.9 51 20.6 15.2 38.1 15.2c15.8 0 27.5-5.7 35.2-17.1 7.7-11.5 11.5-28 11.5-49.5 0-22-3.8-38.4-11.5-49.2-7.8-10.7-19.8-16.1-35.9-16.1m-194.4-.3c-15 0-26.6 5.9-34.6 17.6-7.9 11.6-11.9 28.1-11.9 49.4s3.9 37.5 11.8 48.8c7.9 11 19.5 16.6 35 16.7 17.1 0 29.5-4.6 37.3-13.8s12-24.6 12.5-46.3V254q0-35.55-12-51c-8-10.5-20.8-15.7-38.1-15.7m333.5-30.9C479 125.9 460.5 98.5 437 75s-50.9-42-81.4-54.9C324.1 6.8 290.6 0 256 0s-68.1 6.8-99.6 20.1C125.9 33 98.5 51.5 75 75s-42 50.9-54.9 81.4C6.8 187.9 0 221.4 0 256s6.8 68.1 20.1 99.6C33 386.1 51.5 413.5 75 437s50.9 42 81.4 54.9C188 505.3 221.5 512 256 512s68.1-6.8 99.6-20.1c30.5-12.9 57.9-31.4 81.4-54.9s42-50.9 54.9-81.4C505.3 324 512 290.5 512 256s-6.8-68.1-20.1-99.6M234.2 414h-25.6v-72.3c0-10.3.5-19 1.7-26.2h-2c-11.9 17.2-29.7 25.8-53.5 25.8-21.8 0-38.8-7.6-51-23q-18.3-22.95-18.3-64.2c0-27.7 6.1-49.2 18.5-64.8 12.3-15.5 29.5-23.3 51.5-23.3 23.1 0 41 8.8 53.4 26.2h1.4L214 169h20.2zM408 318.2c-12.4 15.3-29.5 23-51.5 23-11 0-21.1-2-30.2-6-9-4.1-16.7-10.4-22.9-18.8h-1.9l-5.4 21.8h-18.4V98h25.6v58.3c0 13-.4 24.8-1.2 35.2h1.2c11.9-16.9 29.6-25.3 53.1-25.3 22.2 0 39.5 7.6 51.7 22.8 12.4 15.1 18.5 36.5 18.5 64.3.1 27.8-6.1 49.5-18.6 64.9"
  />,
  info: <path
    fill="currentColor"
    stroke="none"
    d="M512 64C264.6 64 64 264.6 64 512s200.6 448 448 448s448-200.6 448-448S759.4 64 512 64m32 664c0 4.4-3.6 8-8 8h-48c-4.4 0-8-3.6-8-8V456c0-4.4 3.6-8 8-8h48c4.4 0 8 3.6 8 8zm-32-344a48.01 48.01 0 0 1 0-96a48.01 48.01 0 0 1 0 96"
  />,
  search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></>,
  archive: <path
    fill="currentColor"
    stroke="none"
    d="M7.22 19.385q-.585 0-1.037-.383q-.454-.382-.554-.965l-1.287-7.702q-.063-.38.172-.665t.622-.286h13.728q.388 0 .622.286q.235.286.172.664l-1.287 7.702q-.1.584-.553.966q-.454.383-1.037.383zm2.78-5.5h4q.213 0 .356-.144q.144-.144.144-.357t-.144-.356t-.356-.144h-4q-.213 0-.356.145t-.144.356t.144.356t.356.143M6 8q-.213 0-.356-.144T5.5 7.499t.144-.356T6 7h12q.213 0 .356.144t.144.357t-.144.356T18 8zm2-2.384q-.213 0-.356-.144T7.5 5.115t.144-.356q.143-.144.356-.144h8q.213 0 .356.144t.144.357t-.144.356t-.356.144z"
  />,
  sliders: <><path d="M4 6h16M4 12h16M4 18h16" /><circle cx="8" cy="6" r="2" /><circle cx="15" cy="12" r="2" /><circle cx="11" cy="18" r="2" /></>,
  settings: <path
    fill="currentColor"
    stroke="none"
    d="M256 176a80 80 0 1 0 80 80a80.24 80.24 0 0 0-80-80m172.72 80a165.5 165.5 0 0 1-1.64 22.34l48.69 38.12a11.59 11.59 0 0 1 2.63 14.78l-46.06 79.52a11.64 11.64 0 0 1-14.14 4.93l-57.25-23a176.6 176.6 0 0 1-38.82 22.67l-8.56 60.78a11.93 11.93 0 0 1-11.51 9.86h-92.12a12 12 0 0 1-11.51-9.53l-8.56-60.78A169.3 169.3 0 0 1 151.05 393L93.8 416a11.64 11.64 0 0 1-14.14-4.92L33.6 331.57a11.59 11.59 0 0 1 2.63-14.78l48.69-38.12A175 175 0 0 1 83.28 256a165.5 165.5 0 0 1 1.64-22.34l-48.69-38.12a11.59 11.59 0 0 1-2.63-14.78l46.06-79.52a11.64 11.64 0 0 1 14.14-4.93l57.25 23a176.6 176.6 0 0 1 38.82-22.67l8.56-60.78A11.93 11.93 0 0 1 209.94 26h92.12a12 12 0 0 1 11.51 9.53l8.56 60.78A169.3 169.3 0 0 1 361 119l57.2-23a11.64 11.64 0 0 1 14.14 4.92l46.06 79.52a11.59 11.59 0 0 1-2.63 14.78l-48.69 38.12a175 175 0 0 1 1.64 22.66"
  />,
  menu: <><path d="M4 6h16M4 12h16M4 18h16" /></>,
  activity: <><path d="M3 12h4l2-7 4 14 2-7h6" /></>,
  play: <path d="m8 5 11 7-11 7V5Z" />,
  pause: <><path d="M8 5v14M16 5v14" /></>,
  check: <path d="m5 12 4 4L19 6" />,
  'new-media': <path
    fill="currentColor"
    stroke="none"
    fillRule="evenodd"
    d="M5 11.5a.5.5 0 0 1 .5-.5h9a.5.5 0 0 1 0 1h-9a.5.5 0 0 1-.5-.5m0-4a.5.5 0 0 1 .5-.5h9a.5.5 0 0 1 0 1h-9a.5.5 0 0 1-.5-.5m0-4a.5.5 0 0 1 .5-.5h9a.5.5 0 0 1 0 1h-9a.5.5 0 0 1-.5-.5M3.854 2.146a.5.5 0 0 1 0 .708l-1.5 1.5a.5.5 0 0 1-.708 0l-.5-.5a.5.5 0 1 1 .708-.708L2 3.293l1.146-1.147a.5.5 0 0 1 .708 0m0 4a.5.5 0 0 1 0 .708l-1.5 1.5a.5.5 0 0 1-.708 0l-.5-.5a.5.5 0 1 1 .708-.708L2 7.293l1.146-1.147a.5.5 0 0 1 .708 0m0 4a.5.5 0 0 1 0 .708l-1.5 1.5a.5.5 0 0 1-.708 0l-.5-.5a.5.5 0 0 1 .708-.708l.146.147l1.146-1.147a.5.5 0 0 1 .708 0"
  />,
  chevron: <path d="m9 18 6-6-6-6" />,
  plus: <path d="M12 17V7m-5 5h10" />,
  refresh: <>
    <path fill="none" stroke="currentColor" strokeLinecap="round" strokeMiterlimit={10} strokeWidth={32} d="m400 148l-21.12-24.57A191.43 191.43 0 0 0 240 64C134 64 48 150 48 256s86 192 192 192a192.09 192.09 0 0 0 181.07-128" />
    <path fill="currentColor" stroke="none" d="M464 97.42V208a16 16 0 0 1-16 16H337.42c-14.26 0-21.4-17.23-11.32-27.31L436.69 86.1C446.77 76 464 83.16 464 97.42" />
  </>,
  logout: <><path d="M10 17l5-5-5-5M15 12H3M21 3v18" /></>,
  external: <><path d="M14 3h7v7M21 3l-9 9" /><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" /></>,
  grid: <><rect x="4" y="4" width="6" height="6" /><rect x="14" y="4" width="6" height="6" /><rect x="4" y="14" width="6" height="6" /><rect x="14" y="14" width="6" height="6" /></>,
  list: <><path d="M9 6h12M9 12h12M9 18h12M4 6h.01M4 12h.01M4 18h.01" /></>,
  folder: <path
    fill="currentColor"
    stroke="none"
    d="M880 298.4H521L403.7 186.2a8.15 8.15 0 0 0-5.5-2.2H144c-17.7 0-32 14.3-32 32v592c0 17.7 14.3 32 32 32h736c17.7 0 32-14.3 32-32V330.4c0-17.7-14.3-32-32-32"
  />,
  database: <><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7" /></>,
  server: <><rect x="4" y="3" width="16" height="7" rx="1" /><rect x="4" y="14" width="16" height="7" rx="1" /><path d="M8 7h.01M8 18h.01" /></>,
  clock: <path
    fill="currentColor"
    stroke="none"
    d="M16 8A8 8 0 1 1 0 8a8 8 0 0 1 16 0M8 3.5a.5.5 0 0 0-1 0V9a.5.5 0 0 0 .252.434l3.5 2a.5.5 0 0 0 .496-.868L8 8.71z"
  />,
  arrow: <><path d="M5 12h14M13 6l6 6-6 6" /></>,
  close: <><path d="m6 6 12 12M18 6 6 18" /></>,
  eye: <path
    fill="currentColor"
    stroke="none"
    d="M12 19c7.63 0 9.93-6.62 9.95-6.68c.07-.21.07-.43 0-.63c-.02-.07-2.32-6.68-9.95-6.68s-9.93 6.61-9.95 6.67c-.07.21-.07.43 0 .63c.02.07 2.32 6.68 9.95 6.68Zm0-10c1.64 0 3 1.36 3 3s-1.36 3-3 3s-3-1.36-3-3s1.36-3 3-3"
  />,
  'eye-off': <path
    fill="currentColor"
    stroke="none"
    d="M2.05 11.68c-.07.21-.07.43 0 .63c.02.07 2.32 6.68 9.95 6.68c.91 0 1.73-.1 2.49-.26L4.13 8.37c-1.49 1.65-2.06 3.28-2.08 3.31m19.9.64c.07-.21.07-.43 0-.63c-.02-.07-2.32-6.68-9.95-6.68c-1.84 0-3.36.39-4.61.97L2.71 1.29L1.3 2.7l4.32 4.32l3.69 3.69l3.98 3.98l3.33 3.33l4.68 4.68l1.41-1.41l-4.32-4.32c2.61-1.95 3.55-4.61 3.56-4.65M12 9c1.64 0 3 1.36 3 3c0 .46-.11.89-.3 1.29l-3.98-3.98c.39-.19.83-.3 1.29-.3Z"
  />,
  // A camera watching the title. The mask id is fixed: every instance draws the
  // same artwork, so a repeated id resolves to an identical mask.
  monitored: <path
    fill="currentColor"
    stroke="none"
    d="M18.618 7.462L6.403 2.085a1 1 0 0 0-.77-.016a1 1 0 0 0-.552.537l-3 7a1 1 0 0 0 .525 1.313L9.563 13.9L8.323 17H4v-3H2v8h2v-3h4.323c.823 0 1.552-.494 1.856-1.258l1.222-3.054l3.419 1.465a1 1 0 0 0 1.311-.518l3-6.857a1 1 0 0 0-.513-1.316m1.312 8.91l-1.858-.742l1.998-5l1.858.741z"
  />,
  // The slash is cut out of the camera by the mask, then drawn over it, so the
  // stroke keeps a clean gap on both sides whatever colour it inherits.
  'monitored-off': <>
    <mask id="medialogue-monitored-off">
      <rect width="24" height="24" fill="#fff" />
      <path d="M3 3L19 19" stroke="#000" strokeWidth="4.5" strokeLinecap="round" />
    </mask>
    <g mask="url(#medialogue-monitored-off)">
      <path
        fill="currentColor"
        stroke="none"
        d="M18.618 7.462L6.403 2.085a1 1 0 0 0-.77-.016a1 1 0 0 0-.552.537l-3 7a1 1 0 0 0 .525 1.313L9.563 13.9L8.323 17H4v-3H2v8h2v-3h4.323c.823 0 1.552-.494 1.856-1.258l1.222-3.054l3.419 1.465a1 1 0 0 0 1.311-.518l3-6.857a1 1 0 0 0-.513-1.316m1.312 8.91l-1.858-.742l1.998-5l1.858.741z"
      />
    </g>
    <path d="M3 3L19 19" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
  </>,
  copy: <>
    <path d="M18.327 7.286h-8.044a1.93 1.93 0 0 0-1.925 1.938v10.088c0 1.07.862 1.938 1.925 1.938h8.044a1.93 1.93 0 0 0 1.925-1.938V9.224c0-1.07-.862-1.938-1.925-1.938" />
    <path d="M15.642 7.286V4.688c0-.514-.203-1.007-.564-1.37a1.92 1.92 0 0 0-1.361-.568H5.673c-.51 0-1 .204-1.36.568a1.95 1.95 0 0 0-.565 1.37v10.088c0 .514.203 1.007.564 1.37s.85.568 1.361.568h2.685" />
  </>,
  clapper: <path
    fill="currentColor"
    stroke="none"
    d="M216 104H102.09L210 75.51a8 8 0 0 0 5.68-9.84l-8.16-30a15.93 15.93 0 0 0-19.42-11.13L35.81 64.74a15.75 15.75 0 0 0-9.7 7.4a15.5 15.5 0 0 0-1.55 12L32 111.56V200a16 16 0 0 0 16 16h160a16 16 0 0 0 16-16v-88a8 8 0 0 0-8-8m-23.84-64l6 22.07L164.57 71l-28.13-16.28ZM77.55 70.27l28.12 16.24l-59.6 15.73l-6-22.08Z"
  />,
  'tv-screen': <>
    <path fill="currentColor" stroke="none" d="M488 384H24a8 8 0 0 1-8-8V88a8 8 0 0 1 8-8h464a8 8 0 0 1 8 8v288a8 8 0 0 1-8 8" />
    <rect width="288" height="32" x="112" y="400" rx="4" ry="4" fill="currentColor" stroke="none" />
  </>,
  // An arrow going into a box: opens a further window.
  'open-in': <g fill="currentColor" stroke="none">
    <path d="m16 12l-6-5v4H3v2h7v4z" />
    <path d="M19 3h-7v2h7v14h-7v2h7c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2" />
  </g>,
  // A selected radio button: ring and centre dot.
  'radio-selected': <>
    <path fill="currentColor" stroke="none" d="M12 5c-3.859 0-7 3.141-7 7s3.141 7 7 7s7-3.141 7-7s-3.141-7-7-7m0 12c-2.757 0-5-2.243-5-5s2.243-5 5-5s5 2.243 5 5s-2.243 5-5 5" />
    <path fill="currentColor" stroke="none" d="M12 9c-1.627 0-3 1.373-3 3s1.373 3 3 3s3-1.373 3-3s-1.373-3-3-3" />
  </>,
  caret: <path
    fill="currentColor"
    stroke="none"
    fillRule="evenodd"
    clipRule="evenodd"
    d="M7 9a1 1 0 0 0-.707 1.707l5 5a1 1 0 0 0 1.414 0l5-5A1 1 0 0 0 17 9z"
  />,
  'quality-profile': <>
    <defs>
      <mask id="medialogue-qp-cutout">
        <rect x="0" y="0" width="400" height="300" rx="24" ry="24" fill="#fff" />
        <text
          x="200"
          y="216"
          fill="#000"
          stroke="none"
          fontSize="190"
          fontWeight="700"
          letterSpacing="6"
          textAnchor="middle"
          fontFamily="'Segoe UI Variable Display', 'Segoe UI', 'Helvetica Neue', Arial, sans-serif"
        >QP</text>
      </mask>
    </defs>
    <rect x="0" y="0" width="400" height="300" rx="24" ry="24" fill="currentColor" stroke="none" mask="url(#medialogue-qp-cutout)" />
  </>,
  'custom-format': <path
    fill="currentColor"
    stroke="none"
    d="M5.835 19.956q-.652.086-1.162-.317q-.51-.404-.615-1.056L2.87 10.177q-.067-.416.205-.758t.688-.41h.448v5.318q0 1.342.944 2.286t2.286.945H18.98q-.085.332-.344.56t-.608.284zm2.396-4.76q-.69 0-1.153-.462t-.462-1.153V5.616q0-.691.462-1.153T8.231 4h11.154q.69 0 1.153.463T21 5.616v7.965q0 .69-.462 1.153t-1.153.462zM10.308 8.5h7v-1h-7zm0 3.154h4v-1h-4z"
  />,
  spark: <><path d="m12 3-1.5 5.5L5 10l5.5 1.5L12 17l1.5-5.5L19 10l-5.5-1.5L12 3Z" /><path d="m19 16-.6 2.4L16 19l2.4.6L19 22l.6-2.4L22 19l-2.4-.6L19 16Z" /></>,
}

const VIEW_BOXES: Partial<Record<IconName, string>> = {
  security: '-3.48 -3.48 62.96 62.96',
  indexers: '-1.44 -0.73 26.45 26.45',
  download: '-1.12 -1.37 18.24 18.24',
  'upgrade-media': '-1.12 -1.37 18.24 18.24',
  alert: '-1.88 -1.85 27.75 27.75',
  lock: '-1.51 -1.51 27.03 27.03',
  unlock: '-1.51 -1.51 27.03 27.03',
  blocklist: '0 0 16 16',
  backup: '-1.51 -2.01 27.03 27.03',
  plex: '-1.51 -1.51 27.03 27.03',
  tmdb: '-70.27 -160.27 540.54 540.54',
  qbittorrent: '-89.95 -89.95 691.89 691.89',
  info: '-93.41 -93.41 1210.81 1210.81',
  search: '0.97 0.97 22.07 22.07',
  archive: '1.63 1.63 20.73 20.73',
  sliders: '0.97 0.97 22.07 22.07',
  settings: '-54.81 -54.81 621.62 621.62',
  menu: '0.97 0.97 22.07 22.07',
  activity: '-0.41 -0.41 24.83 24.83',
  play: '3.84 2.34 19.31 19.31',
  pause: '2.34 2.34 19.31 19.31',
  check: '2.34 1.34 19.31 19.31',
  'new-media': '-1.46 -2.21 18.92 18.92',
  plus: '5.1 5.1 13.79 13.79',
  refresh: '-25.08 -25.08 562.16 562.16',
  logout: '-0.41 -0.41 24.83 24.83',
  external: '-0.41 -0.41 24.83 24.83',
  grid: '0.97 0.97 22.07 22.07',
  list: '0.78 0.28 23.45 23.45',
  folder: '-28.54 -28.54 1081.08 1081.08',
  database: '-1.79 -1.79 27.59 27.59',
  server: '-0.41 -0.41 24.83 24.83',
  clock: '-2.81 -2.81 21.62 21.62',
  arrow: '2.34 2.34 19.31 19.31',
  close: '3.72 3.72 16.55 16.55',
  copy: '-0.76 -0.76 25.52 25.52',
  clapper: '-11.15 -15.14 270.29 270.29',
  'tv-screen': '-68.32 -68.32 648.65 648.65',
  'quality-profile': '-70.27 -120.27 540.54 540.54',
  'custom-format': '-0.33 -0.27 24.52 24.52',
  spark: '0.4 -0.6 26.21 26.21',
  stop: '0 0 32 32',
  transfer: '0 0 24 25',
  'play-solid': '0 0 16 16',
  moving: '0 0 56 56',
  'share-ratio': '0 0 48 48',
}

const STROKE_SCALE: Partial<Record<IconName, number>> = {
  plus: 0.575,
  search: 0.92,
  sliders: 0.92,
  menu: 0.92,
  activity: 1.034,
  play: 0.805,
  pause: 0.805,
  check: 0.805,
  logout: 1.034,
  external: 1.034,
  grid: 0.92,
  list: 0.977,
  database: 1.149,
  server: 1.034,
  arrow: 0.805,
  close: 0.69,
  copy: 1.063,
  spark: 1.092,
  'chevrons-up': 0.833,
  'chevrons-down': 0.833,
  tracker: 0.833,
}

export function Icon({ name, size = 18, strokeWidth = 1.8, ...props }: { name: IconName; size?: number; strokeWidth?: number } & SVGProps<SVGSVGElement>) {
  return <svg aria-hidden="true" width={size} height={size} viewBox={VIEW_BOXES[name] ?? '0 0 24 24'} fill="none" stroke="currentColor" strokeWidth={strokeWidth * (STROKE_SCALE[name] ?? 1)} strokeLinecap="round" strokeLinejoin="round" {...props}>{paths[name]}</svg>
}
