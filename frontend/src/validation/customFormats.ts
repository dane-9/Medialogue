import { z } from 'zod'
import type { components } from '../api/generated/schema'

type CustomFormatImportBundleDto = components['schemas']['CustomFormatImportBundle']
type RefinementContext = {
  addIssue(issue: { code: 'custom'; path: PropertyKey[]; message: string }): void
}

// Mirrors CustomFormatConditionType. Bundles exported before the field
// existed carry no type, and those have always meant the raw release title.
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
]).default('release_title')

const conditionSchema = z.object({
  id: z.string().max(128).nullable().optional(),
  name: z.string().max(256).nullable().optional(),
  type: conditionTypeSchema,
  pattern: z.string().max(4096).nullable().optional(),
  required: z.boolean().default(false),
  negate: z.boolean().default(false),
  case_sensitive: z.boolean().default(false),
  group: z.string().max(128).nullable().optional(),
  score_offset: z.number().int().min(-100000).max(100000).default(0),
}).strict().superRefine((
  condition: { pattern?: string | null },
  context: RefinementContext,
) => {
  if (typeof condition.pattern !== 'string' || !condition.pattern.trim()) {
    context.addIssue({
      code: 'custom',
      path: ['pattern'],
      message: 'Custom Format conditions require a regex pattern.',
    })
  }
})

const customFormatSchema = z.object({
  schema_version: z.literal(1),
  name: z.string().trim().min(1).max(256),
  description: z.string().max(4096).nullable().optional(),
  enabled: z.boolean().default(true),
  conditions: z.array(conditionSchema).min(1).max(100),
  supersedes: z.array(z.string().trim().min(1).max(256)).max(500).default([]),
}).strict()

const customFormatImportSchema = z.object({
  application: z.literal('Medialogue'),
  schema_version: z.literal(1),
  custom_formats: z.array(customFormatSchema).min(1).max(500),
}).strict().superRefine((
  bundle: { custom_formats: Array<{ name: string }> },
  context: RefinementContext,
) => {
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
