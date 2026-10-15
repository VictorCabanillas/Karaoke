import "../shared/base.css";
import "./style.css";
import { ClockSync } from "../shared/clock";
import { $, h } from "../shared/dom";
import { runClockSync } from "../shared/sync";
import type { MicToServer, PitchFrame, Player, ServerToMic } from "../shared/types";
import { Socket } from "../shared/ws";
import { type CapturedFrame, MicCapture, ScreenWakeLock } from "./capture";
import { hzToMidi, midiToName } from "./yin";

const COLORS = ["#ff4f9a", "#4fd8ff", "#4ade80", "#fbbf24", "#a78bfa", "#fb7185", "#f97316", "#2dd4bf"];
const SEND_EVERY_MS = 100;
const CALIBRATION_MS = 3000;
const MIN_CONFIDENCE = 0.6;
const DEFAULT_GATE = 0.01;
const STORAGE = { name: "karaoke.name", color: "karaoke.color", player: "karaoke.player", latency: "karaoke.latency" };

const app = $("#app");
const room = (new URLSearchParams(location.search).get("room") ?? "").toUpperCase();

function stored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function store(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode */
  }
}

if (!room) {
  app.replaceChildren(
    h("section", { class: "card" }, h("h1", null, "🎤 Karaoke"), h("p", null, "Escanea el código QR de la pantalla.")),
  );
} else {
  showJoin();
}

function showJoin(error = ""): void {
  let color = stored(STORAGE.color) ?? COLORS[Math.floor(Math.random() * COLORS.length)];
  const name = h("input", {
    type: "text",
    maxLength: 20,
    required: true,
    placeholder: "Tu nombre",
    value: stored(STORAGE.name) ?? "",
    autocomplete: "nickname",
  });
  const swatches = COLORS.map((c) =>
    h("button", {
      type: "button",
      class: `swatch${c === color ? " selected" : ""}`,
      style: `background:${c}`,
      "aria-label": c,
      "data-color": c,
      onclick: () => {
        color = c;
        swatches.forEach((s) => s.classList.toggle("selected", s.dataset.color === c));
      },
    }),
  );
  app.replaceChildren(
    h(
      "form",
      {
        class: "card join",
        onsubmit: (e: Event) => {
          e.preventDefault();
          const value = name.value.trim();
          if (!value) return;
          store(STORAGE.name, value);
          store(STORAGE.color, color);
          new MicSession(value, color).start();
        },
      },
      h("h1", null, "🎤 Sala ", h("span", { class: "room-code" }, room)),
      error ? h("p", { class: "error" }, error) : null,
      h("label", null, "Nombre", name),
      h("div", { class: "label" }, "Color"),
      h("div", { class: "swatches" }, ...swatches),
      h("button", { type: "submit", class: "big" }, "Entrar"),
    ),
  );
  name.focus();
}

class MicSession {
  private socket: Socket<ServerToMic, MicToServer>;
  private clock = new ClockSync();
  private capture = new MicCapture();
  private wakeLock = new ScreenWakeLock();
  private player: Player | null = null;
  private pending: PitchFrame[] = [];
  private gate = DEFAULT_GATE;
  private latency = Number(stored(STORAGE.latency) ?? "100");
  private calibrating: number[] | null = null;
  private sendTimer: ReturnType<typeof setInterval> | undefined;
  private lastVoiced = 0;

  // UI
  private status = h("p", { class: "hint connection" }, "Conectando…");
  private header = h("header", { class: "me" });
  private activate = h("button", { class: "big" }, "Activar micrófono");
  private meter = h("div", { class: "meter" }, h("div", { class: "level" }));
  private note = h("div", { class: "note" }, "–");
  private song = h("p", { class: "song hint" });
  private line = h("p", { class: "line" });
  private nextLine = h("p", { class: "next-line" });
  private scoreCard = h("div", { class: "score-card", hidden: true });
  private results = h("div", { class: "results", hidden: true });
  private calibrateButton = h("button", { class: "secondary" }, "Calibrar ruido (3 s en silencio)");
  private calibrateInfo = h("p", { class: "hint small" });
  private latencyInput = h("input", { type: "number", min: "0", max: "1000", step: "10" });
  private micPanel = h("section", { class: "mic-panel", hidden: true });

