import type { Line, Lyrics, Word } from "../shared/types";

/** One row of the lyrics editor: text plus start time (null = not synced yet). */
export interface EditRow {
  start: number | null;
  text: string;
  /** The line as it was loaded, to keep its word timings when possible. */
  orig: Line | null;
}

// Same pacing rules as the LRC parser on the server (processing/lrc.py).
const MS_PER_SYLLABLE_CAP = 600;
const LINE_TAIL_MS = 1500;
const LAST_LINE_MS_PER_SYLLABLE = 350;
const VOWEL_GROUPS = /[aeiouyáéíóúàèìòùâêîôûäëïöüåæøœ]+/gi;

export function countSyllables(word: string): number {
  return Math.max(1, word.match(VOWEL_GROUPS)?.length ?? 0);
}

/** Split a line into words sharing its duration by syllable count. */
export function interpolateWords(text: string, start: number, end: number): Word[] {
  const tokens = text.split(/\s+/).filter(Boolean);
  const weights = tokens.map(countSyllables);
  const total = weights.reduce((a, b) => a + b, 0) || 1;
  const span = Math.max(0, end - start);
  let acc = 0;
  return tokens.map((token, i) => {
    const s = start + Math.round((span * acc) / total);
    acc += weights[i];
    return { text: token, start_ms: s, end_ms: start + Math.round((span * acc) / total), estimated: true };
  });
}

export function lineText(line: Line): string {
  return line.words.map((w) => w.text).join(" ");
}

export function rowsFromLyrics(lyrics: Lyrics | null): EditRow[] {
  if (!lyrics || lyrics.lines.length === 0) return [{ start: null, text: "", orig: null }];
  return lyrics.lines.map((line) => ({
    start: lyrics.synced ? line.start_ms : null,
    text: lineText(line),
    orig: lyrics.synced ? line : null,
  }));
}

export function rowsFromText(text: string): EditRow[] {
  const rows = text
    .split(/\r?\n/)
    .map((t) => t.trim())
    .filter(Boolean)
    .map((t) => ({ start: null, text: t, orig: null }));
  return rows.length ? rows : [{ start: null, text: "", orig: null }];
}

/** Word timings for an edited line: reuse the old ones when the line keeps its word count. */
function wordsFor(row: EditRow, start: number, end: number): Word[] {
  const tokens = row.text.split(/\s+/).filter(Boolean);
  const orig = row.orig;
  if (!orig || orig.words.length !== tokens.length || orig.end_ms <= orig.start_ms) {
    return interpolateWords(row.text, start, end);
  }
  const scale = (end - start) / (orig.end_ms - orig.start_ms);
  const at = (t: number) => Math.round(start + (t - orig.start_ms) * scale);
  return orig.words.map((w, i) => ({
    text: tokens[i],
    start_ms: at(w.start_ms),
    end_ms: at(w.end_ms),
    estimated: w.estimated,
  }));
}

export type BuildResult = { ok: true; lyrics: Lyrics } | { ok: false; row: number; message: string };

/** Turn the editor rows into lyrics (synced if every row has a start, unsynced if none has). */
export function buildLyrics(rows: EditRow[], durationMs: number): BuildResult {
  const filled = rows.map((row, index) => ({ row, index })).filter(({ row }) => row.text.trim());
  if (filled.length === 0) return { ok: false, row: 0, message: "La letra está vacía" };

  const timed = filled.filter(({ row }) => row.start !== null).length;
  if (timed === 0) {
    const lines = filled.map(({ row }) => ({ start_ms: 0, end_ms: 0, words: interpolateWords(row.text, 0, 0) }));
    return { ok: true, lyrics: { source: "manual", synced: false, lines } };
  }
  const missing = filled.find(({ row }) => row.start === null);
  if (missing) {
    return { ok: false, row: missing.index, message: "A esta línea le falta el tiempo de inicio" };
  }
  for (let i = 1; i < filled.length; i++) {
    if (filled[i].row.start! < filled[i - 1].row.start!) {
      return { ok: false, row: filled[i].index, message: "Esta línea empieza antes que la anterior" };
    }
  }

  const lines: Line[] = filled.map(({ row }, i) => {
    const start = row.start!;
    const syllables = row.text.split(/\s+/).filter(Boolean).reduce((n, w) => n + countSyllables(w), 0);
    const next = i + 1 < filled.length ? filled[i + 1].row.start! : null;
    let end =
      next !== null
        ? Math.min(next, start + syllables * MS_PER_SYLLABLE_CAP + LINE_TAIL_MS)
        : Math.min(durationMs || Infinity, start + syllables * LAST_LINE_MS_PER_SYLLABLE + LINE_TAIL_MS);
    // A line whose start did not move keeps its original end (if it still fits before the next one).
    if (row.orig && row.orig.start_ms === start && (next === null || row.orig.end_ms <= next)) end = row.orig.end_ms;
    end = Math.max(end, start + 1);
    return { start_ms: start, end_ms: end, words: wordsFor(row, start, end) };
  });
  return { ok: true, lyrics: { source: "manual", synced: true, lines } };
}
