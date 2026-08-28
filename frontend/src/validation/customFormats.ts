import { z } from 'zod'
import type { components } from '../api/generated/schema'

type CustomFormatImportBundleDto = components['schemas']['CustomFormatImportBundle']
type RefinementContext = {
  addIssue(issue: { code: 'custom'; path: PropertyKey[]; message: string }): void
}

const conditionTypeSchema = z.enum([
  'release_title',
  'release_group',
  'quality',
  'quality_modifier',
  'resolution',
  'source',
  'edition',
  'language',
  'indexer',
  'web_provider',
  'video_codec',
  'audio_codec',
  'audio_channels',
  'hdr_type',
  'release_attribute',
])

const conditionSchema = z.object({
  id: z.string().max(128).nullable().optional(),
  name: z.string().max(256).nullable().optional(),
  type: conditionTypeSchema,
  value: z.union([z.string(), z.array(z.string()), z.null()]).optional(),
  pattern: z.string().max(4096).nullable().optional(),
  required: z.boolean().default(false),
  negate: z.boolean().default(false),
  case_sensitive: z.boolean().default(false),
  group: z.string().max(128).nullable().optional(),
  score_offset: z.number().int().min(-100000).max(100000).default(0),
}).superRefine((
  condition: { pattern?: string | null; value?: string | string[] | null; type: string },
  context: RefinementContext,
) => {
  const hasPattern = typeof condition.pattern === 'string' && condition.pattern.trim().length > 0
  const hasValue = typeof condition.value === 'string'
    ? condition.value.trim().length > 0
    : Array.isArray(condition.value) && condition.value.some((value: string) => value.trim().length > 0)

  if (!hasPattern && !hasValue) {
    context.addIssue({
      code: 'custom',
      path: ['pattern'],
      message: `${condition.type} conditions require a regex pattern or legacy value`,
    })
  }
})

const customFormatSchema = z.object({
  schema_version: z.number().int().default(1),
  name: z.string().trim().min(1).max(256),
  description: z.string().max(4096).nullable().optional(),
  media_scope: z.enum(['movies', 'shows', 'both']).default('both'),
  enabled: z.boolean().default(true),
  conditions: z.array(conditionSchema).min(1).max(100),
})

const customFormatImportSchema = z.object({
  application: z.string(),
  schema_version: z.number().int(),
  custom_formats: z.array(customFormatSchema).min(1).max(500),
}).superRefine((
  bundle: { application: string; schema_version: number; custom_formats: Array<{ name: string }> },
  context: RefinementContext,
) => {
  if (bundle.application.toLowerCase() !== 'medialogue') {
    context.addIssue({
      code: 'custom',
      path: ['application'],
      message: 'This file is not a Medialogue Custom Format export.',
    })
  }
  if (bundle.schema_version !== 1) {
    context.addIssue({
      code: 'custom',
      path: ['schema_version'],
      message: `Custom Format schema ${bundle.schema_version} is not supported.`,
    })
  }

  const seenNames = new Set<string>()
  bundle.custom_formats.forEach((format: { name: string }, index: number) => {
    const key = format.name.trim().toLowerCase()
    if (seenNames.has(key)) {
      context.addIssue({
        code: 'custom',
        path: ['custom_formats', index, 'name'],
        message: `Duplicate Custom Format name: ${format.name.trim()}`,
      })
    }
    seenNames.add(key)
  })
})

function formatValidationError(error: { issues: Array<{ path: PropertyKey[]; message: string }> }): string {
  const issue = error.issues[0]
  if (!issue) return 'The selected file is not a valid Custom Format export.'
  const path = issue.path.length ? `${issue.path.join('.')}: ` : ''
  return `Invalid Custom Format export: ${path}${issue.message}`
}

export function parseCustomFormatImportBundle(input: unknown): CustomFormatImportBundleDto {
  const result = customFormatImportSchema.safeParse(input)
  if (!result.success) throw new Error(formatValidationError(result.error))
  return result.data
}