  constructor(
    private name: string,
    private color: string,
  ) {
    this.socket = new Socket<ServerToMic, MicToServer>(`/ws/mic?room=${encodeURIComponent(room)}`);
  }

  start(): void {
    this.latencyInput.value = String(this.latency);
    this.render();
    runClockSync(this.socket, this.clock);
    this.socket.onOpen(() => {
      this.status.textContent = "Conectado";
      this.socket.send({
        type: "hello",
        name: this.name,
        color: this.color,
        player_id: stored(STORAGE.player),
      });
    });
    this.socket.onClose(() => (this.status.textContent = "Reconectando…"));
    this.socket.on("welcome", (msg) => {
      this.player = msg.player;
      store(STORAGE.player, msg.player.id);
      this.renderHeader();
      if (msg.player.calibrated) this.calibrateInfo.textContent = "Calibrado ✓";
      this.sendCalibration(false);
    });
    this.socket.on("error", (msg) => {
      this.socket.close();
      this.capture.stop();
      clearInterval(this.sendTimer);
      showJoin(msg.message);
    });
    this.socket.on("status", (msg) => {
      this.song.textContent = msg.song_title ? `${msg.playing ? "▶" : "❚❚"} ${msg.song_title}` : "Esperando canción…";
      this.line.textContent = msg.line ?? "";
      this.nextLine.textContent = msg.next_line ?? "";
    });

    this.socket.on("game", (msg) => {
      if (msg.mode === "competition") {
        this.results.hidden = true;
        this.showScore(0, 0, 0, 0, msg.simplified);
      } else {
        this.scoreCard.hidden = true;
      }
    });
    this.socket.on("score", (msg) => this.showScore(msg.score, msg.rank, msg.players, msg.combo));
    this.socket.on("results", (msg) => {
      this.scoreCard.hidden = true;
      const me = this.player?.id;
      const position = msg.ranking.findIndex((e) => e.player_id === me);
      const mine = msg.ranking[position];
      const awards = msg.awards.filter((a) => a.player_id === me);
      this.results.replaceChildren(
        h("h2", null, position === 0 ? "🏆 ¡Has ganado!" : "Resultados"),
        mine
          ? h("p", { class: "big-number" }, `${position + 1}º · ${mine.score.toLocaleString("es-ES")} puntos`)
          : h("p", null, "Esta vez no has puntuado."),
        ...awards.map((a) => h("p", { class: "award" }, `🎖 ${a.title}: ${a.detail}`)),
      );
      this.results.hidden = false;
    });

    this.capture.onFrame = (f) => this.onFrame(f);
    this.activate.addEventListener("click", () => void this.enableMic());
    this.calibrateButton.addEventListener("click", () => this.calibrate());
    this.latencyInput.addEventListener("change", () => {
      this.latency = Math.max(0, Math.min(1000, Number(this.latencyInput.value) || 0));
      store(STORAGE.latency, String(this.latency));
      this.sendCalibration(false);
    });
    this.sendTimer = setInterval(() => this.flush(), SEND_EVERY_MS);
  }

  private showScore(score: number, rank: number, players: number, combo: number, simplified?: boolean): void {
    this.scoreCard.hidden = false;
    if (simplified !== undefined) this.scoreCard.dataset.simplified = String(simplified);
    const parts = [
      h("span", { class: "big-number" }, score.toLocaleString("es-ES")),
      h("span", { class: "hint" }, rank ? `puesto ${rank} de ${players}` : "¡A cantar!"),
      combo >= 10 ? h("span", { class: "combo" }, `🔥 racha de ${Math.floor(combo / 10)} s`) : null,
      this.scoreCard.dataset.simplified === "true" ? h("small", { class: "hint" }, "puntuación simplificada") : null,
    ];
    this.scoreCard.replaceChildren(...parts.filter((p): p is HTMLElement => p !== null));
  }

