import type {
  CustomFormat, CustomFormatCondition, CustomFormatConditionEvaluation, CustomFormatEvaluation,
  CustomFormatSection, MediaProfileSettings, QualityDefinition, QualityProfile,
} from '../../types'
import type { components } from '../generated/schema'

type CustomFormatConditionDto = components['schemas']['CustomFormatConditionResponse']
type CustomFormatDto = components['schemas']['CustomFormatResponse']
type CustomFormatSectionDto = components['schemas']['CustomFormatSection']
type CustomFormatConditionEvaluationDto = components['schemas']['CustomFormatConditionEvaluationResponse']
type CustomFormatEvaluationDto = components['schemas']['CustomFormatEvaluationResponse']
type QualityDefinitionDto = components['schemas']['QualityDefinitionResponse']
type QualityProfileDto = components['schemas']['QualityProfileResponse']
type MediaProfileSettingsDto = components['schemas']['MediaProfileSettingsResponse']
type CustomFormatConditionInputDto = components['schemas']['CustomFormatConditionInput']
type CustomFormatCreateDto = components['schemas']['CustomFormatCreate']

export function mapCustomFormatCondition(value: CustomFormatConditionDto): CustomFormatCondition {
  return {
    id: value.id,
    name: value.name ?? undefined,
    type: value.type ?? 'release_title',
    pattern: value.pattern ?? undefined,
    required: value.required,
    negate: value.negate,
    caseSensitive: value.case_sensitive,
    group: value.group ?? undefined,
    scoreOffset: value.score_offset ?? 0,
  }
}

export function mapCustomFormat(value: CustomFormatDto): CustomFormat {
  const conditions = value.conditions.map(mapCustomFormatCondition)
  return {
    id: value.id,
    name: value.name,
    description: value.description ?? undefined,
    enabled: value.enabled,
    builtin: value.builtin ?? false,
    schemaVersion: value.schema_version,
    conditions,
    supersedes: value.supersedes ?? [],
    conditionCount: value.condition_count,
    usedByProfiles: value.used_by_profiles,
    revision: value.revision,
    createdAt: value.created_at,
    updatedAt: value.updated_at,
  }
}

export function mapCustomFormatSection(value: CustomFormatSectionDto): CustomFormatSection {
  return {
    id: value.id,
    name: value.name,
    formatIds: value.format_ids ?? [],
  }
}

export function mapConditionEvaluation(value: CustomFormatConditionEvaluationDto): CustomFormatConditionEvaluation {
  return {
    conditionId: value.condition_id,
    conditionType: value.condition_type,
    name: value.name ?? undefined,
    matched: value.matched,
    effectiveResult: value.effective_result,
    required: value.required,
    negated: value.negated,
    evidence: value.evidence,
    expected: value.expected,
    reason: value.reason ?? undefined,
    group: value.group ?? undefined,
    regexMatch: value.regex_match ?? undefined,
    scoreOffset: value.score_offset ?? 0,
  }
}

export function mapCustomFormatEvaluation(value: CustomFormatEvaluationDto): CustomFormatEvaluation {
  return {
    customFormatId: value.custom_format_id,
    customFormatName: value.custom_format_name,
    matched: value.matched,
    rawMatched: value.raw_matched,
    suppressedBy: value.suppressed_by ?? [],
    suppressedByNames: value.suppressed_by_names ?? [],
    conditions: value.conditions.map(mapConditionEvaluation),
    groupResults: value.group_results,
    scoreOffset: value.score_offset ?? 0,
    profileScore: value.profile_score ?? undefined,
    contribution: value.contribution ?? undefined,
    error: value.error ?? undefined,
  }
}

export function mapQualityDefinition(value: QualityDefinitionDto): QualityDefinition {
  return {
    id: value.id,
    name: value.name,
    resolution: value.resolution ?? undefined,
    source: value.source ?? undefined,
    modifier: value.modifier ?? undefined,
    scanType: value.scan_type ?? undefined,
    rank: value.rank,
    enabled: value.enabled,
  }
}

export function mapQualityProfile(value: QualityProfileDto): QualityProfile {
  return {
    id: value.id,
    name: value.name,
    minimumQuality: value.minimum_quality_definition
      ? mapQualityDefinition(value.minimum_quality_definition)
      : undefined,
    minimumCustomFormatScore: value.minimum_custom_format_score ?? undefined,
    seasonPackUpgrades: value.season_pack_upgrades ?? true,
    qualities: value.qualities.map(mapQualityDefinition),
    customFormatScores: value.custom_format_scores.map((score) => ({
      customFormatId: score.custom_format_id,
      customFormatName: score.custom_format_name,
      score: score.score,
      enabled: score.enabled,
    })),
    assignedTitles: value.assigned_titles,
    assignedMovies: value.assigned_movies ?? 0,
    assignedShows: value.assigned_shows ?? 0,
    isDefault: value.is_default ?? false,
    revision: value.revision,
    createdAt: value.created_at,
    updatedAt: value.updated_at,
  }
}

export function mapMediaProfileSettings(value: MediaProfileSettingsDto): MediaProfileSettings {
  return {
    mediaType: value.media_type,
    entityId: value.entity_id,
    qualityProfileId: value.quality_profile_id ?? undefined,
    qualityProfileInherited: value.quality_profile_inherited ?? false,
    qualityProfileName: value.quality_profile_name ?? undefined,
    minimumQuality: value.minimum_quality_definition ? mapQualityDefinition(value.minimum_quality_definition) : undefined,
    profileMinimumQuality: value.profile_minimum_quality_definition ? mapQualityDefinition(value.profile_minimum_quality_definition) : undefined,
    minimumQualityOverridden: value.minimum_quality_overridden,
    minimumCustomFormatScore: value.minimum_custom_format_score ?? undefined,
    profileMinimumCustomFormatScore: value.profile_minimum_custom_format_score ?? undefined,
    minimumCustomFormatScoreOverridden: value.minimum_custom_format_score_overridden,
    customFormatScores: value.custom_format_scores.map((score) => ({
      customFormatId: score.custom_format_id,
      customFormatName: score.custom_format_name,
      profileScore: score.profile_score,
      overrideScore: score.override_score ?? undefined,
      effectiveScore: score.effective_score,
      enabled: score.enabled,
    })),
    revision: value.revision,
  }
}

export function customFormatConditionPayload(condition: CustomFormatCondition): CustomFormatConditionInputDto {
  return {
    id: condition.id || undefined,
    name: condition.name || undefined,
    type: condition.type ?? 'release_title',
    pattern: condition.pattern || '',
    required: condition.required,
    negate: condition.negate,
    case_sensitive: condition.caseSensitive,
    group: condition.group || undefined,
    score_offset: condition.scoreOffset,
  }
}

export function customFormatPayload(format: Pick<CustomFormat, 'name' | 'description' | 'enabled' | 'conditions' | 'supersedes'>): CustomFormatCreateDto {
  return {
    name: format.name,
    description: format.description || null,
    enabled: format.enabled,
    conditions: format.conditions.map(customFormatConditionPayload),
    supersedes: format.supersedes,
  }
}
