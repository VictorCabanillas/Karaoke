import "../shared/base.css";
import "./style.css";
import { api } from "../shared/api";
import { $, h } from "../shared/dom";
import type { Song } from "../shared/types";
import { LibraryView } from "./library";
import { LyricsEditor } from "./lyrics-editor";
import { PlayScreen } from "./play";
import { RoomPanel } from "./room-panel";

const app = $("#app");
const library = new LibraryView(
  (song, mode) => (location.hash = `#/${mode === "competition" ? "compete" : "play"}/${song.id}`),
);
const roomPanel = new RoomPanel();
let screen: PlayScreen | LyricsEditor | null = null;

async function route(): Promise<void> {
  screen?.dispose();
  screen = null;
  const match = location.hash.match(/^#\/(play|compete|edit)\/(\w+)/);
  if (match) {
    try {
      const song = await api.get<Song>(`/api/songs/${match[2]}`);
      const back = () => (location.hash = "#/");
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
    h("header", { class: "app-header" }, h("h1", null, "🎤 Karaoke entre amigos")),
    h("div", { class: "home" }, library.el, roomPanel.el),
  );
  await library.refresh();
}

window.addEventListener("hashchange", () => void route());
void route();
