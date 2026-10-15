import { api, ApiError } from "../shared/api";
import { debounce, formatTime, h } from "../shared/dom";
import type { GameMode, Lyrics, Melody, Song } from "../shared/types";
import { AudioEngine } from "./audio";
import { hostSocket, reportPlayback, room, startPlaybackReports } from "./connection";
import { PitchLanes } from "./lanes";
import { LYRICS_LABELS } from "./library";
import { LyricsView } from "./lyrics-view";
import type { Playback } from "./playback";
import { Scoreboard, showResults } from "./scoreboard";
import { SpotifyPlayback } from "./spotify-playback";

const OFFSET_LIMIT_MS = 2000;
const OFFSET_STEP_MS = 50;

export class PlayScreen {
  readonly el: HTMLElement;
  protected playback: Playback | null = null;
  private lyricsView: LyricsView | null = null;
  protected lanes = new PitchLanes();
  private scoreboard = new Scoreboard();
  private gameActive = false;
  private finishButton = h("button", { class: "secondary", hidden: true }, "Terminar y ver resultados");
  private frame = 0;
  private cleanups: (() => void)[] = [];
  protected stage = h("div", { class: "stage" }, h("p", { class: "loading" }, "Cargando…"));
  private playButton = h("button", { class: "play", disabled: true }, "▶");
  protected seek = h("input", { type: "range", min: "0", max: "1000", value: "0", class: "seek" });
  private timeLabel = h("span", { class: "time" }, "0:00 / 0:00");
  private offsetLabel = h("output");
  private banner = h("div", { class: "banner", hidden: true });
  private voice = h("input", { type: "range", min: "0", max: "100", step: "1", value: "100", disabled: true });
  private voiceLabel = h("output", null, "100%");
  private instrumentalOnly = h("input", { type: "checkbox", disabled: true });
  private voiceControl = h(
    "label",
    { class: "control disabled", title: "Disponible cuando termine el procesado" },
    "Volumen de la voz ",
    this.voice,
    this.voiceLabel,
  );
  private instrumentalControl = h(
    "label",
    { class: "control disabled", title: "Disponible cuando termine el procesado" },
    this.instrumentalOnly,
    " Solo instrumental",
  );
  protected voiceHint = h("span", { class: "hint small" }, "Control de voz disponible cuando termine el procesado");
  protected controls: HTMLElement;
  protected header: HTMLElement;
  private seeking = false;

  constructor(
    protected song: Song,
    private onExit: () => void,
    protected mode: GameMode = "free",
  ) {
    const offset = h("input", {
      type: "range",
      min: String(-OFFSET_LIMIT_MS),
      max: String(OFFSET_LIMIT_MS),
      step: String(OFFSET_STEP_MS),
      value: String(song.offset_ms),
    });
    const saveOffset = debounce((value: number) => {
      void api.patch<Song>(`/api/songs/${song.id}`, { offset_ms: value });
      this.report();
    }, 400);
    offset.addEventListener("input", () => {
      this.song.offset_ms = Number(offset.value);
      this.showOffset();
      saveOffset(this.song.offset_ms);
    });
    this.showOffset();

    this.playButton.addEventListener("click", () => this.toggle());
    this.seek.addEventListener("input", () => (this.seeking = true));
    this.seek.addEventListener("change", () => {
      this.seeking = false;
      if (this.playback) this.playback.seek((Number(this.seek.value) / 1000) * this.playback.durationMs());
    });

    this.header = h(
      "header",
      { class: "play-header" },
      h("button", { class: "secondary", onclick: () => this.onExit() }, "← Biblioteca"),
      h("div", { class: "title" }, h("strong", null, song.title), h("span", null, song.artist)),
      h("span", { class: `badge mode ${mode}` }, mode === "competition" ? "Competición" : "Karaoke libre"),
      this.finishButton,
    );
    this.finishButton.addEventListener("click", () => this.finishGame());
    this.controls = h(
      "footer",
      { class: "controls" },
      this.playButton,
      this.seek,
      this.timeLabel,
      h(
        "label",
        { class: "control", title: "Positivo: la letra aparece más tarde. Negativo: aparece antes." },
        "Desfase de la letra ",
        offset,
        this.offsetLabel,
      ),
      this.voiceControl,
      this.instrumentalControl,
      this.voiceHint,
    );
    this.el = h(
      "section",
      { class: "play-screen" },
      this.header,
      this.banner,
      h("div", { class: "stage-row" }, this.stage, this.scoreboard.el),
      this.lanes.el,
      this.controls,
    );
    this.onKey = this.onKey.bind(this);
    document.addEventListener("keydown", this.onKey);

    if (room) this.lanes.setPlayers(room.players);
    this.cleanups.push(
      hostSocket.on("room", (msg) => this.lanes.setPlayers(msg.players)),
      hostSocket.on("pitch_live", (msg) => this.lanes.push(msg)),
      hostSocket.on("game", (msg) => {
        this.gameActive = msg.mode === "competition";
        this.finishButton.hidden = !this.gameActive;
        this.seek.disabled = this.gameActive;
        if (this.gameActive) this.scoreboard.show(msg.simplified);
      }),
      hostSocket.on("scores", (msg) => this.scoreboard.render(msg.entries)),
      hostSocket.on("results", (msg) => {
        this.playback?.pause();
        showResults(msg, () => this.restart(), () => this.onExit());
      }),
      hostSocket.on("error", (msg) => this.showBanner(`⚠ ${msg.message}`)),
    );
  }

