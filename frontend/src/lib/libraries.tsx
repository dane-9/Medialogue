import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { PropsWithChildren } from 'react'
import { librariesApi } from '../api/modules/libraries'
import type { Library } from '../types'

interface LibrariesState {
  /** Undefined until the first load finishes. */
  libraries: Library[] | undefined
  reload: () => Promise<Library[] | undefined>
  setLibraries: (libraries: Library[]) => void
  /** The library of the title on screen, so the sidebar can mark it. */
  currentLibraryId: string | undefined
  setCurrentLibraryId: (id: string | undefined) => void
}

const LibrariesContext = createContext<LibrariesState | null>(null)

export function LibrariesProvider({ children }: PropsWithChildren) {
  const [libraries, setLibraries] = useState<Library[] | undefined>(undefined)
  const [currentLibraryId, setCurrentLibraryId] = useState<string | undefined>(undefined)

  const reload = useCallback(async () => {
    try {
      const next = await librariesApi.libraries()
      setLibraries(next)
      return next
    } catch {
      // Keep what is shown; an empty sidebar would hide every library.
      setLibraries((current) => current ?? [])
      return undefined
    }
  }, [])

  useEffect(() => { void reload() }, [reload])

  const value = useMemo(
    () => ({ libraries, reload, setLibraries, currentLibraryId, setCurrentLibraryId }),
    [libraries, reload, currentLibraryId],
  )
  return <LibrariesContext.Provider value={value}>{children}</LibrariesContext.Provider>
}

// Outside the app shell (a page rendered on its own, as in tests) there are no
// libraries to show.
const detached: LibrariesState = {
  libraries: [],
  reload: async () => [],
  setLibraries: () => undefined,
  currentLibraryId: undefined,
  setCurrentLibraryId: () => undefined,
}

export function useLibraries(): LibrariesState {
  return useContext(LibrariesContext) ?? detached
}

/** Mark ``libraryId`` in the sidebar while the calling page is open. */
export function useCurrentLibrary(libraryId: string | undefined) {
  const { setCurrentLibraryId } = useLibraries()
  useEffect(() => {
    setCurrentLibraryId(libraryId)
    return () => setCurrentLibraryId(undefined)
  }, [libraryId, setCurrentLibraryId])
}

export const libraryIcon = (kind: Library['kind']) => kind === 'movies' ? 'clapper' as const : 'tv-screen' as const

export function firstLibraryOf(libraries: Library[] | undefined, kind: Library['kind']) {
  return libraries?.find((library) => library.kind === kind)
}
