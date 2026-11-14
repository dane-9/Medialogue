import { useEffect, useRef, useState } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { librariesApi } from '../api/modules/libraries'
import { libraryIcon, useLibraries } from '../lib/libraries'
import type { Library } from '../types'
import { useConfirm } from './Confirm'
import { Icon } from './Icon'

function moveItem<T>(items: T[], from: number, to: number) {
  const next = [...items]
  const [item] = next.splice(from, 1)
  next.splice(to, 0, item)
  return next
}

/** The sidebar's libraries, Plex style: drag to reorder, ⋯ for Scan, Edit and Delete. */
export function LibraryNav({ onEdit }: { onEdit: (library: Library) => void }) {
  const { libraries, setLibraries, reload, currentLibraryId } = useLibraries()
  const confirm = useConfirm()
  const location = useLocation()
  const navigate = useNavigate()
  const [order, setOrder] = useState<Library[]>(libraries ?? [])
  const [dragIndex, setDragIndex] = useState<number | null>(null)
  const [menu, setMenu] = useState<{ library: Library; x: number; y: number } | null>(null)
  const [flash, setFlash] = useState<{ id: string; text: string } | null>(null)
  const flashTimer = useRef<number | undefined>(undefined)

  useEffect(() => { if (dragIndex === null) setOrder(libraries ?? []) }, [libraries, dragIndex])
  useEffect(() => () => window.clearTimeout(flashTimer.current), [])

  const say = (id: string, text: string) => {
    window.clearTimeout(flashTimer.current)
    setFlash({ id, text })
    flashTimer.current = window.setTimeout(() => setFlash(null), 4000)
  }

  const tell = (title: string, reason: unknown) => void confirm({
    title,
    body: reason instanceof Error ? reason.message : String(reason),
    confirmLabel: 'OK',
    tone: 'primary',
    icon: 'alert',
  })

  const commitOrder = async () => {
    const wasDragging = dragIndex !== null
    setDragIndex(null)
    if (!wasDragging) return
    try { setLibraries(await librariesApi.reorder(order.map((library) => library.id))) }
    // The sidebar already shows the new order; put the stored one back.
    catch { void reload() }
  }

  const scan = async (library: Library) => {
    try {
      const jobs = await librariesApi.scan(library.id)
      say(library.id, jobs.length ? 'Scanning…' : 'No folders to scan')
    } catch (reason) { tell(`Could not scan ${library.name}`, reason) }
  }

  const remove = async (library: Library) => {
    if (!await confirm({
      title: `Delete ${library.name}?`,
      body: 'The library is removed from the sidebar. It holds no media folders or titles, so nothing else changes.',
      confirmLabel: 'Delete library',
    })) return
    try {
      await librariesApi.remove(library.id)
      const next = await reload()
      if (location.pathname === `/library/${library.id}`) {
        const fallback = next?.find((item) => item.kind === library.kind) ?? next?.[0]
        navigate(fallback ? `/library/${fallback.id}` : '/', { replace: true })
      }
    } catch (reason) { tell(`Could not delete ${library.name}`, reason) }
  }

  const deleteBlock = (library: Library) => {
    if (library.folderCount || library.titleCount) return 'Remove its media folders and titles first'
    if (!order.some((item) => item.id !== library.id && item.kind === library.kind)) return `The only ${library.kind} library`
    return ''
  }

  const openMenu = (library: Library, anchor: HTMLElement) => {
    const rect = anchor.getBoundingClientRect()
    setMenu({ library, x: rect.left, y: rect.bottom + 4 })
  }

  if (!order.length) return null
  return <div className="nav-section library-nav">
    {order.map((library, index) => {
      const onPage = location.pathname === `/library/${library.id}`
      const active = onPage || (!location.pathname.startsWith('/library/') && currentLibraryId === library.id)
      return <div key={library.id}
        className={`library-nav-item ${dragIndex === index ? 'dragging' : ''} ${menu?.library.id === library.id ? 'menu-open' : ''}`}
        draggable
        onDragStart={(event) => { event.dataTransfer.effectAllowed = 'move'; setDragIndex(index) }}
        onDragOver={(event) => {
          event.preventDefault()
          if (dragIndex === null || dragIndex === index) return
          setOrder((items) => moveItem(items, dragIndex, index))
          setDragIndex(index)
        }}
        onDragEnd={() => void commitOrder()}
        onDrop={(event) => { event.preventDefault(); void commitOrder() }}>
        <NavLink to={`/library/${library.id}`} draggable={false} className={`nav-item ${active ? 'active' : ''}`}>
          <Icon name={libraryIcon(library.kind)} size={17} />
          <span className="library-nav-name">{library.name}</span>
          {flash?.id === library.id && <span className="library-nav-flash">{flash.text}</span>}
        </NavLink>
        <button type="button" className="library-nav-more" aria-label={`${library.name} options`}
          onClick={(event) => { event.preventDefault(); event.stopPropagation(); openMenu(library, event.currentTarget) }}>
          <Icon name="more" size={15} />
        </button>
      </div>
    })}
    {menu && <>
      <div className="context-dismiss-layer" onClick={() => setMenu(null)} onContextMenu={(event) => { event.preventDefault(); setMenu(null) }} />
      <LibraryMenu
        position={menu}
        library={menu.library}
        deleteBlock={deleteBlock(menu.library)}
        onScan={() => { setMenu(null); void scan(menu.library) }}
        onEdit={() => { setMenu(null); onEdit(menu.library) }}
        onDelete={() => { setMenu(null); void remove(menu.library) }}
      />
    </>}
  </div>
}

function LibraryMenu({ position, library, deleteBlock, onScan, onEdit, onDelete }: {
  position: { x: number; y: number }
  library: Library
  deleteBlock: string
  onScan: () => void
  onEdit: () => void
  onDelete: () => void
}) {
  const left = Math.max(12, Math.min(position.x, window.innerWidth - 232))
  const top = Math.max(12, Math.min(position.y, window.innerHeight - 140))
  return <div className="bulk-context-menu library-context-menu" role="menu" aria-label={`${library.name} options`} style={{ left, top }}>
    <button type="button" role="menuitem" className="download-menu-item" onClick={onScan} disabled={!library.folderCount}>
      <Icon name="refresh" size={13} />Scan library
    </button>
    <button type="button" role="menuitem" className="download-menu-item" onClick={onEdit}>
      <Icon name="settings" size={13} />Edit library
    </button>
    <button type="button" role="menuitem" className="download-menu-item tone-red" onClick={onDelete} disabled={Boolean(deleteBlock)} title={deleteBlock || undefined}>
      <Icon name="close" size={13} />Delete library
    </button>
    {deleteBlock && <span className="library-menu-hint">{deleteBlock}.</span>}
  </div>
}

/** Right-click menu on the Medialogue titlebar. */
export function TitlebarMenu({ position, onAddLibrary, onClose }: {
  position: { x: number; y: number }
  onAddLibrary: () => void
  onClose: () => void
}) {
  const left = Math.max(12, Math.min(position.x, window.innerWidth - 232))
  const top = Math.max(12, Math.min(position.y, window.innerHeight - 80))
  return <>
    <div className="context-dismiss-layer" onClick={onClose} onContextMenu={(event) => { event.preventDefault(); onClose() }} />
    <div className="bulk-context-menu library-context-menu" role="menu" style={{ left, top }}>
      <button type="button" role="menuitem" className="download-menu-item" onClick={() => { onClose(); onAddLibrary() }}>
        <Icon name="plus" size={13} />Add library
      </button>
    </div>
  </>
}