  async start(): Promise<void> {
    let lyrics: Lyrics | null = null;
    try {
      lyrics = await api.get<Lyrics>(`/api/songs/${this.song.id}/lyrics`);
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 404)) throw err;
    }
    if (lyrics?.source === "whisper") {
      this.showBanner(`⚠ ${LYRICS_LABELS.whisper} (puede contener errores)`);
    } else if (!lyrics && !this.song.instrumental) {
      this.showBanner(
        "No se encontró la letra de esta canción. Puedes buscarla con otro título o artista desde la biblioteca («Buscar letra»).",
      );
    }
    this.lyricsView = new LyricsView(lyrics);
    if (this.song.has_melody) {
      this.lanes.setMelody(await api.get<Melody>(`/api/songs/${this.song.id}/melody`).catch(() => null));
    }

    try {
      this.playback = await this.createPlayback();
    } catch (err) {
      this.stage.replaceChildren(h("p", { class: "error" }, `No se pudo cargar el audio: ${(err as Error).message}`));
      return;
    }
    this.playback.onStateChange = () => {
      this.updateButton();
      this.report();
    };
    this.playback.onEnded = () => this.onEnded();
    this.cleanups.push(startPlaybackReports(() => [this.song.id, this.playback, this.song.offset_ms]));
    // Start once (as soon as the socket is up); a reconnection must not reset the scores.
    let started = false;
    this.cleanups.push(
      hostSocket.onOpen(() => {
        if (started) return;
        started = true;
        this.startGame();
      }),
    );
    this.stage.replaceChildren(this.lyricsView.el);
    this.playButton.disabled = false;
    this.loop();
  }

  protected async createPlayback(): Promise<Playback> {
    if (this.song.origin === "spotify") {
      this.voiceHint.textContent = "Control de voz no disponible con Spotify";
      const spotify = await SpotifyPlayback.create(this.song);
      spotify.onError = (message) => this.showBanner(`⚠ ${message}`);
      return spotify;
    }
    const engine = new AudioEngine();
    if (this.song.has_stems) {
      await Promise.all([
        engine.load("vocals", `/api/songs/${this.song.id}/audio/vocals`),
        engine.load("instrumental", `/api/songs/${this.song.id}/audio/instrumental`),
      ]);
      this.enableVoiceControls(engine);
    } else {
      await engine.load("original", `/api/songs/${this.song.id}/audio/original`);
    }
    return engine;
  }

  private startGame(): void {
    hostSocket.send({ type: "start_game", song_id: this.song.id, mode: this.mode });
  }

  protected onEnded(): void {
    if (this.gameActive) this.finishGame();
  }

  private finishGame(): void {
    if (!this.gameActive) return;
    this.gameActive = false;
    this.playback?.pause();
    hostSocket.send({ type: "stop_game", publish: true });
  }

  private restart(): void {
    this.playback?.seek(0);
    this.scoreboard.render([]);
    this.startGame();
  }

  protected report(): void {
    reportPlayback(this.song.id, this.playback, this.song.offset_ms);
  }

  private enableVoiceControls(engine: AudioEngine): void {
    let level = 100;
    const apply = () => {
      const value = this.instrumentalOnly.checked ? 0 : level;
      this.voice.value = String(value);
      this.voiceLabel.textContent = `${value}%`;
      engine.setGain("vocals", value / 100);
    };
    this.voice.addEventListener("input", () => {
      level = Number(this.voice.value);
      this.instrumentalOnly.checked = level === 0;
      apply();
    });
    this.instrumentalOnly.addEventListener("change", () => {
      if (!this.instrumentalOnly.checked && level === 0) level = 100;
      apply();
    });
    for (const el of [this.voice, this.instrumentalOnly]) el.disabled = false;
    for (const el of [this.voiceControl, this.instrumentalControl]) {
      el.classList.remove("disabled");
      el.removeAttribute("title");
    }
    this.voiceHint.remove();
  }

  protected toggle(): void {
    if (!this.playback) return;
    if (this.playback.isPlaying()) this.playback.pause();
    else void this.playback.play();
  }

  private onKey(e: KeyboardEvent): void {
    if (e.code === "Space" && !(e.target instanceof HTMLInputElement && e.target.type !== "range")) {
      e.preventDefault();
      this.toggle();
    }
  }

  private updateButton(): void {
    this.playButton.textContent = this.playback?.isPlaying() ? "❚❚" : "▶";
  }

  private showOffset(): void {
    const v = this.song.offset_ms;
    this.offsetLabel.textContent = `${v > 0 ? "+" : ""}${(v / 1000).toFixed(2)} s`;
  }

  protected showBanner(text: string): void {
    this.banner.textContent = text;
    this.banner.hidden = false;
  }

  private loop = (): void => {
    this.frame = requestAnimationFrame(this.loop);
    if (!this.playback) return;
    const pos = this.playback.positionMs();
    const dur = this.playback.durationMs();
    this.lyricsView?.update(pos - this.song.offset_ms);
    this.lanes.draw(this.playback.isPlaying() ? pos : null);
    if (!this.seeking && dur > 0) this.seek.value = String(Math.round((pos / dur) * 1000));
    this.timeLabel.textContent = `${formatTime(pos / 1000)} / ${formatTime(dur / 1000)}`;
  };

  dispose(): void {
    cancelAnimationFrame(this.frame);
    document.removeEventListener("keydown", this.onKey);
    this.cleanups.forEach((fn) => fn());
    if (this.gameActive) hostSocket.send({ type: "stop_game", publish: false });
    this.playback?.dispose();
    reportPlayback(null, null);
  }
}
