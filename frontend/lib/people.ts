"use client";

import { useCallback, useMemo } from "react";
import { useAuth } from "./actor";
import { useApi } from "./api";
import SCREEN_ACCESS from "./screen-access.json";

const shortName = (n: string) => n.replace(/\s*\(.*\)/, "");

/* The permissions GET /actors is served to (lib/screen-access.json `panels.directory`, generated from the backend guard
   and checked by backend/tests/test_sidebar_integration.py). Someone without any of them, for example an External
   Auditor (audit_read), is refused, and every refusal is recorded as an authorization denial, so the directory is not
   requested for them at all. */
const DIRECTORY_PERMS: readonly string[] = SCREEN_ACCESS.panels.directory.perms;

/** Resolves actor ids (e.g. "actor-gc") to people's names from the caller's own organisation directory.
 *  Falls back to the id itself, so nothing is hidden when the directory is unavailable or not readable by the caller's
 *  roles (then no request is made). */
export function useActorNames() {
  const { me } = useAuth();
  const readable = DIRECTORY_PERMS.some((p) => me?.permissions?.includes(p));
  const { data } = useApi<{ actor_id: string; display_name: string }[]>(readable ? "/actors" : null);
  const names = useMemo(() => new Map((data ?? []).map((a) => [a.actor_id, shortName(a.display_name)])), [data]);
  return useCallback((id?: string | null) => (id ? names.get(id) ?? id : "—"), [names]);
}
