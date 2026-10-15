/** Common interface for whatever is producing the music (local file engine or Spotify). */
export interface Playback {
  /** Position the audience is hearing right now, in ms. */
  positionMs(): number;
  durationMs(): number;
  isPlaying(): boolean;
  play(): Promise<void> | void;
  pause(): void;
  seek(ms: number): void;
  /** Called on play/pause/seek/end so the server can follow the song clock. */
  onStateChange: (() => void) | null;
  onEnded: (() => void) | null;
  dispose(): void;
}