  private render(): void {
    this.renderHeader();
    this.micPanel.replaceChildren(
      this.meter,
      this.note,
      h(
        "details",
        { class: "settings" },
        h("summary", null, "Calibración"),
        h("p", { class: "hint small" }, "Pide silencio en la sala y pulsa el botón para medir el ruido de fondo."),
        this.calibrateButton,
        this.calibrateInfo,
        h("label", { class: "row" }, "Retraso del micrófono (ms)", this.latencyInput),
      ),
    );
    app.replaceChildren(
      h(
        "section",
        { class: "card session" },
        this.header,
        this.status,
        this.activate,
        this.micPanel,
        this.scoreCard,
        this.results,
        h("div", { class: "lyrics" }, this.song, this.line, this.nextLine),
        h("p", { class: "hint small tip" }, "Canta con el móvil cerca de la boca."),
      ),
    );
  }

  private renderHeader(): void {
    this.header.replaceChildren(
      h("span", { class: "dot", style: `background:${this.color}` }),
      h("strong", null, this.player?.name ?? this.name),
      h("span", { class: "room-code" }, room),
    );
  }

  private async enableMic(): Promise<void> {
    this.activate.disabled = true;
    this.activate.textContent = "Activando…";
    try {
      await this.capture.start();
      await this.wakeLock.enable();
      this.activate.hidden = true;
      this.micPanel.hidden = false;
    } catch (err) {
      this.activate.disabled = false;
      this.activate.textContent = "Activar micrófono";
      const e = err as Error;
      this.status.textContent =
        e.name === "NotAllowedError" ? "Permiso de micrófono denegado. Actívalo en los ajustes del navegador." : e.message;
    }
  }

  private onFrame(frame: CapturedFrame): void {
    if (this.calibrating) this.calibrating.push(frame.rms);
    const voiced = frame.f0 !== null && frame.confidence >= MIN_CONFIDENCE && frame.rms >= this.gate;
    const level = Math.min(1, Math.sqrt(frame.rms / 0.3));
    (this.meter.firstElementChild as HTMLElement).style.width = `${(level * 100).toFixed(0)}%`;
    this.meter.classList.toggle("active", frame.rms >= this.gate);
    if (voiced) {
      this.note.textContent = midiToName(hzToMidi(frame.f0!));
      this.lastVoiced = frame.tClient;
    } else if (frame.tClient - this.lastVoiced > 400) {
      this.note.textContent = "–";
    }
    if (this.clock.synced && this.player) {
      this.pending.push({
        t: Math.round(this.clock.toServer(frame.tClient) * 10) / 10,
        f0: frame.f0 === null ? null : Math.round(frame.f0 * 100) / 100,
        conf: Math.round(frame.confidence * 1000) / 1000,
        rms: Math.round(frame.rms * 100000) / 100000,
      });
    }
  }

  private flush(): void {
    if (!this.pending.length) return;
    const frames = this.pending.splice(0, 100);
    if (!this.socket.send({ type: "pitch", frames })) this.pending = [];
  }

  private calibrate(): void {
    if (!this.capture.active || this.calibrating) return;
    this.calibrating = [];
    this.calibrateButton.disabled = true;
    let left = CALIBRATION_MS / 1000;
    this.calibrateInfo.textContent = `Silencio… ${left}`;
    const countdown = setInterval(() => {
      left -= 1;
      if (left > 0) this.calibrateInfo.textContent = `Silencio… ${left}`;
    }, 1000);
    setTimeout(() => {
      clearInterval(countdown);
      const samples = (this.calibrating ?? []).sort((a, b) => a - b);
      this.calibrating = null;
      this.calibrateButton.disabled = false;
      if (samples.length < 10) {
        this.calibrateInfo.textContent = "No llegó audio del micrófono. Inténtalo otra vez.";
        return;
      }
      const noise = samples[Math.floor(samples.length * 0.9)];
      this.gate = Math.max(0.003, noise * 2);
      this.calibrateInfo.textContent = `Calibrado ✓ (umbral ${this.gate.toFixed(3)})`;
      this.sendCalibration(true);
    }, CALIBRATION_MS);
  }

  private sendCalibration(measured: boolean): void {
    if (!this.player || (!measured && !this.player.calibrated)) return;
    this.socket.send({ type: "calibration", noise_rms: Math.min(1, this.gate), latency_ms: Math.round(this.latency) });
    if (measured) this.player.calibrated = true;
  }
}
