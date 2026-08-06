import { useCallback, useEffect, useState } from "react";
import {
  loadDesktopPreferences,
  resolvedTheme,
  saveDesktopPreferences,
  type DesktopPreferenceChanges,
  type DesktopPreferences,
} from "../lib/desktop-preferences";

function darkThemeQuery(): MediaQueryList | null {
  return typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
}

/** Own device-local presentation preferences and project them to root data attributes. */
export function useDesktopPreferences(): {
  preferences: DesktopPreferences;
  updatePreferences: (changes: DesktopPreferenceChanges) => void;
  requestNotificationPermission: () => Promise<NotificationPermission | "unsupported">;
} {
  const [preferences, setPreferences] = useState<DesktopPreferences>(() => loadDesktopPreferences());
  const [systemDark, setSystemDark] = useState(() => darkThemeQuery()?.matches ?? false);

  useEffect(() => {
    const query = darkThemeQuery();
    if (!query) return;
    const handleChange = (event: MediaQueryListEvent): void => setSystemDark(event.matches);
    query.addEventListener("change", handleChange);
    return () => query.removeEventListener("change", handleChange);
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.theme = resolvedTheme(preferences.theme, systemDark);
    root.dataset.themePreference = preferences.theme;
    root.dataset.density = preferences.density;
    root.dataset.codeWrap = preferences.codeWrap ? "wrap" : "scroll";
    root.dataset.activityDetail = preferences.activityDetail;
    root.lang = preferences.locale;
  }, [preferences, systemDark]);

  useEffect(() => {
    if (!window.ppxClient) return;
    void window.ppxClient.setDesktopHostPreferences({
      backgroundBehavior: preferences.backgroundBehavior,
      notificationsEnabled: preferences.notificationsEnabled,
      notificationSound: preferences.notificationSound,
    });
  }, [preferences.backgroundBehavior, preferences.notificationSound, preferences.notificationsEnabled]);

  const updatePreferences = useCallback((changes: DesktopPreferenceChanges): void => {
    setPreferences((current) => saveDesktopPreferences(current, changes));
  }, []);

  const requestNotificationPermission = useCallback(async (): Promise<NotificationPermission | "unsupported"> => {
    if (!("Notification" in window)) return "unsupported";
    if (Notification.permission !== "default") return Notification.permission;
    return Notification.requestPermission();
  }, []);

  return { preferences, updatePreferences, requestNotificationPermission };
}
