import { useCallback, useEffect, useState } from "react";

export type ThemePreference = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "pwa.theme";
const SYSTEM_QUERY = "(prefers-color-scheme: dark)";

export function isThemePreference(value: unknown): value is ThemePreference {
  return value === "light" || value === "dark" || value === "system";
}

function storage(): Storage | undefined {
  try {
    const candidate = globalThis.localStorage;
    // Node exposes a non-conforming stub unless --localstorage-file is set.
    return candidate && typeof candidate.getItem === "function"
      ? candidate
      : undefined;
  } catch {
    // Private browsing and some embedded webviews throw on access.
    return undefined;
  }
}

export function readThemePreference(): ThemePreference {
  const stored = storage()?.getItem(THEME_STORAGE_KEY);
  return isThemePreference(stored) ? stored : "system";
}

export function writeThemePreference(preference: ThemePreference): void {
  try {
    storage()?.setItem(THEME_STORAGE_KEY, preference);
  } catch {
    // A rejected write only costs persistence, never the theme itself.
  }
}

export function systemPrefersDark(): boolean {
  return (
    typeof globalThis.matchMedia === "function" &&
    globalThis.matchMedia(SYSTEM_QUERY).matches
  );
}

export function resolveTheme(preference: ThemePreference): ResolvedTheme {
  if (preference === "system") return systemPrefersDark() ? "dark" : "light";
  return preference;
}

/** Paints the resolved theme onto the document element. */
export function applyTheme(preference: ThemePreference): ResolvedTheme {
  const resolved = resolveTheme(preference);
  if (typeof document !== "undefined") {
    document.documentElement.dataset.theme = resolved;
    document.documentElement.style.colorScheme = resolved;
  }
  return resolved;
}

export interface ThemeState {
  preference: ThemePreference;
  resolved: ResolvedTheme;
  setPreference: (preference: ThemePreference) => void;
}

/**
 * Owns the theme preference and keeps the document in step with it, including
 * live system changes while the preference is `system`.
 */
export function useThemePreference(): ThemeState {
  const [preference, setPreferenceState] = useState<ThemePreference>(() =>
    readThemePreference(),
  );
  const [resolved, setResolved] = useState<ResolvedTheme>(() =>
    resolveTheme(preference),
  );

  const setPreference = useCallback((next: ThemePreference) => {
    writeThemePreference(next);
    setPreferenceState(next);
    setResolved(applyTheme(next));
  }, []);

  useEffect(() => {
    setResolved(applyTheme(preference));
  }, [preference]);

  useEffect(() => {
    if (preference !== "system") return;
    if (typeof globalThis.matchMedia !== "function") return;
    const query = globalThis.matchMedia(SYSTEM_QUERY);
    const onChange = () => setResolved(applyTheme("system"));
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, [preference]);

  return { preference, resolved, setPreference };
}
