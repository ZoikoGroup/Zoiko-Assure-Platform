"use client";

import { useCallback, useMemo } from "react";
import { useApi } from "./api";

const shortName = (n: string) => n.replace(/\s*\(.*\)/, "");

/** Resolves actor ids (e.g. "actor-gc") to people's names from the caller's own organisation directory.
 *  Falls back to the id itself, so nothing is hidden when the directory is unavailable. */
export function useActorNames() {
  const { data } = useApi<{ actor_id: string; display_name: string }[]>("/actors");
  const names = useMemo(() => new Map((data ?? []).map((a) => [a.actor_id, shortName(a.display_name)])), [data]);
  return useCallback((id?: string | null) => (id ? names.get(id) ?? id : "—"), [names]);
}
