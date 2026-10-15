/// <reference path="./spotify-sdk.d.ts" />
import { api } from "../shared/api";
import type { Song, SpotifyToken } from "../shared/types";
import type { Playback } from "./playback";

const SDK_URL = "https://sdk.scdn.co/spotify-player.js";
const POLL_MS = 500;
const READY_TIMEOUT_MS = 15_000;
const END_MARGIN_MS = 1500;

let sdkLoading: Promise<void> | null = null;

function loadSdk(): Promise<void> {
  if (window.Spotify) return Promise.resolve();
  sdkLoading ??= new Promise<void>((resolve, reject) => {
    window.onSpotifyWebPlaybackSDKReady = () => resolve();
    const script = document.createElement("script");
    script.src = SDK_URL;
    script.onerror = () => {
      sdkLoading = null;
      reject(new Error("No se pudo cargar el reproductor de Spotify (¿sin conexión a internet?)"));
    };
    document.head.append(script);
  });
  return sdkLoading;
}

let cachedToken: SpotifyToken | null = null;

async function accessToken(): Promise<string> {
  if (!cachedToken || cachedToken.expires_at * 1000 - Date.now() < 60_000) {
    cachedToken = await api.get<SpotifyToken>("/api/spotify/token");
  }
  return cachedToken.access_token;
}

const ERROR_MESSAGES: Record<Spotify.ErrorEvent, string> = {
  initialization_error: "Este navegador no puede reproducir Spotify",
  authentication_error: "Spotify no aceptó la sesión: vuelve a conectar la cuenta",
  account_error: "Hace falta una cuenta de Spotify Premium",
  playback_error: "Spotify no pudo reproducir la canción",
};

/**
 * The laptop becomes a Spotify device (Web Playback SDK). The SDK only reports the position now and
 * then: in between it is extrapolated with performance.now() and corrected on every update.
 */
export class SpotifyPlayback implements Playback {
  private player!: Spotify.Player;
  private deviceId = "";
  private started = false;
  private paused = true;
  private lastPosition = 0;
  private lastAt = performance.now();
  private duration: number;
  private ended = false;
  private poll: ReturnType<typeof setInterval> | undefined;
  onStateChange: (() => void) | null = null;
  onEnded: (() => void) | null = null;
  onError: ((message: string) => void) | null = null;

  private constructor(private song: Song) {
    this.duration = song.duration_s * 1000;
  }

  static async create(song: Song): Promise<SpotifyPlayback> {
    if (!song.spotify_uri) throw new Error("La canción no tiene enlace de Spotify");
    await loadSdk();
    const playback = new SpotifyPlayback(song);
    await playback.connect();
    return playback;
  }

  private connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Spotify no respondió a tiempo")), READY_TIMEOUT_MS);
      this.player = new window.Spotify!.Player({
        name: "Karaoke entre amigos",
        getOAuthToken: (cb) => void accessToken().then(cb, () => cb("")),
        volume: 1,
      });
      this.player.addListener("ready", ({ device_id }) => {
        clearTimeout(timer);
        this.deviceId = device_id;
        resolve();
      });
      for (const event of Object.keys(ERROR_MESSAGES) as Spotify.ErrorEvent[]) {
        this.player.addListener(event, ({ message }) => {
          const text = `${ERROR_MESSAGES[event]} (${message})`;
          clearTimeout(timer);
          reject(new Error(text));
          this.onError?.(text);
        });
      }
      this.player.addListener("player_state_changed", (state) => this.apply(state));
      void this.player.connect().then((ok) => {
        if (!ok) {
          clearTimeout(timer);
          reject(new Error("No se pudo conectar con Spotify"));
        }
      });
      this.poll = setInterval(() => void this.player.getCurrentState().then((s) => this.apply(s)), POLL_MS);
    });
  }

  private apply(state: Spotify.WebPlaybackState | null): void {
    if (!state || !this.started) return;
    const track = state.track_window.current_track;
    if (track && track.uri !== this.song.spotify_uri) return; // something else is playing on this device
    const before = this.positionMs();
    const wasPaused = this.paused;
    this.lastPosition = state.position;
    this.lastAt = performance.now();
    this.paused = state.paused;
    if (state.duration) this.duration = state.duration;

    const finished =
      state.position >= this.duration - 300 ||
      (state.paused && state.position === 0 && before >= this.duration - END_MARGIN_MS);
    if (finished && !this.ended) {
      this.ended = true;
      this.paused = true;
      this.lastPosition = this.duration;
      this.onStateChange?.();
      this.onEnded?.();
      return;
    }
    if (wasPaused !== this.paused || Math.abs(before - state.position) > 1000) this.onStateChange?.();
  }

  positionMs(): number {
    if (this.paused) return this.lastPosition;
    return Math.min(this.duration, this.lastPosition + (performance.now() - this.lastAt));
  }

  durationMs(): number {
    return this.duration;
  }

  isPlaying(): boolean {
    return !this.paused;
  }

  async play(): Promise<void> {
    await this.player.activateElement();
    if (this.started && !this.ended) {
      await this.player.resume();
    } else {
      const from = this.ended ? 0 : this.lastPosition;
      const res = await fetch(`https://api.spotify.com/v1/me/player/play?device_id=${this.deviceId}`, {
        method: "PUT",
        headers: { Authorization: `Bearer ${await accessToken()}`, "Content-Type": "application/json" },
        body: JSON.stringify({ uris: [this.song.spotify_uri], position_ms: Math.round(from) }),
      });
      if (!res.ok && res.status !== 202) {
        this.onError?.(`Spotify no pudo empezar la canción (${res.status})`);
        return;
      }
      this.started = true;
      this.ended = false;
      this.lastPosition = from;
    }
    this.paused = false;
    this.lastAt = performance.now();
    this.onStateChange?.();
  }

  pause(): void {
    if (this.paused) return;
    this.lastPosition = this.positionMs();
    this.lastAt = performance.now();
    this.paused = true;
    void this.player.pause();
    this.onStateChange?.();
  }

  seek(ms: number): void {
    this.lastPosition = Math.max(0, Math.min(ms, this.duration));
    this.lastAt = performance.now();
    this.ended = false;
    if (this.started) void this.player.seek(Math.round(this.lastPosition));
    this.onStateChange?.();
  }

  dispose(): void {
    clearInterval(this.poll);
    if (this.started) void this.player.pause();
    this.player?.disconnect();
  }
}
