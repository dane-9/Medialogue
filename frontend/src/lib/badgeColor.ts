/** Turning a user-chosen colour into a dark-theme badge. */

export interface BadgeColor {
  /** Text colour: exactly the colour that was chosen. */
  text: string
  border: string
  background: string
}

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

const css = (rgb: [number, number, number], alpha?: number) =>
  alpha === undefined ? `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})` : `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha})`

/**
 * Derive a badge from one chosen colour.
 *
 * The colour is used as the text, with translucent border and background drawn
 * from the same hue. It is used as given: a colour someone picked deliberately
 * is theirs, and silently substituting a lighter one makes the badge disagree
 * with the swatch that chose it. Returns null for anything unparseable so the
 * caller can fall back to the default badge.
 */
export function badgeColor(value: string | null | undefined): BadgeColor | null {
  if (!value) return null
  const rgb = parseHex(value)
  if (!rgb) return null

  return {
    text: css(rgb),
    border: css(rgb, 0.38),
    background: css(rgb, 0.12),
  }
}
