/** Turning a user-chosen colour into a legible dark-theme badge. */

export interface BadgeColor {
  /** Text colour: the chosen hue, lightened if it would be unreadable. */
  text: string
  border: string
  background: string
}

/** Below this relative luminance a colour is too dark to read on the app's background. */
const MIN_LUMINANCE = 0.28

function parseHex(value: string): [number, number, number] | null {
  const match = /^#?([0-9a-f]{6})$/i.exec(value.trim())
  if (!match) return null
  const int = parseInt(match[1], 16)
  return [(int >> 16) & 255, (int >> 8) & 255, int & 255]
}

/** Perceived brightness, 0..1, weighted the way the eye responds to each channel. */
export function luminance(rgb: [number, number, number]): number {
  const [r, g, b] = rgb
  return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
}

/** Mix toward white by `amount` (0..1). */
function lighten(rgb: [number, number, number], amount: number): [number, number, number] {
  return rgb.map((channel) => Math.round(channel + (255 - channel) * amount)) as [number, number, number]
}

const css = (rgb: [number, number, number], alpha?: number) =>
  alpha === undefined ? `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})` : `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha})`

/**
 * Derive a readable badge from one chosen colour.
 *
 * The colour is used as the text, with translucent border and background drawn
 * from the same hue — so a user can pick anything without producing an
 * unreadable badge. A colour too dark to read against the dark theme is
 * lightened toward white until it clears the threshold, which keeps the hue the
 * user chose while making it legible. Returns null for anything unparseable so
 * the caller can fall back to the default badge.
 */
export function badgeColor(value: string | null | undefined): BadgeColor | null {
  if (!value) return null
  const rgb = parseHex(value)
  if (!rgb) return null

  let text = rgb
  if (luminance(text) < MIN_LUMINANCE) {
    // Solve for roughly the mix that reaches the threshold, then guarantee it.
    const start = luminance(rgb)
    const amount = Math.min(1, (MIN_LUMINANCE - start) / Math.max(1 - start, 0.001))
    text = lighten(rgb, amount)
    for (let step = 0; step < 8 && luminance(text) < MIN_LUMINANCE; step += 1) {
      text = lighten(text, 0.15)
    }
  }

  return {
    text: css(text),
    border: css(text, 0.38),
    background: css(text, 0.12),
  }
}
