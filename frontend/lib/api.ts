"use client";

import { useCallback, useEffect, useState } from "react";
import { useActor } from "./actor";

export const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

/** A troubleshooting identifier the server sent with a response. */
export type ErrorReference = { label: string; value: string };

export class ApiError extends Error {
  status: number;
  detail: unknown;
  reference?: ErrorReference;
  constructor(status: number, detail: unknown, reference?: ErrorReference) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
    this.reference = reference;
  }
}

/* Request and correlation IDs (ZA-API-001 §04/§12) are only ever read from the response, never generated here. The
   current backend sends neither, and a cross-origin response header is readable only when the API exposes it via CORS. */
function referenceOf(headers: Headers | null, body: any): ErrorReference | undefined {
  const request = headers?.get("x-assure-request-id") ?? headers?.get("x-request-id") ?? (typeof body?.request_id === "string" ? body.request_id : null);
  if (request) return { label: "Request ID", value: request };
  const correlation = headers?.get("x-correlation-id") ?? (typeof body?.correlation_id === "string" ? body.correlation_id : null);
  return correlation ? { label: "Correlation ID", value: correlation } : undefined;
}

/** The ApiError for a failed `fetch` response: its JSON `detail` (or text) and any reference the server supplied. */
export async function responseError(res: Response): Promise<ApiError> {
  const text = await res.text().catch(() => "");
  let data: any = text;
  try { data = text ? JSON.parse(text) : null; } catch {}
  return new ApiError(res.status, data?.detail ?? data, referenceOf(res.headers, data));
}

const TOKEN_KEY = "za-session";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
}
export function setToken(t: string) { try { localStorage.setItem(TOKEN_KEY, t); } catch {} }
export function clearToken() { try { localStorage.removeItem(TOKEN_KEY); } catch {} }

export function authHeaders(): Record<string, string> {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

function unauthorized(status: number) {
  if (status === 401 && typeof window !== "undefined") window.dispatchEvent(new Event("za-unauthorized"));
}

type Raw = { ok: boolean; status: number; text: string; headers: Headers };

/* Identical plain GETs that are already in flight share one network request: React StrictMode mounts every page twice
   in development, and the header and a page can ask for the same resource at the same moment. The key includes the
   session token, so nothing is shared across identities, and an entry lives only until its request settles, so no
   response is ever reused later (no caching, no stale data). Each caller still parses its own copy. */
const inFlight = new Map<string, Promise<Raw>>();

export async function api<T = any>(path: string, opts: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { ...authHeaders(), ...(opts.headers as Record<string, string>) };
  let body = opts.body;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  }
  const plainGet = opts.method === undefined && opts.body === undefined && opts.json === undefined && opts.headers === undefined && !opts.signal;
  const key = plainGet ? `${getToken() ?? ""} ${path}` : null;
  let pending = key ? inFlight.get(key) : undefined;
  if (!pending) {
    pending = fetch(`${API}${path}`, { ...opts, headers, body, cache: "no-store" })
      .then(async (res): Promise<Raw> => ({ ok: res.ok, status: res.status, text: await res.text(), headers: res.headers }));
    if (key) {
      inFlight.set(key, pending);
      pending.finally(() => inFlight.delete(key)).catch(() => {});
    }
  }
  const res = await pending;
  const text = res.text;
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    unauthorized(res.status);
    throw new ApiError(res.status, data?.detail ?? data, referenceOf(res.headers, data));
  }
  return data as T;
}

