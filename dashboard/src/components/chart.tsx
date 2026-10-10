"use client";

import { useId, useMemo, useState } from "react";

import { hhmm } from "./format";

export type Point = { t: number; v: number };
export type Reference = { y: number; label: string };

type Props = {
  title: string;
  data: Point[];
  kind?: "line" | "bar";
  yMax?: number;
  references?: Reference[];
  /** decimals shown in labels and tooltips (props must stay serialisable) */
  decimals?: number;
  height?: number;
};

const W = 640;
const PAD = { top: 12, right: 12, bottom: 22, left: 40 };

const time = hhmm;

/** Single-series time chart: 2px line or thin bars, recessive grid, crosshair tooltip.
 * A single series needs no legend; the title names it. */
export function TimeSeriesChart({
  title,
  data,
  kind = "line",
  yMax,
  references = [],
  decimals = 0,
  height = 180,
}: Props) {
  const format = (v: number) => v.toFixed(decimals);
  const id = useId();
  const [hover, setHover] = useState<number | null>(null);
  const H = height;
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;

  const { x, y, ticks, t0, t1 } = useMemo(() => {
    const t0 = data.length ? data[0].t : 0;
    const t1 = data.length > 1 ? data[data.length - 1].t : t0 + 1;
    const top =
      yMax ?? Math.max(1, ...data.map((d) => d.v), ...references.map((r) => r.y)) * 1.1;
    const x = (t: number) => PAD.left + ((t - t0) / (t1 - t0 || 1)) * innerW;
    const y = (v: number) => PAD.top + innerH - (Math.min(v, top) / top) * innerH;
    const ticks = [0, top / 2, top];
    return { x, y, ticks, t0, t1 };
  }, [data, yMax, references, innerW, innerH]);

  if (data.length === 0) {
    return (
      <figure className="card p-4">
        <figcaption className="text-sm font-medium">{title}</figcaption>
        <p className="mt-6 mb-6 text-center text-sm text-zinc-500">No data yet</p>
      </figure>
    );
  }

  const barW = Math.max(2, Math.min(14, innerW / data.length - 2));
  const path = data.map((d, i) => `${i ? "L" : "M"}${x(d.t).toFixed(1)},${y(d.v).toFixed(1)}`).join("");

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const tx = t0 + ((e.clientX - rect.left) / rect.width) * (t1 - t0);
    let best = 0;
    for (let i = 1; i < data.length; i++) {
      if (Math.abs(data[i].t - tx) < Math.abs(data[best].t - tx)) best = i;
    }
    setHover(best);
  };

  const h = hover === null ? null : data[hover];
  return (
    <figure className="card p-4" aria-labelledby={`${id}-title`}>
      <figcaption id={`${id}-title`} className="flex items-baseline justify-between text-sm">
        <span className="font-medium">{title}</span>
        <span className="tabular-nums text-zinc-500">
          {h ? `${time(h.t)} · ${format(h.v)}` : `latest ${format(data[data.length - 1].v)}`}
        </span>
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} className="mt-2 w-full" role="img" aria-label={title}>
        {ticks.map((v) => (
          <g key={v}>
            <line
              x1={PAD.left}
              x2={W - PAD.right}
              y1={y(v)}
              y2={y(v)}
              className="stroke-zinc-200 dark:stroke-zinc-800"
              strokeWidth={1}
            />
            <text
              x={PAD.left - 6}
              y={y(v) + 4}
              textAnchor="end"
              className="fill-zinc-500 text-[11px]"
            >
              {format(v)}
            </text>
          </g>
        ))}
        {references.map((r) => (
          <g key={r.label}>
            <line
              x1={PAD.left}
              x2={W - PAD.right}
              y1={y(r.y)}
              y2={y(r.y)}
              className="stroke-zinc-400 dark:stroke-zinc-500"
              strokeDasharray="4 4"
              strokeWidth={1}
            />
            <text
              x={W - PAD.right}
              y={y(r.y) - 4}
              textAnchor="end"
              className="fill-zinc-600 text-[11px] dark:fill-zinc-400"
            >
              {r.label}
            </text>
          </g>
        ))}
        {kind === "line" ? (
          <path d={path} fill="none" strokeWidth={2} strokeLinejoin="round" className="series" />
        ) : (
          data.map((d, i) => (
            <rect
              key={d.t}
              x={x(d.t) - barW / 2}
              y={y(d.v)}
              width={barW}
              height={Math.max(0, y(0) - y(d.v))}
              rx={Math.min(2, barW / 2)}
              className="series-fill"
              opacity={hover === null || hover === i ? 1 : 0.55}
            />
          ))
        )}
        <text x={PAD.left} y={H - 4} className="fill-zinc-500 text-[11px]">
          {time(t0)}
        </text>
        <text x={W - PAD.right} y={H - 4} textAnchor="end" className="fill-zinc-500 text-[11px]">
          {time(t1)} UTC
        </text>
        {h && (
          <g pointerEvents="none">
            <line
              x1={x(h.t)}
              x2={x(h.t)}
              y1={PAD.top}
              y2={PAD.top + innerH}
              className="stroke-zinc-400"
              strokeWidth={1}
            />
            {kind === "line" && (
              <circle cx={x(h.t)} cy={y(h.v)} r={4} strokeWidth={2} className="series-dot" />
            )}
          </g>
        )}
        <rect
          x={PAD.left}
          y={PAD.top}
          width={innerW}
          height={innerH}
          fill="transparent"
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
        />
      </svg>
      <details className="mt-1 text-xs text-zinc-500">
        <summary className="cursor-pointer">Table view</summary>
        <table className="mt-1 w-full tabular-nums">
          <tbody>
            {data
              .slice(-30)
              .reverse()
              .map((d) => (
                <tr key={d.t}>
                  <td>{time(d.t)}</td>
                  <td className="text-right">{format(d.v)}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}
