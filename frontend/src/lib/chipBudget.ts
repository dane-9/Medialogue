/**
 * How many chips fit on a card before the rest become "+n more".
 *
 * A flat item count is the wrong measure: nine short names like "HDR" and "DV"
 * sit on two lines, while nine long ones like "Bravia Core Remux Tier"
 * overflow the card. Budgeting by total characters lets short lists show more
 * of themselves without letting long ones take over the layout.
 */
export function fitChips(labels: readonly string[], maxItems = 9, maxChars = 96): number {
  if (!labels.length) return 0

  let used = 0
  for (let index = 0; index < labels.length && index < maxItems; index += 1) {
    // Every chip carries its own padding and score, so a name is never free.
    used += labels[index].length + 5
    // Always show at least one, however long its name: an empty row of chips
    // followed by "+4 more" says less than one example plus "+3 more".
    if (used > maxChars && index > 0) return index
  }
  return Math.min(labels.length, maxItems)
}
