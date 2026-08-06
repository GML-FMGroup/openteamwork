import { describe, expect, it } from "vitest";
import {
  DESKTOP_PREFERENCES_STORAGE_KEY,
  defaultDesktopPreferences,
  loadDesktopPreferences,
  parseDesktopPreferences,
  resolvedTheme,
  saveDesktopPreferences,
} from "../app/src/lib/desktop-preferences";

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() { return values.size; },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => Array.from(values.keys())[index] ?? null,
    removeItem: (key) => { values.delete(key); },
    setItem: (key, value) => { values.set(key, value); },
  };
}

describe("desktop preferences", () => {
  it("falls back field-by-field when persisted data is invalid", () => {
    const parsed = parseDesktopPreferences({
      revision: -1,
      theme: "neon",
      locale: "fr",
      timezone: "Asia/Shanghai",
      density: "compact",
      codeWrap: true,
    });

    expect(parsed.theme).toBe("system");
    expect(parsed.locale).toBe("en");
    expect(parsed.timezone).toBe("Asia/Shanghai");
    expect(parsed.density).toBe("compact");
    expect(parsed.codeWrap).toBe(true);
    expect(parsed.schemaVersion).toBe(1);
  });

  it("stores revisions separately from Node configuration", () => {
    const storage = memoryStorage();
    const initial = defaultDesktopPreferences();
    const next = saveDesktopPreferences(initial, { theme: "dark", activityDetail: "detailed" }, storage);

    expect(next.revision).toBe(initial.revision + 1);
    expect(loadDesktopPreferences(storage)).toMatchObject({
      theme: "dark",
      activityDetail: "detailed",
      revision: next.revision,
    });
    expect(storage.getItem(DESKTOP_PREFERENCES_STORAGE_KEY)).not.toBeNull();
  });

  it("resolves the system theme without changing the stored preference", () => {
    expect(resolvedTheme("system", true)).toBe("dark");
    expect(resolvedTheme("system", false)).toBe("light");
    expect(resolvedTheme("light", true)).toBe("light");
  });
});
