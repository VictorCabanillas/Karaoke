import type { Playback } from "./playback";

interface Track {
  buffer: AudioBuffer;
  gain: GainNode;
  source: AudioBufferSourceNode | null;
}

const START_DELAY_S = 0.05;

/**
 * Plays one or more decoded tracks (e.g. vocals + instrumental) started at the very same
 * AudioContext instant. The master clock is audioContext.currentTime minus the output latency.
 */
export class AudioEngine implements Playback {
  readonly ctx = new AudioContext({ latencyHint: "playback" });
  private tracks = new Map<string, Track>();
  private playing = false;
  private startCtxTime = 0; // ctx time at which `startPos` is played
  private startPos = 0; // seconds into the song
  private pausedPos = 0;
  private generation = 0;
  onStateChange: (() => void) | null = null;
  onEnded: (() => void) | null = null;

  async load(name: string, url: string, gain = 1): Promise<void> {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`No se pudo descargar el audio (${res.status})`);
    const buffer = await this.ctx.decodeAudioData(await res.arrayBuffer());
    const node = this.ctx.createGain();
    node.gain.value = gain;
    node.connect(this.ctx.destination);
    this.tracks.set(name, { buffer, gain: node, source: null });
  }

  hasTrack(name: string): boolean {
    return this.tracks.has(name);
  }

  setGain(name: string, value: number): void {
    const track = this.tracks.get(name);
    if (track) track.gain.gain.setTargetAtTime(value, this.ctx.currentTime, 0.02);
  }

  private outputLatency(): number {
    return this.ctx.outputLatency || this.ctx.baseLatency || 0;
  }

  durationMs(): number {
    let max = 0;
    for (const t of this.tracks.values()) max = Math.max(max, t.buffer.duration);
    return max * 1000;
  }

  positionMs(): number {
    if (!this.playing) return this.pausedPos * 1000;
    const elapsed = this.ctx.currentTime - this.outputLatency() - this.startCtxTime;
    const pos = this.startPos + Math.max(0, elapsed);
    return Math.min(pos * 1000, this.durationMs());
  }

  isPlaying(): boolean {
    return this.playing;
  }

  async play(): Promise<void> {
    if (this.playing || this.tracks.size === 0) return;
    if (this.ctx.state === "suspended") await this.ctx.resume();
    const offset = this.pausedPos >= this.durationMs() / 1000 ? 0 : this.pausedPos;
    const when = this.ctx.currentTime + START_DELAY_S;
    const generation = ++this.generation;
    let longest: AudioBufferSourceNode | null = null;
    let longestDuration = -1;
    for (const track of this.tracks.values()) {
      const source = this.ctx.createBufferSource();
      source.buffer = track.buffer;
      source.connect(track.gain);
      source.start(when, Math.min(offset, track.buffer.duration));
      track.source = source;
      if (track.buffer.duration > longestDuration) {
        longestDuration = track.buffer.duration;
        longest = source;
      }
    }
    if (longest) {
      longest.onended = () => {
        if (generation !== this.generation) return; // stopped by pause/seek
        this.stopSources();
        this.playing = false;
        this.pausedPos = this.durationMs() / 1000;
        this.onStateChange?.();
        this.onEnded?.();
      };
    }
    this.startCtxTime = when;
    this.startPos = offset;
    this.playing = true;
    this.onStateChange?.();
  }

  pause(): void {
    if (!this.playing) return;
    this.pausedPos = this.positionMs() / 1000;
    this.generation++;
    this.stopSources();
    this.playing = false;
    this.onStateChange?.();
  }

  seek(ms: number): void {
    const wasPlaying = this.playing;
    if (wasPlaying) {
      this.generation++;
      this.stopSources();
      this.playing = false;
    }
    this.pausedPos = Math.max(0, Math.min(ms / 1000, this.durationMs() / 1000));
    if (wasPlaying) void this.play();
    else this.onStateChange?.();
  }

  private stopSources(): void {
    for (const track of this.tracks.values()) {
      if (track.source) {
        track.source.onended = null;
        try {
          track.source.stop();
        } catch {
          /* already stopped */
        }
        track.source.disconnect();
        track.source = null;
      }
    }
  }

  dispose(): void {
    this.generation++;
    this.stopSources();
    this.playing = false;
    void this.ctx.close();
  }
}
