

interface BadgeColor {

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

const css = (rgb: [number, number, number], alpha?: number) =>
  alpha === undefined ? `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})` : `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha})`

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
