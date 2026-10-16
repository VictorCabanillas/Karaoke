import { api } from "../shared/api";
import { h } from "../shared/dom";
import type { GameRecord, Rankings } from "../shared/types";
import { breakdown } from "./scoreboard";

const points = (n: number) => n.toLocaleString("es-ES");
const date = (seconds: number) =>
  new Date(seconds * 1000).toLocaleString("es-ES", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const dot = (color: string) => h("span", { class: "dot", style: `background:${color}` });

/** Ranking of players, records per song and the history of games. */
export class RankingView {
  readonly el: HTMLElement;
  private body = h("div", null, h("p", { class: "hint" }, "Cargando…"));
  private tabs: Record<string, HTMLButtonElement> = {};
  private data: { rankings: Rankings; games: GameRecord[] } | null = null;

  constructor(onExit: () => void) {
    const tab = (id: string, label: string) =>
      (this.tabs[id] = h("button", { class: "tab", onclick: () => this.show(id) }, label));
    this.el = h(
      "section",
      { class: "ranking-view" },
      h(
        "header",
        { class: "play-header" },
        h("button", { class: "secondary", onclick: onExit }, "← Biblioteca"),
        h("div", { class: "title" }, h("strong", null, "Ranking")),
      ),
      h("nav", { class: "tabs" }, tab("players", "Jugadores"), tab("records", "Récords"), tab("history", "Historial")),
      this.body,
    );
  }

  async start(): Promise<void> {
    const [rankings, games] = await Promise.all([
      api.get<Rankings>("/api/rankings"),
      api.get<GameRecord[]>("/api/history?limit=200"),
    ]);
    this.data = { rankings, games };
    this.show("players");
  }

  private show(id: string): void {
    Object.entries(this.tabs).forEach(([key, el]) => el.classList.toggle("active", key === id));
    if (!this.data) return;
    if (!this.data.games.length) {
      this.body.replaceChildren(h("p", { class: "hint" }, "Todavía no hay partidas. Juega en modo «Competir» para empezar el ranking."));
      return;
    }
    const view = { players: () => this.players(), records: () => this.records(), history: () => this.history() }[id];
    this.body.replaceChildren(view!());
  }

  private players(): HTMLElement {
    const rows = this.data!.rankings.players.map((p, i) =>
      h(
        "tr",
        null,
        h("td", null, i < 3 ? ["🥇", "🥈", "🥉"][i] : `${i + 1}.`),
        h("td", null, dot(p.color), " ", p.name),
        h("td", null, p.wins),
        h("td", null, p.games),
        h("td", null, points(p.best_score)),
        h("td", null, points(p.average_score)),
      ),
    );
    return h(
      "table",
      { class: "data-table" },
      h(
        "thead",
        null,
        h("tr", null, h("th"), h("th", null, "Jugador"), h("th", null, "Victorias"), h("th", null, "Partidas"),
          h("th", null, "Mejor"), h("th", null, "Media")),  // prettier-ignore
      ),
      h("tbody", null, ...rows),
    );
  }

  private records(): HTMLElement {
    return h(
      "table",
      { class: "data-table" },
      h("thead", null, h("tr", null, h("th", null, "Canción"), h("th", null, "Récord"), h("th", null, "De"), h("th", null, "Cuándo"))),
      h(
        "tbody",
        null,
        ...this.data!.rankings.records.map((r) =>
          h(
            "tr",
            null,
            h("td", null, r.song_title),
            h("td", null, points(r.score)),
            h("td", null, dot(r.color), " ", r.name),
            h("td", { class: "hint" }, date(r.played_at)),
          ),
        ),
      ),
    );
  }

  private history(): HTMLElement {
    return h(
      "ul",
      { class: "history" },
      ...this.data!.games.map((g) =>
        h(
          "li",
          null,
          h("div", { class: "row spread" }, h("strong", null, g.song_title), h("span", { class: "hint small" }, date(g.played_at))),
          h(
            "ol",
            null,
            ...g.ranking.map((e) =>
              h("li", null, dot(e.color), " ", e.name, " · ", h("strong", null, points(e.score)), h("small", { class: "hint" }, ` ${breakdown(e)}`)),
            ),
          ),
          g.awards.length ? h("p", { class: "hint small" }, g.awards.map((a) => `${a.title}: ${a.name}`).join(" · ")) : null,
        ),
      ),
    );
  }

  dispose(): void {}
}
