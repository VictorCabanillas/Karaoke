import { h } from "../shared/dom";
import type { Melody, PitchLiveMsg, Player } from "../shared/types";
import { hostClock } from "./connection";

const PAST_MS = 4000;
const FUTURE_MS = 2000;
const KEEP_MS = 10_000;
const LANE_HEIGHT = 64;
const LABEL_WIDTH = 90;

interface TrailPoint {
  songMs: number | null;
  localMs: number;
  midi: number | null;
}

/** Fold `midi` into the octave closest to `reference`, so low/high voices draw on the same line. */
export function foldOctave(midi: number, reference: number): number {
  return midi + 12 * Math.round((reference - midi) / 12);
}

/** One pitch lane per player under the lyrics: reference melody + the player's live pitch trail. */
export class PitchLanes {
  readonly el: HTMLElement;
  private canvas = h("canvas");
  private ctx = this.canvas.getContext("2d")!;
  private trails = new Map<string, TrailPoint[]>();
  private melody: Melody | null = null;
  private range: [number, number] = [45, 77];
  private players: Player[] = [];

  constructor() {
    this.el = h("div", { class: "lanes", hidden: true }, this.canvas);
  }

  setMelody(melody: Melody | null): void {
    this.melody = melody;
    const voiced = (melody?.midi ?? []).filter((m): m is number => m !== null).sort((a, b) => a - b);
    if (voiced.length > 20) {
      const lo = voiced[Math.floor(voiced.length * 0.05)];
      const hi = voiced[Math.floor(voiced.length * 0.95)];
      const mid = (lo + hi) / 2;
      const half = Math.max(7, (hi - lo) / 2 + 3);
      this.range = [mid - half, mid + half];
    }
  }

  setPlayers(players: Player[]): void {
    this.players = players.filter((p) => p.connected);
    this.el.hidden = this.players.length === 0;
  }

  push(msg: PitchLiveMsg): void {
    const now = performance.now();
    for (const item of msg.players) {
      let trail = this.trails.get(item.player_id);
      if (!trail) this.trails.set(item.player_id, (trail = []));
      for (const f of item.frames) {
        trail.push({ songMs: f.song_ms, localMs: hostClock.toClient(f.server_ms), midi: f.midi });
      }
      while (trail.length && trail[0].localMs < now - KEEP_MS) trail.shift();
    }
  }

  private referenceAt(songMs: number): number | null {
    const m = this.melody;
    if (!m) return null;
    const i = Math.round(songMs / m.hop_ms);
    return i >= 0 && i < m.midi.length ? m.midi[i] : null;
  }

  /** songMs: current song position when playing, otherwise null (trails follow the wall clock). */
  draw(songMs: number | null): void {
    if (this.el.hidden) return;
    const dpr = window.devicePixelRatio || 1;
    const width = this.canvas.clientWidth;
    const height = this.players.length * LANE_HEIGHT;
    if (this.canvas.width !== Math.round(width * dpr) || this.canvas.height !== Math.round(height * dpr)) {
      this.canvas.width = Math.round(width * dpr);
      this.canvas.height = Math.round(height * dpr);
      this.canvas.style.height = `${height}px`;
    }
    const ctx = this.ctx;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const usingSong = songMs !== null;
    const now = usingSong ? songMs : performance.now();
    const t0 = now - PAST_MS;
    const plotWidth = width - LABEL_WIDTH;
    const x = (t: number) => LABEL_WIDTH + ((t - t0) / (PAST_MS + FUTURE_MS)) * plotWidth;
    const [lo, hi] = this.range;
    const center = (lo + hi) / 2;

    this.players.forEach((player, lane) => {
      const top = lane * LANE_HEIGHT;
      const y = (midi: number) => top + LANE_HEIGHT - 6 - ((midi - lo) / (hi - lo)) * (LANE_HEIGHT - 12);
      ctx.fillStyle = lane % 2 ? "rgba(255,255,255,0.03)" : "rgba(255,255,255,0.06)";
      ctx.fillRect(LABEL_WIDTH, top, plotWidth, LANE_HEIGHT);

      // Reference melody.
      if (usingSong && this.melody) {
        ctx.strokeStyle = "rgba(255,255,255,0.35)";
        ctx.lineWidth = 6;
        ctx.lineCap = "round";
        ctx.beginPath();
        let drawing = false;
        const hop = this.melody.hop_ms;
        for (let t = Math.floor(t0 / hop) * hop; t <= now + FUTURE_MS; t += hop) {
          const ref = this.referenceAt(t);
          if (ref === null) {
            drawing = false;
            continue;
          }
          if (drawing) ctx.lineTo(x(t), y(ref));
          else ctx.moveTo(x(t), y(ref));
          drawing = true;
        }
        ctx.stroke();
      }

      // Player trail.
      ctx.fillStyle = player.color;
      for (const p of this.trails.get(player.id) ?? []) {
        if (p.midi === null) continue;
        const t = usingSong ? p.songMs : p.localMs;
        if (t === null || t < t0 || t > now) continue;
        const ref = usingSong ? this.referenceAt(t) : null;
        const shown = foldOctave(p.midi, ref ?? center);
        ctx.beginPath();
        ctx.arc(x(t), y(Math.min(hi, Math.max(lo, shown))), 3.5, 0, Math.PI * 2);
        ctx.fill();
      }

      // Label.
      ctx.fillStyle = player.color;
      ctx.fillRect(0, top + 8, 6, LANE_HEIGHT - 16);
      ctx.fillStyle = "#f2eefc";
      ctx.font = "600 15px system-ui, sans-serif";
      ctx.textBaseline = "middle";
      ctx.fillText(player.name, 14, top + LANE_HEIGHT / 2, LABEL_WIDTH - 18);
    });

    // "Now" marker.
    ctx.strokeStyle = "rgba(255,79,154,0.8)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x(now), 0);
    ctx.lineTo(x(now), height);
    ctx.stroke();
  }
}
