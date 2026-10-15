import { describe, expect, it } from "vitest";
import type { Line } from "../shared/types";
import { cursorAt, lastStartedLine, wordProgress } from "./lyrics-timing";

const word = (text: string, start_ms: number, end_ms: number) => ({ text, start_ms, end_ms, estimated: true });
const lines: Line[] = [
  { start_ms: 1000, end_ms: 3000, words: [word("uno", 1000, 2000), word("dos", 2000, 3000)] },
  { start_ms: 3000, end_ms: 5000, words: [word("tres", 3000, 5000)] },
  { start_ms: 15000, end_ms: 17000, words: [word("cuatro", 15000, 17000)] },
];

describe("lyrics timing", () => {
  it("finds the last started line", () => {
    expect(lastStartedLine(lines, 0)).toBe(-1);
    expect(lastStartedLine(lines, 1000)).toBe(0);
    expect(lastStartedLine(lines, 4000)).toBe(1);
    expect(lastStartedLine(lines, 99999)).toBe(2);
  });

  it("shows the upcoming line before the song starts", () => {
    expect(cursorAt(lines, 0)).toEqual({ current: 0, active: false, countdown: 0 });
  });

  it("tracks the active line and switches at the next start", () => {
    expect(cursorAt(lines, 2500)).toMatchObject({ current: 0, active: true });
    expect(cursorAt(lines, 3000)).toMatchObject({ current: 1, active: true });
  });

  it("counts down after a long gap", () => {
    expect(cursorAt(lines, 8000)).toEqual({ current: 2, active: false, countdown: 0 });
    expect(cursorAt(lines, 12500)).toEqual({ current: 2, active: false, countdown: 3 });
    expect(cursorAt(lines, 14500)).toEqual({ current: 2, active: false, countdown: 1 });
  });

  it("ends after the last line", () => {
    expect(cursorAt(lines, 30000).current).toBe(-1);
  });

  it("fills words progressively", () => {
    const w = word("x", 1000, 2000);
    expect(wordProgress(w, 500)).toBe(0);
    expect(wordProgress(w, 1500)).toBe(0.5);
    expect(wordProgress(w, 2500)).toBe(1);
  });
});
