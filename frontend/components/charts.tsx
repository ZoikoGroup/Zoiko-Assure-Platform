"use client";

import { useId } from "react";
import { delay } from "@/lib/motion";

/* Lightweight, dependency-free SVG charts in the brand palette. Each chart also renders an
   sr-only data table so values are never conveyed by shape or colour alone. */

const NAVY = "#154a6c";
const ORANGE = "#d9822b";
const GRID = "#e3ebf1";
const AXIS = "#7a8a93";

const niceMax = (v: number) => {
  if (v <= 4) return 4;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  return Math.ceil(v / p) * p;
};

function Grid({ w, h, pad, max, ticks = 4 }: { w: number; h: number; pad: { l: number; r: number; t: number; b: number }; max: number; ticks?: number }) {
  return (
    <g>
      {Array.from({ length: ticks + 1 }, (_, i) => {
        const v = (max / ticks) * i;
        const y = h - pad.b - ((h - pad.t - pad.b) * i) / ticks;
        return (
          <g key={i}>
            <line x1={pad.l} x2={w - pad.r} y1={y} y2={y} stroke={GRID} strokeDasharray={i ? "4 4" : undefined} />
            <text x={pad.l - 8} y={y + 4} textAnchor="end" fontSize="11" fill={AXIS}>{Number.isInteger(v) ? v : v.toFixed(1)}</text>
          </g>
        );
      })}
    </g>
  );
}

export function BarChart({ data, color = NAVY, label }: { data: { x: string; y: number }[]; color?: string; label: string }) {
  const w = 420, h = 200, pad = { l: 34, r: 8, t: 10, b: 26 };
  const max = niceMax(Math.max(1, ...data.map((d) => d.y)));
  const slot = (w - pad.l - pad.r) / data.length;
  const bw = Math.min(34, slot * 0.55);
  const gid = `bar${useId().replace(/:/g, "")}`;
  return (
    <figure>
      <svg viewBox={`0 0 ${w} ${h}`} className="h-auto w-full" role="img" aria-label={label}>
        <defs>
          <linearGradient id={gid} x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor={color} /><stop offset="1" stopColor={color} stopOpacity="0.65" /></linearGradient>
        </defs>
        <Grid w={w} h={h} pad={pad} max={max} />
        {data.map((d, i) => {
          const bh = ((h - pad.t - pad.b) * d.y) / max;
          const x = pad.l + slot * i + (slot - bw) / 2;
          return (
            <g key={d.x}>
              <rect x={x} y={h - pad.b - bh} width={bw} height={Math.max(bh, 0)} rx="6" fill={`url(#${gid})`} className="za-grow-y" style={delay(250 + i * 90)}><title>{`${d.x}: ${d.y}`}</title></rect>
              {d.y > 0 && <text x={x + bw / 2} y={h - pad.b - bh - 5} textAnchor="middle" fontSize="11" fontWeight="600" fill={NAVY} className="za-fade" style={delay(800 + i * 90)}>{d.y}</text>}
              <text x={x + bw / 2} y={h - 8} textAnchor="middle" fontSize="11" fill={AXIS}>{d.x}</text>
            </g>
          );
        })}
      </svg>
      <SrTable label={label} rows={data.map((d) => [d.x, String(d.y)])} />
    </figure>
  );
}

