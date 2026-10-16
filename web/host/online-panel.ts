import { api } from "../shared/api";
import { formatTime, h } from "../shared/dom";
import type { OnlineTrack, Song } from "../shared/types";

/** Search songs on YouTube and download them into the library. */
export class OnlinePanel {
  readonly el: HTMLElement;
  private body = h("div", { class: "online-body" });
  private message = h("p", { class: "hint small" });
  private results = h("ul", { class: "online-results" });

  constructor(private onAdded: (song: Song) => void) {
    this.el = h(
      "details",
      { class: "online-panel" },
      h("summary", null, "Buscar canciones en internet"),
      this.body,
      this.message,
    );
  }

  setAvailable(available: boolean): void {
    if (!available) {
      this.body.replaceChildren(
        h("p", { class: "hint" }, "Falta yt-dlp. Ejecuta «uv sync» (o setup.cmd) y reinicia el servidor."),
      );
      return;
    }
    const input = h("input", { type: "search", placeholder: "Artista y canción" });
    this.body.replaceChildren(
      h(
        "form",
        {
          class: "row",
          onsubmit: (e: Event) => {
            e.preventDefault();
            void this.search(input.value.trim());
          },
        },
        input,
        h("button", { type: "submit" }, "Buscar"),
      ),
      this.results,
      h(
        "p",
        { class: "hint small" },
        "Busca en YouTube y descarga solo el audio. Úsalo para uso privado y con música que tengas derecho a usar.",
      ),
    );
  }

  private async search(q: string): Promise<void> {
    if (!q) return;
    this.message.textContent = "Buscando…";
    this.results.replaceChildren();
    try {
      const tracks = await api.get<OnlineTrack[]>(`/api/online/search?q=${encodeURIComponent(q)}`);
      this.message.textContent = tracks.length ? "" : "Sin resultados.";
      this.results.replaceChildren(...tracks.map((t) => this.item(t)));
    } catch (err) {
      this.message.textContent = `Error: ${(err as Error).message}`;
    }
  }

  private item(t: OnlineTrack): HTMLElement {
    return h(
      "li",
      null,
      t.thumbnail ? h("img", { src: t.thumbnail, alt: "", width: 64, height: 36, loading: "lazy" }) : null,
      h(
        "span",
        { class: "info" },
        h("strong", null, t.title),
        h("small", { class: "hint" }, [t.channel, t.duration_s ? formatTime(t.duration_s) : null].filter(Boolean).join(" · ")),
      ),
      h(
        "button",
        {
          class: "secondary",
          onclick: async (e: Event) => {
            const button = e.currentTarget as HTMLButtonElement;
            button.disabled = true;
            try {
              const song = await api.post<Song>("/api/online/download", {
                video_id: t.id,
                title: t.title,
                channel: t.channel,
              });
              this.onAdded(song);
              button.textContent = "En la biblioteca ✓";
            } catch (err) {
              button.disabled = false;
              this.message.textContent = `Error: ${(err as Error).message}`;
            }
          },
        },
        "Descargar",
      ),
    );
  }
}
