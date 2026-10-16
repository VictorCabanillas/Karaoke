import { describe, expect, it } from "vitest";
import type { Line } from "../shared/types";
import { buildLyrics, countSyllables, interpolateWords, rowsFromLyrics, rowsFromText } from "./lyrics-edit";

const exactLine: Line = {
  start_ms: 1000,
  end_ms: 2000,
  words: [
    { text: "hola", start_ms: 1000, end_ms: 1300, estimated: false },
    { text: "mundo", start_ms: 1500, end_ms: 2000, estimated: false },
  ],
};

describe("lyrics editor model", () => {
  it("counts syllables like the server", () => {
    expect(countSyllables("luna")).toBe(2);
    expect(countSyllables("balcón")).toBe(2);
    expect(countSyllables("...")).toBe(1);
  });

  it("interpolates words by syllables", () => {
    const words = interpolateWords("la mariposa", 0, 1000);
    expect(words.map((w) => [w.start_ms, w.end_ms])).toEqual([
      [0, 200],
      [200, 1000],
    ]);
  });

  it("keeps exact word timings when only a word's spelling changes", () => {
    const rows = rowsFromLyrics({ source: "whisper", synced: true, lines: [exactLine] });
    rows[0].text = "hola mundial";
    const result = buildLyrics(rows, 10_000);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const line = result.lyrics.lines[0];
    expect(line).toMatchObject({ start_ms: 1000, end_ms: 2000 });
    expect(line.words).toEqual([
      { text: "hola", start_ms: 1000, end_ms: 1300, estimated: false },
      { text: "mundial", start_ms: 1500, end_ms: 2000, estimated: false },
    ]);
    expect(result.lyrics.source).toBe("manual");
  });

  it("rescales word timings when a line is moved", () => {
    const rows = rowsFromLyrics({ source: "whisper", synced: true, lines: [exactLine] });
    rows[0].start = 1500;
    rows.push({ start: 3500, text: "fin", orig: null });
    const result = buildLyrics(rows, 10_000);
    if (!result.ok) throw new Error(result.message);
    const [first] = result.lyrics.lines;
    expect(first.start_ms).toBe(1500);
    expect(first.words[0].start_ms).toBe(1500);
    expect(first.words[1].end_ms).toBe(first.end_ms);
  });

  it("interpolates when the number of words changes", () => {
    const rows = rowsFromLyrics({ source: "whisper", synced: true, lines: [exactLine] });
    rows[0].text = "hola a todo el mundo";
    const result = buildLyrics(rows, 10_000);
    if (!result.ok) throw new Error(result.message);
    expect(result.lyrics.lines[0].words).toHaveLength(5);
    expect(result.lyrics.lines[0].words.every((w) => w.estimated)).toBe(true);
  });

  it("ends a line at the next one and caps long gaps", () => {
    const rows = [
      { start: 0, text: "uno dos", orig: null },
      { start: 1000, text: "tres", orig: null },
      { start: 60_000, text: "cuatro", orig: null },
    ];
    const result = buildLyrics(rows, 70_000);
    if (!result.ok) throw new Error(result.message);
    const ends = result.lyrics.lines.map((l) => l.end_ms);
    expect(ends[0]).toBe(1000);
    expect(ends[1]).toBeLessThan(5000);
    expect(ends[2]).toBeLessThanOrEqual(70_000);
  });

  it("saves pasted text without timings as unsynced lyrics", () => {
    const result = buildLyrics(rowsFromText("primera\n\n segunda linea \n"), 0);
    expect(result).toMatchObject({ ok: true, lyrics: { synced: false } });
    if (result.ok) expect(result.lyrics.lines).toHaveLength(2);
  });

  it("reports rows without time or out of order", () => {
    expect(buildLyrics([{ start: 0, text: "a", orig: null }, { start: null, text: "b", orig: null }], 0)).toMatchObject({
      ok: false,
      row: 1,
    });
    expect(buildLyrics([{ start: 5000, text: "a", orig: null }, { start: 1000, text: "b", orig: null }], 0)).toMatchObject({
      ok: false,
      row: 1,
    });
    expect(buildLyrics([{ start: null, text: "  ", orig: null }], 0)).toMatchObject({ ok: false });
  });
});
