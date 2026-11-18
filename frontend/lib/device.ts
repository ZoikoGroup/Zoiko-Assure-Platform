/* A readable name for the device a session was signed in from, made from the user-agent string the server recorded at
   sign-in (at most 300 characters). Display only: nothing is decided from it, it is shown as plain text, and the
   original string is always shown alongside, because a user agent is whatever the client chose to send. */

export type DeviceInfo = { browser: string | null; os: string | null; label: string };

// Most specific first: Edge, Opera and Samsung Internet also announce "Chrome", and Chrome also announces "Safari".
const BROWSERS: [RegExp, string][] = [
  [/\bEdg(?:e|A|iOS)?\/(\d+)/, "Edge"],
  [/\b(?:OPR|Opera)\/(\d+)/, "Opera"],
  [/\bSamsungBrowser\/(\d+)/, "Samsung Internet"],
  [/\b(?:Firefox|FxiOS)\/(\d+)/, "Firefox"],
  [/\bHeadlessChrome\/(\d+)/, "Headless Chrome"],
  [/\b(?:Chrome|CriOS)\/(\d+)/, "Chrome"],
  [/\bVersion\/(\d+)[\d.]* (?:Mobile\/\S+ )?Safari\//, "Safari"],
];
// iPhone and iPad user agents say "like Mac OS X", and Android and ChromeOS ones say "Linux": those come first.
const SYSTEMS: [RegExp, string][] = [
  [/\bWindows NT\b/, "Windows"],
  [/\b(?:iPhone|iPod)\b/, "iOS"],
  [/\biPad\b/, "iPadOS"],
  [/\bCrOS\b/, "ChromeOS"],
  [/\bAndroid\b/, "Android"],
  [/\bMac OS X\b/, "macOS"],
  [/\bLinux\b/, "Linux"],
];
// Programs that call the API directly name themselves rather than a browser.
const CLIENTS: [RegExp, string][] = [
  [/^curl\//i, "curl"],
  [/^python-(?:requests|httpx|urllib)/i, "Python client"],
  [/^testclient$/i, "Test client"],
];

/** "Chrome 142 on Windows", "Safari 17 on iOS", "Firefox 131", "Browser on Android", "Not recorded". */
export function describeUserAgent(ua: string | null | undefined): DeviceInfo {
  const s = (ua ?? "").trim();
  if (!s) return { browser: null, os: null, label: "Not recorded" };
  const client = CLIENTS.find(([re]) => re.test(s));
  if (client) return { browser: client[1], os: null, label: client[1] };
  let browser: string | null = null;
  for (const [re, name] of BROWSERS) {
    const m = re.exec(s);
    if (m) { browser = `${name} ${m[1]}`; break; }
  }
  const os = SYSTEMS.find(([re]) => re.test(s))?.[1] ?? null;
  const label = browser && os ? `${browser} on ${os}` : browser ?? (os ? `Browser on ${os}` : "Unrecognised device");
  return { browser, os, label };
}
