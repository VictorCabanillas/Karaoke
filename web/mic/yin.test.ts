import { describe, expect, it } from "vitest";
import { hzToMidi, midiToName, Yin } from "./yin";

function tone(freq: number, sampleRate: number, length: number, harmonics = [1]): Float32Array {
  const out = new Float32Array(length);
  for (let i = 0; i < length; i++) {
    let v = 0;
    harmonics.forEach((amp, k) => (v += amp * Math.sin((2 * Math.PI * freq * (k + 1) * i) / sampleRate)));
    out[i] = 0.3 * v;
  }
  return out;
}

describe("Yin", () => {
  const yin = new Yin({ sampleRate: 24000 });

  it.each([82.4, 110, 196, 261.6, 440, 880])("detects %f Hz", (freq) => {
    const result = yin.detect(tone(freq, 24000, 1024));
    expect(result.f0).not.toBeNull();
    expect(Math.abs(hzToMidi(result.f0!) - hzToMidi(freq))).toBeLessThan(0.1);
    expect(result.confidence).toBeGreaterThan(0.85);
  });

  it("handles voice-like harmonics without octave errors", () => {
    const result = yin.detect(tone(220, 24000, 1024, [1, 0.8, 0.6, 0.4, 0.3]));
    expect(Math.abs(hzToMidi(result.f0!) - hzToMidi(220))).toBeLessThan(0.1);
  });

  it("gives noise a low confidence (filtered out downstream)", () => {
    let seed = 1;
    const noise = new Float32Array(1024).map(() => {
      seed = (seed * 16807) % 2147483647;
      return (seed / 2147483647 - 0.5) * 0.5;
    });
    const result = yin.detect(noise);
    expect(result.confidence).toBeLessThan(0.5);
    expect(result.rms).toBeGreaterThan(0);
  });

  it("still finds the pitch of a breathy voice (tone + noise)", () => {
    let seed = 7;
    const breathy = tone(196, 24000, 1024, [1, 0.6, 0.3]).map((v) => {
      seed = (seed * 16807) % 2147483647;
      return v + (seed / 2147483647 - 0.5) * 0.35;
    });
    const result = yin.detect(breathy);
    expect(result.f0).not.toBeNull();
    expect(Math.abs(hzToMidi(result.f0!) - hzToMidi(196))).toBeLessThan(0.5);
    expect(result.confidence).toBeGreaterThan(0.6);
  });

  it("returns no pitch for silence", () => {
    expect(yin.detect(new Float32Array(1024))).toEqual({ f0: null, confidence: 0, rms: 0 });
  });
});

describe("note names", () => {
  it("names notes in solfège", () => {
    expect(midiToName(69)).toBe("La4");
    expect(midiToName(60)).toBe("Do4");
    expect(midiToName(61.2)).toBe("Do#4");
  });
});
