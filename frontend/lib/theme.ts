/* The application theme: light or dark, held as data-theme on <html>. Every screen follows that one attribute: the
   foundation tokens (app/design-tokens.css) and the application palette (app/theme.css) both switch on it.
   No React here, so the root layout (a server component) can inline THEME_SCRIPT; the hook and the header button are
   in components/ThemeToggle.tsx. */

export type Theme = "light" | "dark";

/** localStorage key for the person's choice. Absent until they choose, so the theme follows the operating system. */
export const THEME_KEY = "za-theme";

/** Runs in <head> before first paint: the stored choice, else the operating system's preference. The page therefore
 *  never flashes the other theme, and hydration finds the attribute already set. */
export const THEME_SCRIPT = `(function(){var t;try{t=localStorage.getItem(${JSON.stringify(THEME_KEY)})}catch(e){}if(t!=="light"&&t!=="dark")t=window.matchMedia&&window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light";document.documentElement.setAttribute("data-theme",t)})()`;
