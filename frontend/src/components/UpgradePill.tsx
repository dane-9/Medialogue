import { Fragment } from 'react'
import { primaryUpgrade, upgradeChanges } from '../lib/primaryUpgrade'
import { Icon } from './Icon'
import { Tooltip } from './Tooltip'

type ComparedFormat = Parameters<typeof upgradeChanges>[0]['currentMatchedFormats'][number]

/** Everything the pill and its card read, the same on Upgrade and Replace Media. */
export interface UpgradeFacts {
  currentQuality?: string
  quality?: string
  upgradeTypes: string[]
  currentMatchedFormats: ComparedFormat[]
  candidateMatchedFormats: ComparedFormat[]
  currentCustomFormatScore: number
  customFormatScore: number
}

/** "1080p → 2160p" with the size-change caret, pointing right, for the arrow. */
function CaretText({ text }: { text: string }) {
  const parts = text.split(' → ')
  return <>{parts.map((part, index) => <Fragment key={index}>{index > 0 && <span className="upgrade-caret" aria-label="to"><Icon name="caret" size={9} /></span>}{part}</Fragment>)}</>
}

/** What the new release changes against the current one, aspect by aspect. */
function UpgradeChangesCard({ facts }: { facts: UpgradeFacts }) {
  const rows = upgradeChanges(facts)
  return <span className="upgrade-diff">
    {rows.length > 0 && <span className="upgrade-diff-rows">
      {rows.map((row, index) => <Fragment key={`${row.label}-${index}`}>
        <span className="upgrade-diff-label">{row.label}</span>
        <span className="upgrade-diff-from">{row.from}</span>
        <span className="upgrade-caret" aria-label="to"><Icon name="caret" size={9} /></span>
        <span className={`upgrade-diff-to ${row.tone}`}>{row.to}</span>
      </Fragment>)}
    </span>}
  </span>
}

/** Formats the new release gains or scores higher on, biggest gain first. */
function gainedFormats(facts: UpgradeFacts) {
  return facts.candidateMatchedFormats.map((format) => {
    const before = facts.currentMatchedFormats.find((other) => other.name === format.name)?.contribution ?? 0
    return { name: format.name, delta: format.contribution - before }
  }).filter((item) => item.delta > 0)
}

/** The UPGRADE pill: the biggest improvement, with every change on hover. */
export function UpgradePill({ facts }: { facts: UpgradeFacts }) {
  const improvement = primaryUpgrade({
    currentQuality: facts.currentQuality,
    quality: facts.quality,
    upgradeTypes: facts.upgradeTypes,
    addedCustomFormats: gainedFormats(facts),
    scoreDelta: facts.customFormatScore - facts.currentCustomFormatScore,
  })
  if (!improvement) return null
  return <Tooltip className="release-pill release-pill-upgrade" placement="below" delay={250} content={<UpgradeChangesCard facts={facts} />}>
    <i>Upgrade</i><CaretText text={improvement.label} />
  </Tooltip>
}