export function LineChart({ labels, series, label }: { labels: string[]; series: { name: string; color: string; values: number[] }[]; label: string }) {
  const w = 420, h = 200, pad = { l: 34, r: 12, t: 12, b: 26 };
  const max = niceMax(Math.max(1, ...series.flatMap((s) => s.values)));
  const x = (i: number) => pad.l + ((w - pad.l - pad.r) * i) / Math.max(1, labels.length - 1);
  const y = (v: number) => h - pad.b - ((h - pad.t - pad.b) * v) / max;
  return (
    <figure>
      <svg viewBox={`0 0 ${w} ${h}`} className="h-auto w-full" role="img" aria-label={label}>
        <Grid w={w} h={h} pad={pad} max={max} />
        {series.map((s, si) => {
          const pts = s.values.map((v, i) => `${x(i)},${y(v)}`).join(" ");
          return (
            <g key={s.name}>
              <polygon points={`${x(0)},${y(0)} ${pts} ${x(s.values.length - 1)},${y(0)}`} fill={s.color} opacity="0.08" className="za-fade" style={delay(900 + si * 200)} />
              <polyline points={pts} pathLength={1} fill="none" stroke={s.color} strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" className="za-draw" style={delay(250 + si * 200)} />
              {s.values.map((v, i) => <circle key={i} cx={x(i)} cy={y(v)} r="4" fill="white" stroke={s.color} strokeWidth="2" className="za-pop" style={delay(350 + si * 200 + (i * 1100) / Math.max(1, s.values.length))}><title>{`${s.name} ${labels[i]}: ${v}`}</title></circle>)}
            </g>
          );
        })}
        {labels.map((l, i) => <text key={l} x={x(i)} y={h - 8} textAnchor="middle" fontSize="11" fill={AXIS}>{l}</text>)}
      </svg>
      <div className="mt-1 flex gap-4 text-[11px] text-slate-600">
        {series.map((s) => <span key={s.name} className="flex items-center gap-1.5"><span className="h-2 w-4 rounded-full" style={{ background: s.color }} />{s.name}</span>)}
      </div>
      <SrTable label={label} rows={labels.map((l, i) => [l, series.map((s) => `${s.name} ${s.values[i]}`).join(", ")])} />
    </figure>
  );
}

export function Donut({ parts, label, center }: { parts: { name: string; value: number; color: string }[]; label: string; center?: React.ReactNode }) {
  const total = parts.reduce((a, p) => a + p.value, 0) || 1;
  const r = 60, c = 2 * Math.PI * r;
  const mid = `donut${useId().replace(/:/g, "")}`;
  let off = 0;
  return (
    <figure className="flex flex-wrap items-center gap-6">
      <div className="relative">
        <svg viewBox="0 0 160 160" className="h-40 w-40 -rotate-90" role="img" aria-label={label}>
          <defs>
            <mask id={mid}>
              <circle cx="80" cy="80" r={r} fill="none" stroke="white" strokeWidth="20" strokeDasharray={c}
                className="za-sweep" style={{ ...delay(300), ["--za-c" as string]: `${c}` } as React.CSSProperties} />
            </mask>
          </defs>
          <circle cx="80" cy="80" r={r} fill="none" stroke={GRID} strokeWidth="18" />
          <g mask={`url(#${mid})`}>
          {parts.map((p) => {
            const len = (p.value / total) * c;
            const el = <circle key={p.name} cx="80" cy="80" r={r} fill="none" stroke={p.color} strokeWidth="18" strokeDasharray={`${len} ${c - len}`} strokeDashoffset={-off}><title>{`${p.name}: ${p.value}`}</title></circle>;
            off += len;
            return el;
          })}
          </g>
        </svg>
        <div className="za-fade absolute inset-0 flex flex-col items-center justify-center text-center" style={delay(700)}>{center}</div>
      </div>
      <ul className="space-y-2 text-sm">
        {parts.map((p, i) => (
          <li key={p.name} className="za-slide-in flex items-center gap-2" style={delay(600 + i * 120)}>
            <span className="h-3 w-3 rounded-full" style={{ background: p.color }} />
            <span className="text-slate-700">{p.name}</span>
            <b className="ml-auto pl-4 text-navy-900">{p.value}</b>
          </li>
        ))}
      </ul>
      <SrTable label={label} rows={parts.map((p) => [p.name, String(p.value)])} />
    </figure>
  );
}

function SrTable({ label, rows }: { label: string; rows: string[][] }) {
  return (
    <table className="sr-only">
      <caption>{label}</caption>
      <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody>
    </table>
  );
}

export const CHART = { NAVY, ORANGE };
