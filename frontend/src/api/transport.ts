import createClient, { type Middleware } from 'openapi-fetch'
import type { ApiErrorShape } from '../types'
import type { paths } from './generated/schema'

export class ApiError extends Error {
  status: number
  code?: string

  constructor(message: string, status: number, code?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

function readCookie(name: string): string | undefined {
  const prefix = `${encodeURIComponent(name)}=`
  return document.cookie.split('; ').find((cookie) => cookie.startsWith(prefix))?.slice(prefix.length)
}

async function ensureCsrf(): Promise<string | undefined> {
  const existing = readCookie('csrf_token') ?? readCookie('csrf') ?? readCookie('medialogue_csrf')
  if (existing) return decodeURIComponent(existing)
  try {
    await fetch('/api/v1/auth/csrf', { credentials: 'include' })
  } catch {
  }
  const refreshed = readCookie('csrf_token') ?? readCookie('csrf') ?? readCookie('medialogue_csrf')
  return refreshed ? decodeURIComponent(refreshed) : undefined
}

async function apiErrorFromResponse(response: Response, parsed?: unknown): Promise<ApiError> {
  let payload = parsed as ApiErrorShape | undefined
  if (!payload) {
    try {
      payload = await response.clone().json() as ApiErrorShape
    } catch {
    }
  }
  const message = (payload?.error?.message ?? payload?.detail ?? payload?.message ?? response.statusText) || 'Request failed'
  return new ApiError(message, response.status, payload?.error?.code)
}

function needsCsrf(method: string): boolean {
  return !['GET', 'HEAD', 'OPTIONS'].includes(method.toUpperCase())
}

const transportMiddleware: Middleware = {
  async onRequest({ request }) {
    request.headers.set('Accept', 'application/json')
    if (needsCsrf(request.method)) {
      const csrf = await ensureCsrf()
      if (csrf) request.headers.set('X-CSRF-Token', csrf)
    }
    return request
  },
  async onResponse({ response }) {
    if (!response.ok) throw await apiErrorFromResponse(response)
  },
}

export const openApiClient = createClient<paths>({
  credentials: 'include',
  headers: { Accept: 'application/json' },
})

openApiClient.use(transportMiddleware)

type OpenApiResult<T> = {
  data?: T
  error?: unknown
  response: Response
}

export async function openApiData<T>(operation: Promise<OpenApiResult<T>>): Promise<Exclude<T, undefined>> {
  const result = await operation
  if (result.data !== undefined) return result.data as Exclude<T, undefined>
  if (result.response.ok) return undefined as Exclude<T, undefined>
  throw await apiErrorFromResponse(result.response, result.error)
}
