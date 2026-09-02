import { openApiClient, openApiData } from '../transport'

export const authApi = {
  login: (username: string, password: string) => openApiData(openApiClient.POST('/api/v1/auth/login', { body: { username, password } })),
  bootstrapStatus: () => openApiData(openApiClient.GET('/api/v1/auth/bootstrap/status')),
  bootstrap: (setupToken: string, username: string, password: string) => openApiData(openApiClient.POST('/api/v1/auth/bootstrap', { body: { setup_token: setupToken, username, password } })),
  logout: () => openApiData(openApiClient.POST('/api/v1/auth/logout')),
  session: () => openApiData(openApiClient.GET('/api/v1/auth/me')),
  sessions: () => openApiData(openApiClient.GET('/api/v1/auth/sessions')),
  revokeSession: (sessionId: string) => openApiData(openApiClient.DELETE('/api/v1/auth/sessions/{session_id}', { params: { path: { session_id: sessionId } } })),
  revokeOtherSessions: () => openApiData(openApiClient.POST('/api/v1/auth/sessions/revoke-others')),
  security: async () => {
    const payload = await openApiData(openApiClient.GET('/api/v1/auth/security'))
    return { ...payload, session_expires_at: payload.session_expires_at ?? undefined }
  },
  changePassword: (currentPassword: string, newPassword: string) => openApiData(openApiClient.POST('/api/v1/auth/password', { body: { current_password: currentPassword, new_password: newPassword } })),
}
