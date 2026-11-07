// Settings: one file per tab beside this one.
import { Icon } from '../../components/Icon'
import { useUrlState } from '../../lib/urlState'
import { Page } from '../shared'
import { SecuritySettings } from './security'
import { StorageSettings } from './storage'
import { MetadataSettings } from './metadata'
import { PlexSettings } from './plex'
import { QBittorrentSettings } from './qbittorrent'
import { IndexerSettings } from './indexers'

const settingsTabItems: Array<{ name: string; icon: Parameters<typeof Icon>[0]['name'] }> = [
  { name: 'Media', icon: 'folder' },
  { name: 'qBittorrent', icon: 'qbittorrent' },
  { name: 'TMDB', icon: 'tmdb' },
  { name: 'Plex', icon: 'plex' },
  { name: 'Indexers', icon: 'indexers' },
  { name: 'Security', icon: 'security' },
]

const settingsTabs = settingsTabItems.map((item) => item.name)

export function SettingsPage() {
  const [tab, setTab] = useUrlState('tab', 'Media')
  const current = settingsTabs.includes(tab) ? tab : 'Media'
  return <Page title="Settings" subtitle="Connections, library paths, and system administration.">
    <div className="split split-narrow">
      <nav className="settings-nav">{settingsTabItems.map((item) => <button
        className={current === item.name ? 'active' : ''}
        onClick={() => setTab(item.name)}
        key={item.name}
      ><Icon name={item.icon} size={16} />{item.name}</button>)}</nav>
      <section className="panel settings-panel">
        {current === 'Media' ? <StorageSettings />
          : current === 'TMDB' ? <MetadataSettings />
          : current === 'Plex' ? <PlexSettings />
          : current === 'qBittorrent' ? <QBittorrentSettings />
          : current === 'Indexers' ? <IndexerSettings />
          : <SecuritySettings />}
      </section>
    </div>
  </Page>
}
