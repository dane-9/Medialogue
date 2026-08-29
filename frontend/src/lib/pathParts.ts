/** Splits a path into the thing it names and the folder that holds it.
 *
 * Every row that showed a raw path was unreadable at full width and buried the
 * one segment that identifies it, so the leaf leads and the parent follows.
 * Both separators are handled: media folders are routinely UNC or Windows
 * paths, and matching only "/" silently returned the whole path as one segment.
 */
export function pathParts(value?: string): { name: string; parent?: string } | undefined {
  if (!value) return undefined
  const segments = value.split(/[\\/]/).filter(Boolean)
  const name = segments.pop()
  if (!name) return { name: value }
  const parent = value.slice(0, value.length - name.length).replace(/[\\/]+$/, '')
  return { name, parent: parent || undefined }
}
