import type { Line, Word } from "../shared/types";

/** Keep a sung line on screen this long after it ends before switching to the next one. */
export const LINE_HOLD_MS = 400;
/** Show a countdown before a line when the silence before it is at least this long. */
export const COUNTDOWN_GAP_MS = 4000;
export const COUNTDOWN_MS = 3000;

export interface LyricsCursor {
  /** Line shown in the main slot (being sung or coming next), -1 if none. */
  current: number;
  /** True while `current` is actually being sung (otherwise it is upcoming). */
  active: boolean;
  /** Seconds-to-go countdown (3, 2, 1) shown before a line after a long silence; 0 = none. */
  countdown: number;
}

/** Index of the last line that started at or before t, or -1. Lines are sorted by start. */
export function lastStartedLine(lines: Line[], t: number): number {
  let lo = 0;
  let hi = lines.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (lines[mid].start_ms <= t) {
      found = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return found;
}

export function cursorAt(lines: Line[], t: number): LyricsCursor {
  const idx = lastStartedLine(lines, t);
  if (idx >= 0) {
    const line = lines[idx];
    const nextStart = idx + 1 < lines.length ? lines[idx + 1].start_ms : Infinity;
    if (t <= line.end_ms + LINE_HOLD_MS && t < nextStart) return { current: idx, active: true, countdown: 0 };
  }
  const upcoming = idx + 1;
  if (upcoming >= lines.length) return { current: -1, active: false, countdown: 0 };
  const prevEnd = idx >= 0 ? lines[idx].end_ms : 0;
  const start = lines[upcoming].start_ms;
  const remaining = start - t;
  const countdown = start - prevEnd >= COUNTDOWN_GAP_MS && remaining <= COUNTDOWN_MS ? Math.ceil(remaining / 1000) : 0;
  return { current: upcoming, active: false, countdown };
}

/** 0..1 fill of a word at time t. */
export function wordProgress(word: Word, t: number): number {
  if (t <= word.start_ms) return 0;
  if (t >= word.end_ms) return 1;
  return (t - word.start_ms) / Math.max(1, word.end_ms - word.start_ms);
}
