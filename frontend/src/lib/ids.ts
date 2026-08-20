/** Non-secure-context safe client id generator.
 *
 * `crypto.randomUUID` only exists in secure contexts (HTTPS or localhost).
 * Medialogue is normally reached over plain HTTP on a LAN address, where the
 * property is `undefined` and calling it throws a TypeError during render.
 */
export function clientId(): string {
  const webCrypto = typeof globalThis !== 'undefined' ? globalThis.crypto : undefined
  if (webCrypto && typeof webCrypto.randomUUID === 'function') return webCrypto.randomUUID()
  if (webCrypto && typeof webCrypto.getRandomValues === 'function') {
    const bytes = webCrypto.getRandomValues(new Uint8Array(16))
    bytes[6] = (bytes[6] & 0x0f) | 0x40
    bytes[8] = (bytes[8] & 0x3f) | 0x80
    const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
  }
  return `id-${Date.now().toString(16)}-${Math.random().toString(16).slice(2, 10)}`
}
