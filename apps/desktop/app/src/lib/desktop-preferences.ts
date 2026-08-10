export const DESKTOP_PREFERENCES_STORAGE_KEY = "openteamwork.desktop.preferences.v1";

export type ColorTheme = "system" | "light" | "dark";
export type InterfaceDensity = "comfortable" | "compact";
export type ActivityDetail = "summary" | "detailed";
export type DesktopLocale = "en";
export type BackgroundBehavior = "keep-running" | "confirm-before-close";

export interface DesktopPreferences {
  schemaVersion: 1;
  revision: number;
  updatedAt: string;
  theme: ColorTheme;
  locale: DesktopLocale;
  timezone: string;
  density: InterfaceDensity;
  codeWrap: boolean;
  activityDetail: ActivityDetail;
  notificationsEnabled: boolean;
  notificationSound: boolean;
  backgroundBehavior: BackgroundBehavior;
}

export type DesktopPreferenceChanges = Partial<Omit<DesktopPreferences, "schemaVersion" | "revision" | "updatedAt">>;

function systemTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** Return the stable device-local defaults without reading Node or Agent configuration. */
export function defaultDesktopPreferences(): DesktopPreferences {
  return {
    schemaVersion: 1,
    revision: 1,
    updatedAt: new Date(0).toISOString(),
    theme: "system",
    locale: "en",
    timezone: systemTimezone(),
    density: "comfortable",
    codeWrap: false,
    activityDetail: "summary",
    notificationsEnabled: false,
    notificationSound: false,
    backgroundBehavior: "confirm-before-close",
  };
}

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

/** Validate and migrate persisted Desktop preferences. Invalid fields fail back to safe defaults. */
export function parseDesktopPreferences(value: unknown): DesktopPreferences {
  const defaults = defaultDesktopPreferences();
  const input = record(value);
  const theme = input.theme === "light" || input.theme === "dark" || input.theme === "system" ? input.theme : defaults.theme;
  const locale: DesktopLocale = input.locale === "en" ? "en" : defaults.locale;
  const density = input.density === "compact" || input.density === "comfortable" ? input.density : defaults.density;
  const activityDetail = input.activityDetail === "detailed" || input.activityDetail === "summary" ? input.activityDetail : defaults.activityDetail;
  const backgroundBehavior = input.backgroundBehavior === "keep-running" || input.backgroundBehavior === "confirm-before-close"
    ? input.backgroundBehavior
    : defaults.backgroundBehavior;
  const revision = Number.isSafeInteger(input.revision) && Number(input.revision) > 0 ? Number(input.revision) : defaults.revision;
  return {
    schemaVersion: 1,
    revision,
    updatedAt: typeof input.updatedAt === "string" && input.updatedAt ? input.updatedAt : defaults.updatedAt,
    theme,
    locale,
    timezone: typeof input.timezone === "string" && input.timezone.trim() ? input.timezone.trim() : defaults.timezone,
    density,
    codeWrap: typeof input.codeWrap === "boolean" ? input.codeWrap : defaults.codeWrap,
    activityDetail,
    notificationsEnabled: typeof input.notificationsEnabled === "boolean" ? input.notificationsEnabled : defaults.notificationsEnabled,
    notificationSound: typeof input.notificationSound === "boolean" ? input.notificationSound : defaults.notificationSound,
    backgroundBehavior,
  };
}

export function loadDesktopPreferences(storage: Pick<Storage, "getItem"> = window.localStorage): DesktopPreferences {
  try {
    const raw = storage.getItem(DESKTOP_PREFERENCES_STORAGE_KEY);
    return raw ? parseDesktopPreferences(JSON.parse(raw)) : defaultDesktopPreferences();
  } catch {
    return defaultDesktopPreferences();
  }
}

export function saveDesktopPreferences(
  current: DesktopPreferences,
  changes: DesktopPreferenceChanges,
  storage: Pick<Storage, "setItem"> = window.localStorage,
): DesktopPreferences {
  const next = parseDesktopPreferences({
    ...current,
    ...changes,
    revision: current.revision + 1,
    updatedAt: new Date().toISOString(),
  });
  storage.setItem(DESKTOP_PREFERENCES_STORAGE_KEY, JSON.stringify(next));
  return next;
}

export function resolvedTheme(theme: ColorTheme, darkSystemTheme: boolean): "light" | "dark" {
  return theme === "system" ? (darkSystemTheme ? "dark" : "light") : theme;
}
