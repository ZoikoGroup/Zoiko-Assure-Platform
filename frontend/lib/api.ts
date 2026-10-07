"use client";

import { useCallback, useEffect, useState } from "react";
import { useActor } from "./actor";

export const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8100/api";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
  }
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

export async function api<T = any>(path: string, opts: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { ...authHeaders(), ...(opts.headers as Record<string, string>) };
  let body = opts.body;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  }
  const res = await fetch(`${API}${path}`, { ...opts, headers, body, cache: "no-store" });
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    unauthorized(res.status);
    throw new ApiError(res.status, data?.detail ?? data);
  }
  return data as T;
}

export async function download(path: string, filename: string) {
  const res = await fetch(`${API}${path}`, { headers: authHeaders() });
  if (!res.ok) {
    unauthorized(res.status);
    throw new ApiError(res.status, await res.text());
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
  const [loading, setLoading] = useState(false);
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

export function errText(e: unknown): string {
  if (!e) return "";
  if (e instanceof ApiError) {
    const d: any = e.detail;
    if (typeof d === "string") return d;
    if (d?.message) return `${d.error ?? ""} ${d.message}`.trim();
    if (Array.isArray(d)) return d.map((x: any) => x.msg ?? JSON.stringify(x)).join("; ");
    return JSON.stringify(d);
  }
  return String((e as Error).message ?? e);
}
