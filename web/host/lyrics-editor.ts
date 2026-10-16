import { api, ApiError } from "../shared/api";
import { h } from "../shared/dom";
import type { Lyrics, Song, SystemInfo } from "../shared/types";
import { AudioEngine } from "./audio";
import { LYRICS_LABELS } from "./library";
import { openDeleteLyrics } from "./lyrics-delete";
import { buildLyrics, type EditRow, rowsFromLyrics, rowsFromText } from "./lyrics-edit";
import type { Playback } from "./playback";
import { SpotifyPlayback } from "./spotify-playback";

const NUDGE_MS = 100;

export function formatPrecise(ms: number | null): string {
  if (ms === null) return "--:--.-";
  const total = Math.max(0, ms) / 1000;
  const m = Math.floor(total / 60);
  const s = total - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
}

/** Play the full mix (with vocals: you need to hear the singer to sync the lyrics). */
async function createPlayback(song: Song): Promise<Playback> {
  if (song.origin === "spotify") return SpotifyPlayback.create(song);
  const engine = new AudioEngine();
  await engine.load("original", `/api/songs/${song.id}/audio/original`);
  return engine;
}

/** Edit the text and line timing of a song's lyrics while listening to it. */
export class LyricsEditor {
  readonly el: HTMLElement;
  private rows: EditRow[] = [];
  private rowEls: HTMLElement[] = [];
  private list = h("div", { class: "edit-rows" });
  private message = h("p", { class: "hint edit-message" });
  private playback: Playback | null = null;
  private playButton = h("button", { class: "play", disabled: true }, "▶");
  private timeLabel = h("span", { class: "time" }, "0:00.0");
  private seek = h("input", { type: "range", min: "0", max: "1000", value: "0", class: "seek" });
  private syncToggle = h("input", { type: "checkbox" });
  private markButton = h("button", { class: "secondary", disabled: true }, "Marcar línea (Enter)");
  private syncCursor = 0;
  private dirty = false;
  private frame = 0;
  private seeking = false;
  private info: SystemInfo | null = null;
  private sourceLabel = h("span", { class: "hint" });

  constructor(
    private song: Song,
    private onExit: () => void,
  ) {
    this.playButton.addEventListener("click", () => this.toggle());
    this.seek.addEventListener("input", () => (this.seeking = true));
    this.seek.addEventListener("change", () => {
      this.seeking = false;
      if (this.playback) this.playback.seek((Number(this.seek.value) / 1000) * this.playback.durationMs());
    });
    this.syncToggle.addEventListener("change", () => this.setSyncMode(this.syncToggle.checked));
    this.markButton.addEventListener("click", () => this.mark());

    this.el = h(
      "section",
      { class: "editor" },
      h(
        "header",
        { class: "play-header" },
        h("button", { class: "secondary", onclick: () => this.exit() }, "← Biblioteca"),
        h("div", { class: "title" }, h("strong", null, `Editar letra · ${song.title}`), this.sourceLabel),
        h("button", { class: "secondary", onclick: () => void this.deleteLyrics() }, "Borrar letra…"),
        h("button", { class: "secondary", onclick: () => this.openPaste() }, "Pegar letra completa…"),
        h("button", { onclick: () => void this.save() }, "Guardar"),
      ),
      h(
        "div",
        { class: "controls editor-controls" },
        this.playButton,
        this.seek,
        this.timeLabel,
        h("label", { class: "control" }, this.syncToggle, " Modo sincronizar"),
        this.markButton,
      ),
      h(
        "p",
        { class: "hint small" },
        "Espacio: reproducir/pausar. En modo sincronizar, pulsa Enter justo cuando empiece cada línea. ",
        "Con −/+ ajustas el inicio en décimas y con ⏱ lo pones en el momento actual. ",
        "Al hacer clic en un tiempo, la canción salta un segundo antes de esa línea.",
      ),
      this.message,
      this.list,
    );
    this.onKey = this.onKey.bind(this);
    document.addEventListener("keydown", this.onKey);
    window.addEventListener("beforeunload", this.onBeforeUnload);
  }

