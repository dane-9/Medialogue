import type { CustomFormat, CustomFormatScope, CustomFormatSection, CustomFormatTestAllResult, CustomFormatTestResult } from '../../types'
import type { components } from '../generated/schema'
import {
  customFormatPayload, mapCustomFormat, mapCustomFormatEvaluation, mapCustomFormatSection,
  mapMediaProfileSettings, mapQualityDefinition, mapQualityProfile,
} from '../mappers/settings'
import { openApiClient, openApiData } from '../transport'

type CustomFormatLayoutDto = components['schemas']['CustomFormatLayout']
type CustomFormatUpdateDto = components['schemas']['CustomFormatUpdate']
type CustomFormatTestRequestDto = components['schemas']['CustomFormatTestRequest']
type CustomFormatTestAllRequestDto = components['schemas']['CustomFormatTestAllRequest']
type CustomFormatImportBundleDto = components['schemas']['CustomFormatImportBundle']
type QualityProfileCreateDto = components['schemas']['QualityProfileCreate']
type QualityProfileUpdateDto = components['schemas']['QualityProfileUpdate']

export const settingsApi = {
  customFormats: async () => {
    const payload = await openApiData(openApiClient.GET('/api/v1/custom-formats', { params: { query: { page_size: 250 } } }))
    return payload.items.map(mapCustomFormat)
  },
  customFormatLayout: async () => {
    const payload = await openApiData(openApiClient.GET('/api/v1/custom-formats/layout'))
    return payload.sections.map(mapCustomFormatSection)
  },
  saveCustomFormatLayout: async (sections: CustomFormatSection[]) => {
    const body = {
      sections: sections.map((section) => ({ id: section.id, name: section.name, format_ids: section.formatIds })),
    } satisfies CustomFormatLayoutDto
    const payload = await openApiData(openApiClient.PUT('/api/v1/custom-formats/layout', { body }))
    return payload.sections.map(mapCustomFormatSection)
  },
  createCustomFormat: async (format: Pick<CustomFormat, 'name' | 'description' | 'mediaScope' | 'enabled' | 'conditions'>) => {
    const payload = await openApiData(openApiClient.POST('/api/v1/custom-formats', { body: customFormatPayload(format) }))
    return mapCustomFormat(payload)
  },
  updateCustomFormat: async (id: string, format: Pick<CustomFormat, 'name' | 'description' | 'mediaScope' | 'enabled' | 'conditions'> & { expectedRevision?: number }) => {
    const body = { ...customFormatPayload(format), expected_revision: format.expectedRevision } satisfies CustomFormatUpdateDto
    const payload = await openApiData(openApiClient.PATCH('/api/v1/custom-formats/{custom_format_id}', { params: { path: { custom_format_id: id } }, body }))
    return mapCustomFormat(payload)
  },
  setCustomFormatEnabled: async (id: string, enabled: boolean, expectedRevision?: number) => {
    const body = { enabled, expected_revision: expectedRevision } satisfies CustomFormatUpdateDto
    const payload = await openApiData(openApiClient.PATCH('/api/v1/custom-formats/{custom_format_id}', { params: { path: { custom_format_id: id } }, body }))
    return mapCustomFormat(payload)
  },
  deleteCustomFormat: (id: string) => openApiData(openApiClient.DELETE('/api/v1/custom-formats/{custom_format_id}', { params: { path: { custom_format_id: id } } })),
  testCustomFormat: async (format: Pick<CustomFormat, 'id' | 'name' | 'description' | 'mediaScope' | 'enabled' | 'conditions'>, releaseName: string, indexer?: string): Promise<CustomFormatTestResult> => {
    const body = {
      release_name: releaseName,
      indexer,
      custom_format: { id: format.id || undefined, ...customFormatPayload(format) },
    } satisfies CustomFormatTestRequestDto
    const payload = await openApiData(openApiClient.POST('/api/v1/custom-formats/test', { body }))
    return { parsed: payload.parsed, evaluation: mapCustomFormatEvaluation(payload.evaluation) }
  },
  testAllCustomFormats: async (releaseName: string, mediaScope: CustomFormatScope = 'both', indexer?: string, qualityProfileId?: string): Promise<CustomFormatTestAllResult> => {
    const body = {
      release_name: releaseName,
      media_scope: mediaScope,
      indexer,
      quality_profile_id: qualityProfileId || null,
      include_disabled: false,
    } satisfies CustomFormatTestAllRequestDto
    const payload = await openApiData(openApiClient.POST('/api/v1/custom-formats/test-all', { body }))
    return {
      parsed: payload.parsed,
      formats: payload.formats.map(mapCustomFormatEvaluation),
      matchedCount: payload.matched_count,
      qualityProfileId: payload.quality_profile_id ?? undefined,
      qualityProfileName: payload.quality_profile_name ?? undefined,
      totalScore: payload.total_score ?? undefined,
    }
  },
  exportCustomFormats: () => openApiData(openApiClient.GET('/api/v1/custom-formats/export')),
  exportCustomFormat: (id: string) => openApiData(openApiClient.GET('/api/v1/custom-formats/{custom_format_id}/export', { params: { path: { custom_format_id: id } } })),
  importCustomFormats: async (bundle: CustomFormatImportBundleDto) => {
    const payload = await openApiData(openApiClient.POST('/api/v1/custom-formats/import', { body: bundle }))
    return { imported: payload.imported.map(mapCustomFormat), count: payload.count }
  },
  qualityDefinitions: async () => (await openApiData(openApiClient.GET('/api/v1/quality-definitions'))).map(mapQualityDefinition),
  qualityProfiles: async () => (await openApiData(openApiClient.GET('/api/v1/quality-profiles'))).map(mapQualityProfile),
  createQualityProfile: async (payload: QualityProfileCreateDto) => mapQualityProfile(await openApiData(openApiClient.POST('/api/v1/quality-profiles', { body: payload }))),
  updateQualityProfile: async (id: string, payload: QualityProfileUpdateDto) => mapQualityProfile(await openApiData(openApiClient.PATCH('/api/v1/quality-profiles/{profile_id}', { params: { path: { profile_id: id } }, body: payload }))),
  deleteQualityProfile: async (id: string) => { await openApiData(openApiClient.DELETE('/api/v1/quality-profiles/{profile_id}', { params: { path: { profile_id: id } } })) },
  movieProfileSettings: async (resourceId: string) => mapMediaProfileSettings(await openApiData(openApiClient.GET('/api/v1/movies/{resource_id}/profile-settings', { params: { path: { resource_id: resourceId } } }))),
  saveMovieProfileSettings: async (resourceId: string, payload: { quality_profile_id?: string | null; minimum_quality_definition_override_id?: string | null; custom_format_score_overrides?: Record<string, number>; expected_revision?: number }) => mapMediaProfileSettings(await openApiData(openApiClient.PUT('/api/v1/movies/{resource_id}/profile-settings', { params: { path: { resource_id: resourceId } }, body: payload }))),
  showProfileSettings: async (resourceId: string) => mapMediaProfileSettings(await openApiData(openApiClient.GET('/api/v1/shows/{resource_id}/profile-settings', { params: { path: { resource_id: resourceId } } }))),
  saveShowProfileSettings: async (resourceId: string, payload: { quality_profile_id?: string | null; minimum_quality_definition_override_id?: string | null; custom_format_score_overrides?: Record<string, number>; expected_revision?: number }) => mapMediaProfileSettings(await openApiData(openApiClient.PUT('/api/v1/shows/{resource_id}/profile-settings', { params: { path: { resource_id: resourceId } }, body: payload }))),
}
