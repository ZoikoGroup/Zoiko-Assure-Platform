"use client";

import { useSyncExternalStore } from "react";
import { Moon, Sun } from "lucide-react";
import { THEME_KEY, type Theme } from "@/lib/theme";

/* One theme for every screen. The data-theme attribute on <html> is the single source of truth: THEME_SCRIPT sets it
   before first paint, setTheme changes it (and stores the choice), and every useTheme() re-renders from it. */

const SYSTEM = "(prefers-color-scheme: dark)";
const listeners = new Set<() => void>();
let systemQuery: MediaQueryList | null = null;

const stored = (): Theme | null => {
  try { const t = localStorage.getItem(THEME_KEY); return t === "light" || t === "dark" ? t : null; } catch { return null; }
};
const system = (): Theme => (window.matchMedia(SYSTEM).matches ? "dark" : "light");
const read = (): Theme => (document.documentElement.dataset.theme === "dark" ? "dark" : "light");
const apply = (t: Theme) => { document.documentElement.dataset.theme = t; listeners.forEach((l) => l()); };

// Until a choice is stored the theme follows the operating system; a choice made in another tab applies here too.
const onSystem = () => { if (!stored()) apply(system()); };
const onStorage = (e: StorageEvent) => { if (e.key === THEME_KEY || e.key === null) apply(stored() ?? system()); };

function subscribe(listener: () => void) {
  if (listeners.size === 0) {
    systemQuery = window.matchMedia(SYSTEM);
    systemQuery.addEventListener("change", onSystem);
    window.addEventListener("storage", onStorage);
  }
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (listeners.size > 0) return;
    systemQuery?.removeEventListener("change", onSystem);
    systemQuery = null;
    window.removeEventListener("storage", onStorage);
  };
}

/** The current theme (null while hydrating: the server cannot know it) and a setter that applies and stores a choice. */
export function useTheme() {
  const theme = useSyncExternalStore<Theme | null>(subscribe, read, () => null);
  const setTheme = (t: Theme) => { try { localStorage.setItem(THEME_KEY, t); } catch {} apply(t); };
  return { theme, setTheme };
}

/** The light/dark switch: a sun while dark (switch to light), a moon while light. The icon is chosen by CSS from the
 *  attribute, so it is right from first paint; the label names the action. Callers supply the button's styling. */
export function ThemeToggle({ className, iconSize = 16 }: { className: string; iconSize?: number }) {
  const { theme, setTheme } = useTheme();
  const label = `Switch to ${theme === "dark" ? "light" : "dark"} theme`;
  return (
    <button type="button" onClick={() => setTheme(read() === "dark" ? "light" : "dark")} aria-label={label} title={label} className={className}>
      <Moon size={iconSize} aria-hidden className="dark:hidden" />
      <Sun size={iconSize} aria-hidden className="hidden dark:block" />
    </button>
  );
}