  async start(): Promise<void> {
    let lyrics: Lyrics | null = null;
    try {
      lyrics = await api.get<Lyrics>(`/api/songs/${this.song.id}/lyrics`);
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 404)) throw err;
    }
    this.sourceLabel.textContent = lyrics ? LYRICS_LABELS[lyrics.source] : "sin letra todavía";
    this.rows = rowsFromLyrics(lyrics);
    this.render();
    this.info = await api.get<SystemInfo>("/api/system").catch(() => null);
    try {
      this.playback = await createPlayback(this.song);
      this.playback.onStateChange = () => (this.playButton.textContent = this.playback?.isPlaying() ? "❚❚" : "▶");
      this.playButton.disabled = false;
      this.markButton.disabled = !this.syncToggle.checked;
    } catch (err) {
      this.message.textContent = `No se pudo cargar el audio: ${(err as Error).message}. Puedes editar el texto igualmente.`;
    }
    this.loop();
  }

  /** Lyrics time now (song position minus the song's manual offset). */
  private now(): number {
    return (this.playback?.positionMs() ?? 0) - this.song.offset_ms;
  }

  private changed(): void {
    this.dirty = true;
    this.message.textContent = "Cambios sin guardar";
  }

  // --- rows -------------------------------------------------------------

  private render(): void {
    this.rowEls = this.rows.map((row, i) => this.rowEl(row, i));
    this.list.replaceChildren(...this.rowEls);
    this.syncCursor = Math.min(this.syncCursor, this.rows.length - 1);
    this.highlightCursor();
  }

  private rowEl(row: EditRow, index: number): HTMLElement {
    const time = h(
      "button",
      {
        class: "time-button",
        title: "Escuchar desde un poco antes",
        onclick: () => {
          if (row.start !== null && this.playback) {
            this.playback.seek(Math.max(0, row.start + this.song.offset_ms - 1000));
            if (!this.playback.isPlaying()) void this.playback.play();
          }
          this.syncCursor = index;
          this.highlightCursor();
        },
      },
      formatPrecise(row.start),
    );
    const setStart = (value: number | null) => {
      row.start = value === null ? null : Math.max(0, Math.round(value));
      time.textContent = formatPrecise(row.start);
      this.changed();
    };
    const text = h("input", {
      type: "text",
      value: row.text,
      placeholder: "texto de la línea",
      oninput: () => {
        row.text = text.value;
        this.changed();
      },
      onkeydown: (e: KeyboardEvent) => {
        if (e.key === "Enter") {
          e.preventDefault();
          (this.rowEls[index + 1]?.querySelector("input[type=text]") as HTMLInputElement | null)?.focus();
        }
      },
    });
    return h(
      "div",
      { class: "edit-row" },
      time,
      h("button", { class: "icon", title: "−0,1 s", onclick: () => row.start !== null && setStart(row.start - NUDGE_MS) }, "−"),
      h("button", { class: "icon", title: "+0,1 s", onclick: () => row.start !== null && setStart(row.start + NUDGE_MS) }, "+"),
      h("button", { class: "icon", title: "Empieza ahora", onclick: () => setStart(this.now()) }, "⏱"),
      text,
      h(
        "button",
        {
          class: "icon",
          title: "Añadir línea debajo",
          onclick: () => {
            this.rows.splice(index + 1, 0, { start: null, text: "", orig: null });
            this.render();
            this.changed();
            (this.rowEls[index + 1].querySelector("input[type=text]") as HTMLInputElement).focus();
          },
        },
        "＋",
      ),
      h(
        "button",
        {
          class: "icon danger",
          title: "Borrar línea",
          onclick: () => {
            this.rows.splice(index, 1);
            if (this.rows.length === 0) this.rows.push({ start: null, text: "", orig: null });
            this.render();
            this.changed();
          },
        },
        "✕",
      ),
    );
  }

  // --- sync mode ----------------------------------------------------------

  private setSyncMode(on: boolean): void {
    this.markButton.disabled = !on || !this.playback;
    this.el.classList.toggle("syncing", on);
    if (on) {
      const firstMissing = this.rows.findIndex((r) => r.start === null);
      this.syncCursor = firstMissing >= 0 ? firstMissing : 0;
    }
    this.highlightCursor();
  }

  private highlightCursor(): void {
    const on = this.syncToggle.checked;
    this.rowEls.forEach((el, i) => el.classList.toggle("cursor", on && i === this.syncCursor));
    if (on) this.rowEls[this.syncCursor]?.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  private mark(): void {
    if (!this.syncToggle.checked || this.syncCursor >= this.rows.length) return;
    const row = this.rows[this.syncCursor];
    row.start = Math.max(0, Math.round(this.now()));
    (this.rowEls[this.syncCursor].querySelector(".time-button") as HTMLElement).textContent = formatPrecise(row.start);
    this.changed();
    this.syncCursor = Math.min(this.syncCursor + 1, this.rows.length - 1);
    this.highlightCursor();
  }

  private onKey(e: KeyboardEvent): void {
    const typing = e.target instanceof HTMLInputElement && e.target.type === "text";
    const inDialog = e.target instanceof Element && e.target.closest("dialog");
    if (typing || inDialog) return;
    if (e.code === "Space") {
      e.preventDefault();
      this.toggle();
    } else if (e.key === "Enter" && this.syncToggle.checked) {
      e.preventDefault();
      this.mark();
    }
  }

  private toggle(): void {
    if (!this.playback) return;
    if (this.playback.isPlaying()) this.playback.pause();
    else void this.playback.play();
  }

  private loop = (): void => {
    this.frame = requestAnimationFrame(this.loop);
    if (!this.playback) return;
    const pos = this.playback.positionMs();
    const dur = this.playback.durationMs();
    this.timeLabel.textContent = formatPrecise(pos);
    if (!this.seeking && dur > 0) this.seek.value = String(Math.round((pos / dur) * 1000));
    // Highlight the line being sung.
    const t = pos - this.song.offset_ms;
    let current = -1;
    this.rows.forEach((r, i) => {
      if (r.start !== null && r.start <= t) current = i;
    });
    this.rowEls.forEach((el, i) => el.classList.toggle("playing", i === current && this.playback!.isPlaying()));
  };

  // --- saving -------------------------------------------------------------

  private async save(): Promise<boolean> {
    this.rowEls.forEach((el) => el.classList.remove("error"));
    const result = buildLyrics(this.rows, this.playback?.durationMs() ?? this.song.duration_s * 1000);
    if (!result.ok) {
      this.message.textContent = `⚠ Línea ${result.row + 1}: ${result.message}`;
      this.rowEls[result.row]?.classList.add("error");
      this.rowEls[result.row]?.scrollIntoView({ block: "center" });
      return false;
    }
    try {
      const song = await api.put<Song>(`/api/songs/${this.song.id}/lyrics`, result.lyrics);
      this.sourceLabel.textContent = song.lyrics_source ? LYRICS_LABELS[song.lyrics_source] : "";
      this.dirty = false;
      this.message.textContent = result.lyrics.synced
        ? "Guardado ✓"
        : "Guardado ✓ (sin tiempos: usa el modo sincronizar para que se pueda cantar siguiendo la letra)";
      return true;
    } catch (err) {
      this.message.textContent = `No se pudo guardar: ${(err as Error).message}`;
      return false;
    }
  }

  private openPaste(): void {
    const canAlign = !!this.info?.processing_available && this.song.origin === "file";
    const textarea = h("textarea", { rows: 16, placeholder: "Pega aquí la letra, una línea por verso" });
    const align = h("input", { type: "checkbox", checked: canAlign, disabled: !canAlign });
    const status = h("p", { class: "hint" });
    const dialog = h(
      "dialog",
      { class: "modal paste" },
      h("h2", null, "Pegar letra completa"),
      textarea,
      h(
        "label",
        { class: "control" },
        align,
        canAlign
          ? " Sincronizar automáticamente con la voz (tarda unos minutos)"
          : " Sincronizar automáticamente (necesita el procesado de canciones y un archivo de audio)",
      ),
      status,
      h(
        "div",
        { class: "row end" },
        h("button", { class: "secondary", onclick: () => dialog.close() }, "Cancelar"),
        h(
          "button",
          {
            onclick: async () => {
              const text = textarea.value.trim();
              if (!text) return;
              if (align.checked) {
                status.textContent = "Enviando…";
                try {
                  await api.post<Song>(`/api/songs/${this.song.id}/lyrics/text`, { text, align: true });
                  this.dirty = false;
                  dialog.close();
                  alert("Letra guardada. Se está sincronizando con la voz: verás el progreso en la biblioteca.");
                  this.onExit();
                } catch (err) {
                  status.textContent = `Error: ${(err as Error).message}`;
                }
                return;
              }
              this.rows = rowsFromText(text);
              this.render();
              this.changed();
              dialog.close();
              this.syncToggle.checked = true;
              this.setSyncMode(true);
              this.message.textContent = "Letra pegada. Reproduce la canción y pulsa Enter al empezar cada línea; luego Guardar.";
            },
          },
          "Usar esta letra",
        ),
      ),
    );
    dialog.addEventListener("close", () => dialog.remove());
    document.body.append(dialog);
    dialog.showModal();
    textarea.focus();
  }

  private async deleteLyrics(): Promise<void> {
    const canTranscribe = !!this.info?.processing_available && this.song.origin === "file";
    const song = await openDeleteLyrics(this.song, canTranscribe);
    if (!song) return;
    this.dirty = false;
    if (song.status === "processing") {
      alert("Letra borrada. Se está transcribiendo de nuevo: verás el progreso en la biblioteca.");
      this.onExit();
      return;
    }
    this.rows = rowsFromLyrics(null);
    this.render();
    this.sourceLabel.textContent = "sin letra todavía";
    this.message.textContent = "Letra borrada. Puedes pegar otra con «Pegar letra completa» o escribirla aquí.";
  }

  private exit(): void {
    if (this.dirty && !confirm("Hay cambios sin guardar. ¿Salir igualmente?")) return;
    this.dirty = false;
    this.onExit();
  }

  private onBeforeUnload = (e: BeforeUnloadEvent): void => {
    if (this.dirty) e.preventDefault();
  };

  dispose(): void {
    cancelAnimationFrame(this.frame);
    document.removeEventListener("keydown", this.onKey);
    window.removeEventListener("beforeunload", this.onBeforeUnload);
    this.playback?.dispose();
  }
}
