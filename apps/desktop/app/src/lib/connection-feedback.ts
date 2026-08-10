import type { ConnectionSettings } from "../types";
import { productProfile } from "../../../product";

function unwrapDesktopError(error: unknown): string {
  let message = error instanceof Error ? error.message : String(error);
  message = message.replace(/^Error invoking remote method '[^']+':\s*/, "").trim();
  while (/^(?:Error|ClientApiRequestError):\s*/.test(message)) {
    message = message.replace(/^(?:Error|ClientApiRequestError):\s*/, "").trim();
  }
  return message;
}

/** Translate connection transport failures into stable, actionable UI copy. */
export function connectionFailureMessage(
  error: unknown,
  settings: ConnectionSettings,
): string {
  const detail = unwrapDesktopError(error);
  const url = settings.clientApiBaseUrl.trim() || "the configured URL";

  if (/\b(?:401|403|unauthori[sz]ed|forbidden)\b|(?:access|auth) token|invalid token/i.test(detail)) {
    return "The Node rejected the access token. Check the token and try again.";
  }
  if (/protocol.*(?:incompatible|mismatch|unsupported)|not compatible/i.test(detail)) {
    return `This Node is not compatible with this version of ${productProfile.displayName} Desktop. Update the Node or Desktop, then try again.`;
  }
  if (
    /fetch failed|failed to fetch|econnrefused|ehostunreach|enotfound|timed? out|abort|unavailable|requires the (?:OpenPPX|OpenTeamwork) Client API/i
      .test(detail)
  ) {
    return settings.targetType === "local"
      ? `Couldn’t reach an ${productProfile.displayName} Node at ${url}. Check the URL and make sure the Node is running, then try again.`
      : `Couldn’t reach the ${productProfile.displayName} Node at ${url}. Check the address and network connection, then try again.`;
  }
  return "Connection test failed. Check the Node URL and credentials, then try again.";
}
