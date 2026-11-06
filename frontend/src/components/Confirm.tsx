import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react'
import type { PropsWithChildren, ReactNode } from 'react'
import { Modal } from './Modal'
import { Button } from './ui'
import type { Icon } from './Icon'

interface ConfirmOptions {
  title: string

  body?: ReactNode
  confirmLabel?: string
  cancelLabel?: string

  tone?: 'danger' | 'primary'
  icon?: Parameters<typeof Icon>[0]['name']

  note?: string
}

type Ask = (options: ConfirmOptions) => Promise<boolean>

const ConfirmContext = createContext<Ask | null>(null)

export function ConfirmProvider({ children }: PropsWithChildren) {
  const [options, setOptions] = useState<ConfirmOptions | null>(null)
  const resolver = useRef<((value: boolean) => void) | null>(null)

  const ask = useCallback<Ask>((next) => {
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

export function useConfirm(): Ask {
  const ask = useContext(ConfirmContext)
  if (!ask) throw new Error('useConfirm must be used inside ConfirmProvider')
  return ask
}
