import { api } from "../shared/api";
import { formatTime, h } from "../shared/dom";
import type { Song, SpotifyStatus, SpotifyTrack } from "../shared/types";

/** Connect the host's Spotify account, search tracks and add them to the library. */
export class SpotifyPanel {
  readonly el: HTMLElement;
  private body = h("div", { class: "spotify-body" });
  private message = h("p", { class: "hint small" });
  private status: SpotifyStatus | null = null;

  constructor(private onAdded: (song: Song) => void) {
    this.el = h("details", { class: "spotify-panel" }, h("summary", null, "Spotify"), this.body, this.message);
    const error = new URLSearchParams(location.hash.split("?")[1] ?? "").get("spotify_error");
    if (error) {
      this.message.textContent = `No se pudo conectar con Spotify (${error}).`;
      (this.el as HTMLDetailsElement).open = true;
    }
  }

  async refresh(): Promise<void> {
    try {
      this.status = await api.get<SpotifyStatus>("/api/spotify/status");
    } catch {
      return;
    }
    this.render();
  }

  private render(): void {
    const s = this.status!;
    if (!s.configured) {
      this.body.replaceChildren(
        h(
          "p",
          { class: "hint" },
          "Para usar canciones de Spotify (cuenta Premium) configura KARAOKE_SPOTIFY_CLIENT_ID. Instrucciones en el README.",
        ),
      );
      return;
    }
    if (!s.connected) {
      this.body.replaceChildren(
        h("p", { class: "hint" }, "Conecta tu cuenta Premium. Los amigos no necesitan Spotify."),
        h("button", { onclick: () => (location.href = "/api/spotify/login") }, "Conectar con Spotify"),
        h("p", { class: "hint small" }, `URI de redirección registrada en Spotify: ${s.redirect_uri}`),
      );
      return;
    }
    const input = h("input", { type: "search", placeholder: "Buscar canción en Spotify" });
    const results = h("ul", { class: "spotify-results" });
    const search = async () => {
      const q = input.value.trim();
      if (!q) return;
      this.message.textContent = "Buscando…";
      try {
        const tracks = await api.get<SpotifyTrack[]>(`/api/spotify/search?q=${encodeURIComponent(q)}`);
        this.message.textContent = tracks.length ? "" : "Sin resultados.";
        results.replaceChildren(...tracks.map((t) => this.track(t)));
      } catch (err) {
        this.message.textContent = `Error: ${(err as Error).message}`;
      }
    };
    this.body.replaceChildren(
      h(
        "form",
        {
          class: "row",
          onsubmit: (e: Event) => {
            e.preventDefault();
            void search();
          },
        },
        input,
        h("button", { type: "submit" }, "Buscar"),
      ),
      results,
      h(
        "p",
        { class: "hint small" },
        "Con Spotify la letra va por líneas, sin control de voz y con puntuación simplificada. ",
        h(
          "button",
          {
            class: "link",
            onclick: async () => {
              await api.post("/api/spotify/logout");
              await this.refresh();
            },
          },
          "Desconectar",
        ),
      ),
    );
  }

  private track(t: SpotifyTrack): HTMLElement {
    return h(
      "li",
      null,
      t.image_url ? h("img", { src: t.image_url, alt: "", width: 40, height: 40 }) : null,
      h("span", { class: "info" }, h("strong", null, t.name), h("small", { class: "hint" }, `${t.artists} · ${formatTime(t.duration_ms / 1000)}`)),
      h(
        "button",
        {
          class: "secondary",
          onclick: async (e: Event) => {
            const button = e.currentTarget as HTMLButtonElement;
            button.disabled = true;
            try {
              const song = await api.post<Song>("/api/spotify/songs", { track_id: t.id });
              this.onAdded(song);
              button.textContent = song.lyrics_source ? "Añadida ✓" : "Añadida (sin letra)";
            } catch (err) {
              button.disabled = false;
              this.message.textContent = `Error: ${(err as Error).message}`;
            }
          },
        },
        "Añadir",
      ),
    );
  }
}
