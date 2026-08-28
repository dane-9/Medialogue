import { authApi } from './modules/auth'
import { discoveryApi } from './modules/discovery'
import { downloadsApi } from './modules/downloads'
import { integrationsApi } from './modules/integrations'
import { moviesApi } from './modules/movies'
import { problemsApi } from './modules/problems'
import { searchApi } from './modules/search'
import { settingsApi } from './modules/settings'
import { showsApi } from './modules/shows'
import { storageApi } from './modules/storage'
import { systemApi } from './modules/system'

export { ApiError } from './transport'

export const api = {
  ...authApi,
  ...systemApi,
  ...moviesApi,
  ...showsApi,
  ...searchApi,
  ...storageApi,
  ...integrationsApi,
  ...settingsApi,
  ...downloadsApi,
  ...discoveryApi,
  ...problemsApi,
} as const
