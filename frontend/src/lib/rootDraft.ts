
export interface RootDraft {
  name: string
  path: string
  mediaType: 'movies' | 'shows'
  accessMode: 'read_only' | 'read_write'
  approveNewMedia: boolean
  missingGraceChecks: number
}

export function rootDraftChanged(draft: RootDraft, baseline: RootDraft | null): boolean {
  if (!baseline) return false
  return draft.name.trim() !== baseline.name.trim()
    || draft.path.trim() !== baseline.path.trim()
    || draft.mediaType !== baseline.mediaType
    || draft.accessMode !== baseline.accessMode
    || draft.approveNewMedia !== baseline.approveNewMedia
    || draft.missingGraceChecks !== baseline.missingGraceChecks
}

export function rootBrowseStart(path: string): string {
  const start = path.trim()
  return start.startsWith('/') || /^[A-Za-z]:[\\/]/.test(start) || /^\\\\/.test(start) ? start : ''
}

export function rootPathAfterBrowse(
  currentPath: string,
  browsedPath: string,
  problem: boolean,
  updateDraftPath = true,
  preservePathOnProblem = false,
): string {
  if (!updateDraftPath) return currentPath
  if (problem) return preservePathOnProblem ? currentPath : ''
  return browsedPath
}
