import { api } from "../shared/api";
import { h } from "../shared/dom";
import type { Song } from "../shared/types";

/**
 * Ask how to delete a song's lyrics. Resolves with the updated song, or null if cancelled.
 * "Reprocesar" keeps lyrics edited by hand; deleting them is how to get a fresh transcription.
 */
export function openDeleteLyrics(song: Song, canTranscribe: boolean): Promise<Song | null> {
  return new Promise((resolve) => {
    let result: Song | null = null;
    const status = h("p", { class: "hint" });
    const run = async (retranscribe: boolean) => {
      status.textContent = "Borrando…";
      try {
        result = await api.delete<Song>(`/api/songs/${song.id}/lyrics?retranscribe=${retranscribe}`);
        dialog.close();
      } catch (err) {
        status.textContent = `Error: ${(err as Error).message}`;
      }
    };
    const dialog = h(
      "dialog",
      { class: "modal" },
      h("h2", null, "Borrar la letra"),
      h("p", null, `¿Qué hacemos con la letra de «${song.title}»?`),
      canTranscribe
        ? h(
            "p",
            { class: "hint" },
            "«Borrar y transcribir» la saca de nuevo de la voz con el modelo elegido en Ajustes (tarda unos minutos). ",
            "Si prefieres otra letra de internet, usa «Cambiar letra».",
          )
        : h("p", { class: "hint" }, "La canción quedará sin letra hasta que busques otra o la pegues en el editor."),
      status,
      h(
        "div",
        { class: "row end" },
        h("button", { class: "secondary", onclick: () => dialog.close() }, "Cancelar"),
        h("button", { class: "danger", onclick: () => void run(false) }, "Solo borrar"),
        canTranscribe ? h("button", { onclick: () => void run(true) }, "Borrar y transcribir") : null,
      ),
    );
    dialog.addEventListener("close", () => {
      dialog.remove();
      resolve(result);
    });
    document.body.append(dialog);
    dialog.showModal();
  });
}
