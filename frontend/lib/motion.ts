"use client";

import { useEffect, useState } from "react";

const reduced = () => typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/** Animates a number from 0 to `target` (ease-out cubic). Returns the final value at once when motion is reduced. */
export function useCountUp(target: number, duration = 1100, delay = 0): number {
  const [v, setV] = useState(0);
  useEffect(() => {
    if (reduced() || !Number.isFinite(target)) { setV(target); return; }
    let raf = 0;
    let start = 0;
    const t0 = setTimeout(() => {
      const step = (ts: number) => {
        if (!start) start = ts;
        const p = Math.min(1, (ts - start) / duration);
        setV(Math.round(target * (1 - Math.pow(1 - p, 3))));
        if (p < 1) raf = requestAnimationFrame(step);
      };
      raf = requestAnimationFrame(step);
    }, delay);
    return () => { clearTimeout(t0); cancelAnimationFrame(raf); };
  }, [target, duration, delay]);
  return v;
}

/** Inline style helper for staggered CSS animation delays. */
export const delay = (ms: number) => ({ ["--d" as string]: `${ms}ms` }) as React.CSSProperties;
