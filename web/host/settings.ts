import { api } from "../shared/api";
import { h } from "../shared/dom";
import type { AppSettings, Song, SystemInfo, WhisperModelName } from "../shared/types";

const MODELS: Record<WhisperModelName, string> = {
  "": "Automático (medium con GPU, small sin GPU)",
  tiny: "tiny · 75 MB · muy rápido, poco preciso",
  base: "base · 150 MB · rápido, poco preciso",
  small: "small · 500 MB · equilibrado (el automático sin GPU)",
  medium: "medium · 1,5 GB · preciso (el automático con GPU)",
  "large-v3-turbo": "large-v3-turbo · 1,6 GB · casi como large-v3 y bastante más rápido",
  "large-v3": "large-v3 · 3 GB · el más preciso; lento, mejor con GPU",
};

/** Settings tab: transcription model, reprocess everything, history. */
export class SettingsView {
  readonly el: HTMLElement;
  private body = h("div", { class: "settings-body" }, h("p", { class: "hint" }, "Cargando…"));

  constructor(onExit: () => void) {
    this.el = h(
      "section",
      { class: "settings-view" },
      h(
        "header",
        { class: "play-header" },
        h("button", { class: "secondary", onclick: onExit }, "← Biblioteca"),
        h("div", { class: "title" }, h("strong", null, "Ajustes")),
      ),
      this.body,
    );
  }

  async start(): Promise<void> {
    const [settings, info] = await Promise.all([
      api.get<AppSettings>("/api/settings"),
      api.get<SystemInfo>("/api/system"),
    ]);
    this.body.replaceChildren(this.transcription(settings, info), this.songs(info), this.history(), this.system(info));
  }

  private transcription(settings: AppSettings, info: SystemInfo): HTMLElement {
    const status = h("span", { class: "hint small" });
    const select = h(
      "select",
      {
        onchange: async () => {
          status.textContent = "Guardando…";
          try {
            await api.put<AppSettings>("/api/settings", { whisper_model: select.value as WhisperModelName });
            status.textContent = "Guardado ✓";
          } catch (err) {
            status.textContent = `Error: ${(err as Error).message}`;
          }
        },
      },
      ...(Object.keys(MODELS) as WhisperModelName[]).map((m) =>
        h("option", { value: m, selected: m === settings.whisper_model }, MODELS[m]),
      ),
    );
    return h(
      "section",
      { class: "card-section" },
      h("h3", null, "Transcripción de letras"),
      h(
        "p",
        { class: "hint" },
        "Modelo de Whisper para alinear las letras con la voz y para transcribir canciones sin letra. ",
        info.device === "cuda" ? "Tienes GPU: los modelos grandes van bien." : "Sin GPU los modelos grandes son muy lentos.",
        " La primera vez que se usa un modelo se descarga.",
      ),
      h("div", { class: "row" }, select, status),
      h("p", { class: "hint small" }, "Se aplica a lo próximo que se procese. Para las canciones ya procesadas: «Reprocesar todas»."),
    );
  }

  private songs(info: SystemInfo): HTMLElement {
    const status = h("p", { class: "hint small" });
    const button = h(
      "button",
      {
        disabled: !info.processing_available,
        onclick: async () => {
          const songs = await api.get<Song[]>("/api/songs");
          const files = songs.filter((s) => s.origin === "file" && s.has_audio);
          if (!files.length) {
            status.textContent = "No hay canciones que procesar.";
            return;
          }
          const each = info.device === "cuda" ? "unos 2–3 minutos" : "bastantes minutos";
          if (!confirm(`¿Volver a procesar ${files.length} canciones? Cada una tarda ${each}; se hacen de una en una.`)) return;
          button.disabled = true;
          try {
            const queued = await api.post<Song[]>("/api/songs/process-all");
            status.textContent = `${queued.length} canciones en cola. El progreso se ve en la biblioteca.`;
          } catch (err) {
            status.textContent = `Error: ${(err as Error).message}`;
            button.disabled = false;
          }
        },
      },
      "Reprocesar todas",
    );
    return h(
      "section",
      { class: "card-section" },
      h("h3", null, "Canciones"),
      h(
        "p",
        { class: "hint" },
        "Vuelve a calcular la letra de todas las canciones (con el modelo elegido y la comprobación contra la voz). ",
        "Las voces separadas y las melodías se reutilizan.",
      ),
      info.processing_available
        ? button
        : h("p", { class: "warn-text" }, "El procesado no está instalado («uv sync» y reinicia el servidor)."),
      status,
    );
  }

  private history(): HTMLElement {
    const status = h("p", { class: "hint small" });
    return h(
      "section",
      { class: "card-section" },
      h("h3", null, "Historial"),
      h("p", { class: "hint" }, "Las partidas de competición se guardan para el ranking y los récords."),
      h(
        "button",
        {
          class: "danger",
          onclick: async () => {
            if (!confirm("¿Borrar todo el historial, el ranking y los récords? No se puede deshacer.")) return;
            await api.delete("/api/history");
            status.textContent = "Historial borrado.";
          },
        },
        "Borrar historial",
      ),
      status,
    );
  }

  private system(info: SystemInfo): HTMLElement {
    const device = { cuda: "GPU NVIDIA", mps: "GPU Apple", cpu: "CPU" }[info.device] ?? info.device;
    const yes = (ok: boolean) => (ok ? "sí" : "no");
    return h(
      "section",
      { class: "card-section" },
      h("h3", null, "Sistema"),
      h(
        "ul",
        { class: "plain-list hint" },
        h("li", null, `Procesado de canciones: ${info.processing_available ? device : "no instalado"}`),
        h("li", null, `ffmpeg: ${yes(info.ffmpeg)}`),
        h("li", null, `HTTPS (micrófono en los móviles): ${yes(info.https)}`),
        h("li", null, `Descargas (yt-dlp): ${yes(info.downloads_available)}`),
        h("li", null, `Spotify: ${info.spotify_configured ? "configurado" : "sin configurar"}`),
      ),
    );
  }

  dispose(): void {}
}
