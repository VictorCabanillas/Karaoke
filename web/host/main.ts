import "../shared/base.css";
import "./style.css";
import { api } from "../shared/api";
import { $, h } from "../shared/dom";
import type { Song } from "../shared/types";
import { LibraryView } from "./library";
import { PlayScreen } from "./play";

const app = $("#app");
const library = new LibraryView((song) => (location.hash = `#/play/${song.id}`));
let screen: PlayScreen | null = null;

async function route(): Promise<void> {
  screen?.dispose();
  screen = null;
  const match = location.hash.match(/^#\/play\/(\w+)/);
  if (match) {
    try {
      const song = await api.get<Song>(`/api/songs/${match[1]}`);
      screen = new PlayScreen(song, () => (location.hash = "#/"));
      app.replaceChildren(screen.el);
      await screen.start();
    } catch (err) {
      app.replaceChildren(h("p", { class: "error" }, `Error: ${(err as Error).message}`));
    }
    return;
  }
  app.replaceChildren(h("header", { class: "app-header" }, h("h1", null, "🎤 Karaoke entre amigos")), library.el);
  await library.refresh();
}

window.addEventListener("hashchange", () => void route());
void route();
