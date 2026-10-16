import { h } from "../shared/dom";
import type { ResultsMsg, ScoreEntry } from "../shared/types";

const COMBO_MIN_WINDOWS = 10; // show the streak from 1 s on

/** Live ranking shown during a competition. */
export class Scoreboard {
  readonly el: HTMLElement;
  private list = h("ol", { class: "scores" });
  private note = h("p", { class: "hint small" });

  constructor() {
    this.el = h("aside", { class: "scoreboard", hidden: true }, h("h3", null, "Marcador"), this.list, this.note);
  }

  show(simplified: boolean): void {
    this.el.hidden = false;
    this.note.textContent = simplified ? "Puntuación simplificada (sin melodía de referencia)" : "";
  }

  hide(): void {
    this.el.hidden = true;
  }

  render(entries: ScoreEntry[]): void {
    this.list.replaceChildren(
      ...entries.map((e) =>
        h(
          "li",
          null,
          h("span", { class: "dot", style: `background:${e.color}` }),
          h("span", { class: "name" }, e.name),
          e.combo >= COMBO_MIN_WINDOWS ? h("span", { class: "combo" }, `🔥 ${(e.combo / 10).toFixed(0)} s`) : null,
          h("strong", { class: "points" }, e.score.toLocaleString("es-ES")),
        ),
      ),
    );
  }
}

const MEDALS = ["🥇", "🥈", "🥉"];

/** "afinación 92% · presencia 81%": where the points went. */
export function breakdown(e: ScoreEntry): string {
  const parts = [];
  if (e.accuracy !== null) parts.push(`afinación ${Math.round(e.accuracy * 100)}%`);
  parts.push(`presencia ${Math.round(e.presence * 100)}%`);
  return parts.join(" · ");
}

/** Final screen: ranking + fun awards. */
export function showResults(results: ResultsMsg, onAgain: () => void, onExit: () => void): HTMLDialogElement {
  const dialog = h(
    "dialog",
    { class: "modal results" },
    h("h2", null, "🏆 Resultados"),
    h("p", { class: "hint" }, results.song_title, results.simplified ? " · puntuación simplificada" : ""),
    results.new_record && results.ranking.length
      ? h(
          "p",
          { class: "record" },
          results.previous_record
            ? `🏅 ¡Nuevo récord de la canción para ${results.ranking[0].name}! (antes: ${results.previous_record.score.toLocaleString("es-ES")} de ${results.previous_record.name})`
            : `🏅 Primer récord de esta canción: ${results.ranking[0].name}`,
        )
      : results.previous_record
        ? h("p", { class: "hint small" }, `Récord de la canción: ${results.previous_record.score.toLocaleString("es-ES")} (${results.previous_record.name})`)
        : null,
    results.ranking.length
      ? h(
          "ol",
          { class: "ranking" },
          ...results.ranking.map((e, i) =>
            h(
              "li",
              { class: i === 0 ? "winner" : "" },
              h("span", { class: "medal" }, MEDALS[i] ?? `${i + 1}.`),
              h("span", { class: "dot", style: `background:${e.color}` }),
              h("span", { class: "name" }, e.name, h("small", { class: "hint breakdown" }, breakdown(e))),
              h("strong", null, e.score.toLocaleString("es-ES")),
            ),
          ),
        )
      : h("p", null, "Nadie ha cantado esta vez."),
    results.awards.length
      ? h(
          "ul",
          { class: "awards" },
          ...results.awards.map((a) =>
            h("li", null, h("strong", null, a.title), ": ", a.name, h("small", { class: "hint" }, ` (${a.detail})`)),
          ),
        )
      : null,
    h(
      "div",
      { class: "row end" },
      h("button", { class: "secondary", onclick: () => (dialog.close(), onExit()) }, "Volver a la biblioteca"),
      h("button", { onclick: () => (dialog.close(), onAgain()) }, "Otra vez"),
    ),
  );
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}
