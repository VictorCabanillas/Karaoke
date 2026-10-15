import { h } from "../shared/dom";
import type { Line, Lyrics } from "../shared/types";
import { cursorAt, wordProgress } from "./lyrics-timing";

/** Two-row karaoke display: current line filling word by word + next line preview. */
export class LyricsView {
  readonly el: HTMLElement;
  private currentEl = h("div", { class: "line current" });
  private nextEl = h("div", { class: "line next" });
  private countdownEl = h("div", { class: "countdown" });
  private shown = -2;
  private wordEls: HTMLElement[] = [];

  constructor(private lyrics: Lyrics | null) {
    if (!lyrics || lyrics.lines.length === 0) {
      this.el = h("div", { class: "lyrics empty" }, h("p", null, "Esta canción no tiene letra."));
    } else if (!lyrics.synced) {
      this.el = h(
        "div",
        { class: "lyrics plain" },
        h("p", { class: "hint" }, "Letra sin sincronizar"),
        ...lyrics.lines.map((line) => h("p", null, lineText(line))),
      );
    } else {
      this.el = h("div", { class: "lyrics synced" }, this.countdownEl, this.currentEl, this.nextEl);
    }
  }

  get lines(): Line[] {
    return this.lyrics?.lines ?? [];
  }

  /** t = song position in lyrics time (position minus manual offset), ms. */
  update(t: number): void {
    if (!this.lyrics?.synced) return;
    const lines = this.lyrics.lines;
    const cursor = cursorAt(lines, t);
    if (cursor.current !== this.shown) {
      this.shown = cursor.current;
      this.render(cursor.current);
    }
    this.countdownEl.textContent = cursor.countdown ? "●".repeat(cursor.countdown) : "";
    if (cursor.current < 0) return;
    const words = lines[cursor.current].words;
    for (let i = 0; i < words.length; i++) {
      const p = cursor.active ? wordProgress(words[i], t) : 0;
      this.wordEls[i].style.setProperty("--p", `${(p * 100).toFixed(1)}%`);
    }
  }

  private render(index: number): void {
    const lines = this.lyrics!.lines;
    if (index < 0) {
      this.currentEl.replaceChildren();
      this.nextEl.replaceChildren();
      this.wordEls = [];
      return;
    }
    this.wordEls = lines[index].words.map((w) => h("span", { class: "word" }, w.text));
    const parts: (HTMLElement | string)[] = [];
    this.wordEls.forEach((el, i) => {
      if (i > 0) parts.push(" ");
      parts.push(el);
    });
    this.currentEl.replaceChildren(...parts);
    this.nextEl.textContent = index + 1 < lines.length ? lineText(lines[index + 1]) : "";
  }
}

export function lineText(line: Line): string {
  return line.words.map((w) => w.text).join(" ");
}
