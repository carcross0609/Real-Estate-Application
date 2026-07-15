"use client";

/**
 * GradeRing — the product's visual signature. A circular gauge whose arc is the 0–100
 * score and whose center is the letter grade; color + letter always travel together
 * (PRD §21 #6). Used identically on cards, tables, map pins, report headers.
 */

import * as React from "react";
import { GRADE_VAR, gradeFamily } from "@/lib/domain";
import { cn } from "@/lib/utils";

const SIZES = {
  xs: { box: 24, stroke: 2.5, font: 9 },
  sm: { box: 32, stroke: 3, font: 11 },
  md: { box: 44, stroke: 3.5, font: 14 },
  lg: { box: 64, stroke: 4, font: 20 },
  xl: { box: 92, stroke: 5, font: 28 },
} as const;

export function GradeRing({
  score,
  grade,
  size = "md",
  className,
  animate = true,
}: {
  score: number;
  grade: string;
  size?: keyof typeof SIZES;
  className?: string;
  animate?: boolean;
}) {
  const s = SIZES[size];
  const r = (s.box - s.stroke) / 2;
  const c = 2 * Math.PI * r;
  const filled = Math.max(0, Math.min(100, score)) / 100;
  const color = GRADE_VAR[gradeFamily(grade)];

  return (
    <div
      className={cn("relative inline-flex shrink-0 items-center justify-center", className)}
      style={{ width: s.box, height: s.box }}
      role="img"
      aria-label={`Score ${Math.round(score)} of 100, grade ${grade}`}
    >
      <svg width={s.box} height={s.box} viewBox={`0 0 ${s.box} ${s.box}`} className="-rotate-90">
        <circle
          cx={s.box / 2}
          cy={s.box / 2}
          r={r}
          fill="none"
          stroke="var(--raise)"
          strokeWidth={s.stroke}
        />
        <circle
          cx={s.box / 2}
          cy={s.box / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={s.stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - filled)}
          className={animate ? "transition-[stroke-dashoffset] duration-700 ease-[var(--ease-swift)]" : undefined}
        />
      </svg>
      <span
        className="figure absolute font-semibold"
        style={{ color, fontSize: s.font, letterSpacing: "-0.02em" }}
      >
        {grade}
      </span>
    </div>
  );
}

/** Compact inline "82 A-" score chip for table cells and dense rows. */
export function ScoreChip({
  score,
  grade,
  className,
}: {
  score: number;
  grade: string;
  className?: string;
}) {
  const fam = gradeFamily(grade);
  return (
    <span
      className={cn(
        "figure inline-flex items-center gap-1.5 rounded-sm px-1.5 py-0.5 text-xs font-semibold",
        className,
      )}
      style={{ background: `var(--grade-${fam}-soft)`, color: `var(--grade-${fam})` }}
    >
      {Math.round(score)}
      <span className="opacity-85">{grade}</span>
    </span>
  );
}
