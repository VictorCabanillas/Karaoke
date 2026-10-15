import { api } from "../shared/api";
import { formatTime, h } from "../shared/dom";
import type { LyricsSource, Song, SongStatus } from "../shared/types";
import { openLyricsSearch } from "./lyrics-search";

export const STATUS_LABELS: Record<SongStatus, string> = {
  imported: "importada",
  lyrics_ready: "letra lista",
  processing: "procesando",
  processed: "procesada",
  error: "error",
};

export const LYRICS_LABELS: Record<LyricsSource, string> = {
  lrclib_synced: "letra sincronizada",
  lrclib_aligned: "letra alineada con el audio",
  whisper: "letra transcrita automáticamente",
  lrclib_lines_only: "letra sin sincronizar",
};

export class LibraryView {
  readonly el: HTMLElement;
  private list = h("ul", { class: "songs" });
  private message = h("p", { class: "hint" });
  private songs: Song[] = [];

  constructor(private onPlay: (song: Song) => void) {
    const fileInput = h("input", {
      type: "file",
      accept: "audio/*,.mp3,.m4a,.flac,.ogg,.opus,.wav,.webm,.wma",
      multiple: true,
      hidden: true,
      onchange: () => {
        void this.upload([...(fileInput.files ?? [])]);
        fileInput.value = "";
      },
    });
    const drop = h(
      "div",
      { class: "dropzone", onclick: () => fileInput.click() },
      h("strong", null, "Añadir canciones"),
      h("span", null, "Arrastra archivos de audio aquí o haz clic para elegirlos"),
      fileInput,
    );
    drop.addEventListener("dragover", (e) => {
      e.preventDefault();
      drop.classList.add("over");
    });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (e) => {
      e.preventDefault();
      drop.classList.remove("over");
      void this.upload([...(e.dataTransfer?.files ?? [])]);
    });

    this.el = h("section", { class: "library" }, h("h2", null, "Biblioteca"), drop, this.message, this.list);
  }

  async refresh(): Promise<void> {
    try {
      this.songs = await api.get<Song[]>("/api/songs");
      this.render();
    } catch (err) {
      this.message.textContent = `No se pudo cargar la biblioteca: ${(err as Error).message}`;
    }
  }

  /** Replace one song in place (e.g. progress pushed by the server). */
  updateSong(song: Song): void {
    const i = this.songs.findIndex((s) => s.id === song.id);
    if (i >= 0) this.songs[i] = song;
    else this.songs.unshift(song);
    this.render();
  }

  private async upload(files: File[]): Promise<void> {
    for (const [i, file] of files.entries()) {
      this.message.textContent = `Subiendo ${file.name} (${i + 1}/${files.length})…`;
      try {
        const song = await api.upload<Song>("/api/songs", file);
        this.updateSong(song);
      } catch (err) {
        this.message.textContent = `Error con ${file.name}: ${(err as Error).message}`;
        return;
      }
    }
    this.message.textContent = "";
  }

  protected actions(song: Song): HTMLElement[] {
    return [
      h("button", { onclick: () => this.onPlay(song) }, "Cantar"),
      h(
        "button",
        {
          class: "secondary",
          onclick: async () => {
            const updated = await openLyricsSearch(song);
            if (updated) this.updateSong(updated);
          },
        },
        song.lyrics_source ? "Cambiar letra" : "Buscar letra",
      ),
      h(
        "button",
        {
          class: "danger",
          onclick: async () => {
            if (!confirm(`¿Eliminar «${song.title}» de la biblioteca?`)) return;
            await api.delete(`/api/songs/${song.id}`);
            this.songs = this.songs.filter((s) => s.id !== song.id);
            this.render();
          },
        },
        "Eliminar",
      ),
    ];
  }

  private render(): void {
    if (this.songs.length === 0) {
      this.list.replaceChildren(h("li", { class: "empty" }, "Todavía no hay canciones."));
      return;
    }
    this.list.replaceChildren(...this.songs.map((song) => this.item(song)));
  }

  private item(song: Song): HTMLElement {
    const lyricsInfo = song.lyrics_source
      ? LYRICS_LABELS[song.lyrics_source]
      : song.instrumental
        ? "instrumental"
        : "sin letra";
    const progress =
      song.status === "processing"
        ? h(
            "div",
            { class: "progress" },
            h("div", { class: "bar", style: `width:${Math.round(song.progress * 100)}%` }),
            h("span", null, song.progress_label || "en cola"),
          )
        : null;
    return h(
      "li",
      { class: `song status-${song.status}` },
      h(
        "div",
        { class: "info" },
        h("strong", null, song.title),
        h("span", null, [song.artist, song.album].filter(Boolean).join(" · ")),
        h(
          "small",
          null,
          [song.origin === "spotify" ? "Spotify" : null, formatTime(song.duration_s), lyricsInfo]
            .filter(Boolean)
            .join(" · "),
        ),
        song.error ? h("small", { class: "error" }, song.error) : null,
        progress,
      ),
      h("span", { class: `badge status ${song.status}` }, STATUS_LABELS[song.status]),
      h("div", { class: "actions" }, ...this.actions(song)),
    );
  }
}
