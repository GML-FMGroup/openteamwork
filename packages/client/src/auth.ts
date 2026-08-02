/** Build a bearer header without placing credentials in URLs or renderer-visible query parameters. */
export function buildClientApiAuthorizationHeaders(accessToken: string): Record<string, string> {
  const normalized = accessToken.trim();
  return normalized ? { Authorization: `Bearer ${normalized}` } : {};
}
