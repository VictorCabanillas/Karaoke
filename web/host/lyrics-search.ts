import { api } from "../shared/api";
import { formatTime, h } from "../shared/dom";
import type { LrclibCandidate, Song } from "../shared/types";

/** Modal to search LRCLIB by hand and pick the lyrics for a song. Resolves with the updated song. */
export function openLyricsSearch(song: Song): Promise<Song | null> {
  return new Promise((resolve) => {
    const input = h("input", {
      type: "search",
      value: [song.artist, song.title].filter(Boolean).join(" "),
      placeholder: "Artista y título",
    });
    const results = h("ul", { class: "candidates" });
    const status = h("p", { class: "hint" });
    let chosen: Song | null = null;

    const search = async () => {
      status.textContent = "Buscando…";
      results.replaceChildren();
      try {
        const q = encodeURIComponent(input.value.trim());
        const items = await api.get<LrclibCandidate[]>(`/api/songs/${song.id}/lyrics/search?q=${q}`);
        status.textContent = items.length ? "" : "No se encontró nada. Prueba con otro título o artista.";
        results.replaceChildren(...items.map(candidateItem));
      } catch (err) {
        status.textContent = `Error: ${(err as Error).message}`;
      }
    };

    const candidateItem = (c: LrclibCandidate) => {
      const diff = c.duration && song.duration_s ? Math.abs(c.duration - song.duration_s) : null;
      const badges = [
        c.has_synced ? h("span", { class: "badge ok" }, "sincronizada") : null,
        !c.has_synced && c.has_plain ? h("span", { class: "badge" }, "solo texto") : null,
        c.instrumental ? h("span", { class: "badge" }, "instrumental") : null,
        diff !== null && diff > 2 ? h("span", { class: "badge warn" }, `duración ±${Math.round(diff)} s`) : null,
      ];
      return h(
        "li",
        null,
        h(
          "button",
          {
            class: "candidate",
            onclick: async () => {
              status.textContent = "Guardando…";
              try {
                chosen = await api.post<Song>(`/api/songs/${song.id}/lyrics`, { lrclib_id: c.id });
                dialog.close();
              } catch (err) {
                status.textContent = `Error: ${(err as Error).message}`;
              }
            },
          },
          h("strong", null, c.track_name),
          ` — ${c.artist_name}`,
          c.album_name ? h("small", null, ` (${c.album_name})`) : null,
          c.duration ? h("small", null, ` · ${formatTime(c.duration)}`) : null,
          ...badges,
        ),
      );
    };

    const dialog = h(
      "dialog",
      { class: "modal" },
      h("h2", null, "Buscar letra"),
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
      status,
      results,
      h("div", { class: "row end" }, h("button", { class: "secondary", onclick: () => dialog.close() }, "Cerrar")),
    );
    dialog.addEventListener("close", () => {
      dialog.remove();
      resolve(chosen);
    });
    document.body.append(dialog);
    dialog.showModal();
    void search();
  });
}
