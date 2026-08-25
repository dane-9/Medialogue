/** The editable fields of a root storage form. */
export interface RootDraft {
  name: string
  path: string
  mediaType: 'movies' | 'shows'
  accessMode: 'read_only' | 'read_write'
  enabled: boolean
  reviewNewMedia: boolean
  missingGraceChecks: number
}

/**
 * Whether a root form differs from what it was opened with.
 *
 * A "has the user touched anything" flag cannot answer this, because opening
 * the form starts a filesystem browse whose result writes the path back into
 * the draft. That write is indistinguishable from a real edit to a flag, so
 * every open looked dirty and every close asked to discard changes. Comparing
 * against the opening snapshot says what actually changed.
 */
export function rootDraftChanged(draft: RootDraft, baseline: RootDraft | null): boolean {
  if (!baseline) return false
  return draft.name.trim() !== baseline.name.trim()
    || draft.path.trim() !== baseline.path.trim()
    || draft.mediaType !== baseline.mediaType
    || draft.accessMode !== baseline.accessMode
    || draft.enabled !== baseline.enabled
    || draft.reviewNewMedia !== baseline.reviewNewMedia
    || draft.missingGraceChecks !== baseline.missingGraceChecks
}

/** Keep supported absolute roots intact when opening the filesystem browser. */
export function rootBrowseStart(path: string): string {
  const start = path.trim()
  return start.startsWith('/') || /^[A-Za-z]:[\\/]/.test(start) || /^\\\\/.test(start) ? start : ''
}

/** Apply a filesystem browse result without manufacturing a form edit. */
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
