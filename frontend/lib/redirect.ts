/**
 * Post-sign-in redirect target from the `?next=` parameter.
 *
 * Only an internal absolute path that resolves to exactly the current origin is honoured. Anything else (absolute or
 * protocol-relative URLs, backslash tricks such as `/\evil.example`, control characters, `javascript:` and other
 * schemes, unparsable values) falls back to "/". The returned value is the normalised same-origin path, so the target
 * that is navigated to is the one that was validated.
 */
export function safeNextPath(raw: string | null | undefined, origin: string): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//") || /[\\\u0000-\u001f\u007f]/.test(raw)) return "/";
  let url: URL;
  try {
    url = new URL(raw, origin);
  } catch {
    return "/";
  }
  if (url.origin !== origin || url.pathname === "/login") return "/";
  return `${url.pathname}${url.search}${url.hash}`;
}
