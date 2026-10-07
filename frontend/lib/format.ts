export function fmtDate(v?: string | null, withTime = false): string {
  if (!v) return "—";
  const d = new Date(v);
  if (isNaN(d.getTime())) return v;
  return withTime
    ? d.toLocaleString("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) + " UTC"
    : d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
}

export function fmtRange(r?: [string, string | null] | string[] | null): string {
  if (!r) return "—";
  return `${fmtDate(r[0])} → ${r[1] ? fmtDate(r[1]) : "open"}`;
}

export const short = (h?: string | null, n = 10) => (h ? (h.length > n ? h.slice(0, n) + "…" : h) : "—");

export function isoLocal(d: Date) {
  return d.toISOString().slice(0, 16);
}
