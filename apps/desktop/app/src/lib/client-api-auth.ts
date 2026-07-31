/** Build a Main-side bearer header without placing credentials in URLs. */
export function buildClientApiAuthorizationHeaders(accessToken: string): Record<string, string> {
  const normalized = accessToken.trim();
  return normalized ? { Authorization: `Bearer ${normalized}` } : {};
}
