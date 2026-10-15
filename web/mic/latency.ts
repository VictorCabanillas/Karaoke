/**
 * Microphone latency test: the phone shows a steady visual pulse and the player sings "ta" on each beat.
 * Because people anticipate a rhythm (instead of reacting to it), the delay between each beat and the
 * detected voice onset is mostly the microphone's own latency.
 */

export interface LevelSample {
  /** performance.now() time of the analysed window's end. */
  t: number;
  rms: number;
}

export const BEAT_MS = 750;
export const LEAD_IN_BEATS = 3;
export const MEASURED_BEATS = 6;
/** Onsets are matched to a beat if they fall in [beat - EARLY, beat + LATE]. */
const EARLY_MS = 150;
const LATE_MS = 600;
/** Silence needed between two "ta" for a new onset. */
const RELEASE_MS = 80;
/** A frame's time is the end of its ~43 ms window: the onset is on average half a window earlier. */
export const WINDOW_BIAS_MS = 20;
const MIN_MATCHED = 3;
/** Above this spread (median absolute deviation) the measurement is not trusted. */
const MAX_SPREAD_MS = 100;
const MAX_LATENCY_MS = 600;

/** Times where the level jumps above the threshold after a short silence. */
export function detectOnsets(samples: LevelSample[], gate: number): number[] {
  const threshold = Math.max(gate * 3, 0.01);
  const onsets: number[] = [];
  let silentSince: number | null = samples.length ? samples[0].t - RELEASE_MS : null;
  for (const s of samples) {
    if (s.rms >= threshold) {
      if (silentSince !== null && s.t - silentSince >= RELEASE_MS) onsets.push(s.t);
      silentSince = null;
    } else if (s.rms < threshold / 2) {
      silentSince ??= s.t;
    }
  }
  return onsets;
}

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

export type LatencyResult =
  | { ok: true; latencyMs: number; matched: number; spreadMs: number }
  | { ok: false; reason: "few" | "irregular"; matched: number };

export function estimateLatency(beats: number[], onsets: number[]): LatencyResult {
  const delays: number[] = [];
  let next = 0; // each onset belongs to one beat at most
  for (const beat of beats) {
    while (next < onsets.length && onsets[next] < beat - EARLY_MS) next++;
    if (next < onsets.length && onsets[next] <= beat + LATE_MS) delays.push(onsets[next++] - beat - WINDOW_BIAS_MS);
  }
  if (delays.length < MIN_MATCHED) return { ok: false, reason: "few", matched: delays.length };
  const center = median(delays);
  const spread = median(delays.map((d) => Math.abs(d - center)));
  if (spread > MAX_SPREAD_MS) return { ok: false, reason: "irregular", matched: delays.length };
  const latency = Math.round(Math.min(MAX_LATENCY_MS, Math.max(0, center)) / 10) * 10;
  return { ok: true, latencyMs: latency, matched: delays.length, spreadMs: Math.round(spread) };
}
