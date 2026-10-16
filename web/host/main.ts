import "../shared/base.css";
import "./style.css";
import { api } from "../shared/api";
import { $, h } from "../shared/dom";
import type { Song } from "../shared/types";
import { LibraryView } from "./library";
import { LyricsEditor } from "./lyrics-editor";
import { PlayScreen } from "./play";
import { RankingView } from "./ranking";
import { RoomPanel } from "./room-panel";
import { SettingsView } from "./settings";
import { tvButton } from "./tv";

const app = $("#app");
const library = new LibraryView(
  (song, mode) => (location.hash = `#/${mode === "competition" ? "compete" : "play"}/${song.id}`),
);
const roomPanel = new RoomPanel();
let screen: PlayScreen | LyricsEditor | SettingsView | RankingView | null = null;
const home = () => (location.hash = "#/");

async function route(): Promise<void> {
  screen?.dispose();
  screen = null;
  const page = location.hash.match(/^#\/(settings|ranking)$/);
  if (page) {
    screen = page[1] === "settings" ? new SettingsView(home) : new RankingView(home);
    app.replaceChildren(screen.el);
    await screen.start().catch((err) => app.append(h("p", { class: "error" }, `Error: ${(err as Error).message}`)));
    return;
  }
  const match = location.hash.match(/^#\/(play|compete|edit)\/(\w+)/);
  if (match) {
    try {
      const song = await api.get<Song>(`/api/songs/${match[2]}`);
      const back = home;
      screen =
        match[1] === "edit"
          ? new LyricsEditor(song, back)
          : new PlayScreen(song, back, match[1] === "compete" ? "competition" : "free");
      app.replaceChildren(screen.el);
      await screen.start();
    } catch (err) {
      app.replaceChildren(h("p", { class: "error" }, `Error: ${(err as Error).message}`));
    }
    return;
  }
  app.replaceChildren(
    h(
      "header",
      { class: "app-header row spread" },
      h("h1", null, "🎤 Karaoke entre amigos"),
      h(
        "nav",
        { class: "row" },
        h("button", { class: "secondary small", onclick: () => (location.hash = "#/ranking") }, "🏆 Ranking"),
        h("button", { class: "secondary small", onclick: () => (location.hash = "#/settings") }, "⚙ Ajustes"),
        tvButton(),
      ),
    ),
    h("div", { class: "home" }, library.el, roomPanel.el),
  );
  await library.refresh();
}

window.addEventListener("hashchange", () => void route());
void route();
