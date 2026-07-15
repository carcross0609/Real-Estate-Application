"use client";

/**
 * PropertyArt — deterministic architectural elevation rendered per property, used where
 * listing photos can't be displayed (MLS photo licensing is per-market; PRD §31 A1).
 * Seeded by property id so every surface draws the identical elevation. Blueprint-style
 * line work over a dusk gradient — a designed state, never a gray box (§21 #9).
 */

import * as React from "react";
import { cn } from "@/lib/utils";

function seededRng(seed: string) {
  let h = 1779033703 ^ seed.length;
  for (let i = 0; i < seed.length; i++) {
    h = Math.imul(h ^ seed.charCodeAt(i), 3432918353);
    h = (h << 13) | (h >>> 19);
  }
  return () => {
    h = Math.imul(h ^ (h >>> 16), 2246822507);
    h = Math.imul(h ^ (h >>> 13), 3266489909);
    h ^= h >>> 16;
    return (h >>> 0) / 4294967296;
  };
}

export function PropertyArt({
  seed,
  className,
  overlay,
}: {
  seed: string;
  className?: string;
  overlay?: React.ReactNode;
}) {
  const rnd = React.useMemo(() => {
    const r = seededRng(seed);
    const stories = r() > 0.55 ? 2 : 1;
    return {
      hue: 216 + Math.floor(r() * 28), // dusk blues → violets
      stories,
      houseW: 46 + Math.floor(r() * 22),
      roofPeak: 8 + Math.floor(r() * 9),
      hasGarage: r() > 0.45,
      hasPorch: r() > 0.5,
      windows: 2 + Math.floor(r() * 2),
      treeLeft: r() > 0.4,
      treeRight: r() > 0.55,
      chimney: r() > 0.5,
      moon: r() > 0.6,
      moonX: 18 + r() * 30,
    };
  }, [seed]);

  const H = 100;
  const W = 160;
  const groundY = 78;
  const bodyH = rnd.stories === 2 ? 38 : 26;
  const bodyY = groundY - bodyH;
  const cx = rnd.hasGarage ? 62 : 80;
  const x0 = cx - rnd.houseW / 2;
  const x1 = cx + rnd.houseW / 2;
  const line = `hsl(${rnd.hue} 45% 78% / 0.75)`;
  const lineSoft = `hsl(${rnd.hue} 40% 72% / 0.35)`;
  const gid = React.useId();

  const windowRow = (y: number) => {
    const cells: React.ReactNode[] = [];
    const n = rnd.windows;
    const gap = rnd.houseW / (n + 1);
    for (let i = 1; i <= n; i++) {
      cells.push(
        <rect
          key={`${y}-${i}`}
          x={x0 + gap * i - 3.5}
          y={y}
          width={7}
          height={8}
          fill={`hsl(${rnd.hue} 70% 65% / 0.16)`}
          stroke={line}
          strokeWidth="0.8"
        />,
      );
    }
    return cells;
  };

  return (
    <div className={cn("relative overflow-hidden bg-panel", className)}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="xMidYMid slice"
        className="absolute inset-0 size-full"
        aria-hidden
      >
        <defs>
          <linearGradient id={`sky-${gid}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={`hsl(${rnd.hue} 42% 13%)`} />
            <stop offset="70%" stopColor={`hsl(${rnd.hue + 8} 38% 19%)`} />
            <stop offset="100%" stopColor={`hsl(${rnd.hue + 14} 34% 24%)`} />
          </linearGradient>
        </defs>
        <rect width={W} height={H} fill={`url(#sky-${gid})`} />

        {rnd.moon ? (
          <circle cx={rnd.moonX} cy={20} r={6} fill={`hsl(${rnd.hue} 60% 80% / 0.5)`} />
        ) : null}

        {/* survey grid */}
        {[0.25, 0.5, 0.75].map((f) => (
          <line key={f} x1={0} y1={H * f} x2={W} y2={H * f} stroke={lineSoft} strokeWidth="0.3" strokeDasharray="1.5 4" />
        ))}

        {/* ground */}
        <line x1={0} y1={groundY} x2={W} y2={groundY} stroke={line} strokeWidth="1" />
        <line x1={0} y1={groundY + 7} x2={W} y2={groundY + 7} stroke={lineSoft} strokeWidth="0.5" strokeDasharray="3 5" />

        {/* house body */}
        <rect x={x0} y={bodyY} width={rnd.houseW} height={bodyH} fill={`hsl(${rnd.hue} 45% 30% / 0.25)`} stroke={line} strokeWidth="1.1" />
        {/* roof */}
        <polygon
          points={`${x0 - 4},${bodyY} ${cx},${bodyY - rnd.roofPeak} ${x1 + 4},${bodyY}`}
          fill={`hsl(${rnd.hue} 45% 40% / 0.28)`}
          stroke={line}
          strokeWidth="1.1"
          strokeLinejoin="round"
        />
        {rnd.chimney ? (
          <rect x={cx + rnd.houseW / 4} y={bodyY - rnd.roofPeak + 1} width={4} height={rnd.roofPeak - 2} fill="none" stroke={line} strokeWidth="0.9" />
        ) : null}

        {/* windows + door */}
        {rnd.stories === 2 ? windowRow(bodyY + 5) : null}
        {windowRow(groundY - 16)}
        <rect x={cx - 4} y={groundY - 12} width={8} height={12} fill={`hsl(${rnd.hue} 60% 60% / 0.2)`} stroke={line} strokeWidth="0.9" />

        {rnd.hasPorch ? (
          <line x1={x0 - 7} y1={groundY - 10} x2={x1 + 7} y2={groundY - 10} stroke={lineSoft} strokeWidth="0.8" />
        ) : null}

        {/* garage */}
        {rnd.hasGarage ? (
          <g>
            <rect x={x1} y={groundY - 18} width={26} height={18} fill={`hsl(${rnd.hue} 45% 27% / 0.22)`} stroke={line} strokeWidth="1" />
            <polygon points={`${x1},${groundY - 18} ${x1 + 13},${groundY - 24} ${x1 + 26},${groundY - 18}`} fill="none" stroke={line} strokeWidth="1" strokeLinejoin="round" />
            <rect x={x1 + 5} y={groundY - 13} width={16} height={13} fill="none" stroke={lineSoft} strokeWidth="0.8" />
            <line x1={x1 + 5} y1={groundY - 9} x2={x1 + 21} y2={groundY - 9} stroke={lineSoft} strokeWidth="0.6" />
            <line x1={x1 + 5} y1={groundY - 5} x2={x1 + 21} y2={groundY - 5} stroke={lineSoft} strokeWidth="0.6" />
          </g>
        ) : null}

        {/* trees */}
        {rnd.treeLeft ? (
          <g>
            <line x1={x0 - 18} y1={groundY} x2={x0 - 18} y2={groundY - 12} stroke={line} strokeWidth="0.9" />
            <circle cx={x0 - 18} cy={groundY - 17} r={6.5} fill={`hsl(${rnd.hue} 40% 45% / 0.18)`} stroke={line} strokeWidth="0.8" />
          </g>
        ) : null}
        {rnd.treeRight ? (
          <g>
            <line x1={W - 16} y1={groundY} x2={W - 16} y2={groundY - 14} stroke={line} strokeWidth="0.9" />
            <circle cx={W - 16} cy={groundY - 20} r={7.5} fill={`hsl(${rnd.hue} 40% 45% / 0.18)`} stroke={line} strokeWidth="0.8" />
          </g>
        ) : null}

        {/* corner registration marks — the "instrument" framing */}
        {(
          [
            [5, 5, 1, 1],
            [W - 5, 5, -1, 1],
            [5, H - 5, 1, -1],
            [W - 5, H - 5, -1, -1],
          ] as const
        ).map(([x, y, dx, dy], i) => (
          <path key={i} d={`M${x + dx * 5},${y} L${x},${y} L${x},${y + dy * 5}`} fill="none" stroke={lineSoft} strokeWidth="0.7" />
        ))}
      </svg>
      {overlay}
    </div>
  );
}
