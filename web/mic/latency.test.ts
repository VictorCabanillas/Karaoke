import { describe, expect, it } from "vitest";
import { BEAT_MS, detectOnsets, estimateLatency, type LevelSample, WINDOW_BIAS_MS } from "./latency";

const HOP = 21;

/** Simulated level trace: "ta" of 250 ms at each beat + delay (plus jitter), background noise otherwise. */
function trace(beats: number[], delay: (i: number) => number | null, noise = 0.002): LevelSample[] {
  const end = beats[beats.length - 1] + 1500;
  const samples: LevelSample[] = [];
  for (let t = beats[0] - 1000; t < end; t += HOP) {
    const loud = beats.some((b, i) => {
      const d = delay(i);
      return d !== null && t >= b + d && t < b + d + 250;
    });
    samples.push({ t, rms: loud ? 0.15 : noise });
  }
  return samples;
}

const beats = Array.from({ length: 6 }, (_, i) => 10_000 + i * BEAT_MS);

describe("latency test", () => {
  it("finds one onset per sung beat", () => {
    expect(detectOnsets(trace(beats, () => 120), 0.005)).toHaveLength(6);
  });

  it("estimates a steady delay", () => {
    const jitter = [0, 15, -10, 5, -5, 10];
    const result = estimateLatency(beats, detectOnsets(trace(beats, (i) => 150 + jitter[i]), 0.005));
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(Math.abs(result.latencyMs - 150)).toBeLessThanOrEqual(HOP + WINDOW_BIAS_MS);
      expect(result.matched).toBe(6);
    }
  });

  it("never goes below zero when the singer anticipates", () => {
    const result = estimateLatency(beats, detectOnsets(trace(beats, () => -60), 0.005));
    expect(result).toMatchObject({ ok: true, latencyMs: 0 });
  });

  it("asks to repeat when too few beats were sung", () => {
    const result = estimateLatency(beats, detectOnsets(trace(beats, (i) => (i < 2 ? 100 : null)), 0.005));
    expect(result).toEqual({ ok: false, reason: "few", matched: 2 });
  });

  it("asks to repeat when the timing is all over the place", () => {
    const delays = [0, 300, 40, 320, 10, 290];
    const result = estimateLatency(beats, detectOnsets(trace(beats, (i) => delays[i]), 0.005));
    expect(result).toMatchObject({ ok: false, reason: "irregular" });
  });

  it("ignores background noise below the gate", () => {
    expect(detectOnsets(trace(beats, () => null, 0.004), 0.005)).toEqual([]);
  });
});