export async function download(path: string, filename: string) {
  const res = await fetch(`${API}${path}`, { headers: authHeaders() });
  if (!res.ok) {
    unauthorized(res.status);
    throw await responseError(res);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function useApi<T = any>(path: string | null, deps: unknown[] = []) {
  const { actorId } = useActor();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  // Loading from the first render when a request will be made, so the frame before the effect runs is not shown as empty.
  const [loading, setLoading] = useState(!!path && !!actorId);
  const load = useCallback(async () => {
    if (!path || !actorId) return;
    setLoading(true);
    setError(null);
    try {
      setData(await api<T>(path));
    } catch (e) {
      setError(e as ApiError);
      setData(null);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, actorId, ...deps]);
  useEffect(() => {
    load();
  }, [load]);
  return { data, error, loading, reload: load, setData };
}

const LOCATIONS = new Set(["body", "query", "path", "header", "cookie"]);

/** Readable text for an API `detail`: server sentences as written (but never an HTML error page), validation lists as
 *  "field: message" (without echoing the submitted input), and coded objects as their message plus a short summary of
 *  any report they carry. Never raw JSON. */
export function describeDetail(d: unknown): string {
  if (d == null) return "";
  if (typeof d === "string") return /^\s*</.test(d) ? "" : d.trim();
  if (Array.isArray(d)) {
    return d.map((x: any) => {
      if (!x || typeof x !== "object") return String(x ?? "");
      const field = Array.isArray(x.loc) ? x.loc.filter((p: unknown) => !LOCATIONS.has(String(p))).join(".") : "";
      const msg = typeof x.msg === "string" ? x.msg : typeof x.message === "string" ? x.message : "";
      return field && msg ? `${field}: ${msg}` : msg || field;
    }).filter(Boolean).join("; ");
  }
  if (typeof d === "object") {
    const o = d as Record<string, any>;
    const parts: string[] = [];
    if (typeof o.message === "string") parts.push(o.message);
    if (Array.isArray(o.failing) && o.failing.length) parts.push(`Failing: ${o.failing.join(", ")}`);
    const issues = Array.isArray(o.validation?.issues) ? o.validation.issues.filter((i: any) => i?.severity === "ERROR") : [];
    if (issues.length) parts.push(`${issues.length} validation error(s): ${issues.slice(0, 3).map((i: any) => i.message).join("; ")}${issues.length > 3 ? "; …" : ""}`);
    if (typeof o.vectors?.failed === "number" && typeof o.vectors?.total === "number") parts.push(`${o.vectors.failed} of ${o.vectors.total} golden vectors failed`);
    const text = parts.join(". ");
    const code = typeof o.error === "string" ? o.error : "";
    return code ? (text ? `${text} (${code})` : code) : text;
  }
  return String(d);
}

const FALLBACK: Record<number, string> = {
  401: "Your session has ended. Sign in again to continue.",
  403: "You don't have permission to do this.",
  404: "The requested record was not found.",
  409: "This conflicts with the record's current state. Reload the page and try again.",
  413: "The upload is too large.",
  422: "Some values were not accepted. Check the form and try again.",
  429: "Too many requests. Wait a moment and try again.",
};
const OFFLINE = /failed to fetch|networkerror|load failed|network request failed/i;

/** User-facing text for an error. API refusals keep the server's own explanation, which is written for people;
 *  validation lists name their fields; server faults and unreachable servers get a short message, not raw output. */
export function errText(e: unknown): string {
  if (!e) return "";
  if (e instanceof ApiError) {
    if (e.status === 0) return "Couldn't reach the Zoiko Assure server. Check your connection and try again.";
    if (e.status >= 500) {
      const code = typeof (e.detail as any)?.error === "string" ? ` Reference code: ${(e.detail as any).error}.` : "";
      return `The server couldn't complete this request (HTTP ${e.status}). Try again shortly; if it keeps happening, report it${e.reference ? ` with the ${e.reference.label.replace(/^\w/, (c) => c.toLowerCase())} shown` : ""}.${code}`;
    }
    const d = describeDetail(e.detail);
    if (e.status === 422 && Array.isArray(e.detail) && d) return `Some values were not accepted. ${d}`;
    return d || (FALLBACK[e.status] ?? `The request could not be completed (HTTP ${e.status}).`);
  }
  if (e instanceof TypeError && OFFLINE.test(e.message)) return "Couldn't reach the Zoiko Assure server. Check your connection and try again.";
  if (e instanceof Error) return e.name === "AbortError" ? "The request was cancelled." : e.message;
  return String(e);
}
