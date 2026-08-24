import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react'
import type { PropsWithChildren, ReactNode } from 'react'
import { Modal } from './Modal'
import { Button } from './ui'
import type { Icon } from './Icon'

export interface ConfirmOptions {
  title: string
  /** The consequence, in a sentence or two. Optional for the obvious cases. */
  body?: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  /** danger is the default: nearly everything asking for confirmation destroys something. */
  tone?: 'danger' | 'primary'
  icon?: Parameters<typeof Icon>[0]['name']
  /** Shown quietly in the footer, for a reassurance that is not the question. */
  note?: string
}

type Ask = (options: ConfirmOptions) => Promise<boolean>

const ConfirmContext = createContext<Ask | null>(null)

/**
 * Application-wide confirmation dialogs.
 *
 * A promise-returning `confirm` keeps call sites as short as the browser's own
 * `window.confirm`, so asking properly costs no more than asking badly:
 *
 *     if (!await confirm({ title: 'Delete this?' })) return
 *
 * The native dialog cannot be styled, cannot show markup, blocks the whole
 * page, and looks like the browser rather than the app.
 */
export function ConfirmProvider({ children }: PropsWithChildren) {
  const [options, setOptions] = useState<ConfirmOptions | null>(null)
  const resolver = useRef<((value: boolean) => void) | null>(null)

  const ask = useCallback<Ask>((next) => {
    // A second request while one is open answers the first as cancelled,
    // rather than leaving its caller waiting forever.
    resolver.current?.(false)
    setOptions(next)
    return new Promise<boolean>((resolve) => { resolver.current = resolve })
  }, [])

  const settle = useCallback((value: boolean) => {
    resolver.current?.(value)
    resolver.current = null
    setOptions(null)
  }, [])

  const value = useMemo(() => ask, [ask])
  return <ConfirmContext.Provider value={value}>
    {children}
    {options && <Modal
      icon={options.icon}
      title={options.title}
      onClose={() => settle(false)}
      footer={<>
        {options.note ? <span className="footer-state clean">{options.note}</span> : <span className="footer-state clean" />}
        <Button variant="ghost" onClick={() => settle(false)}>{options.cancelLabel ?? 'Cancel'}</Button>
        <Button variant={options.tone ?? 'danger'} onClick={() => settle(true)}>{options.confirmLabel ?? 'Confirm'}</Button>
      </>}
    >
      {options.body ? <div className="confirm-body">{options.body}</div> : null}
    </Modal>}
  </ConfirmContext.Provider>
}

/** Ask the user to confirm. Resolves false if they cancel or dismiss. */
export function useConfirm(): Ask {
  const ask = useContext(ConfirmContext)
  if (!ask) throw new Error('useConfirm must be used inside ConfirmProvider')
  return ask
}
