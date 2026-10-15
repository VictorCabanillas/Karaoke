import workletUrl from "./pitch-worklet.ts?worker&url";
import type { PitchMessage } from "./pitch-worklet";

export interface CapturedFrame {
  /** performance.now() time of the end of the analysed window. */
  tClient: number;
  f0: number | null;
  confidence: number;
  rms: number;
}

/** Microphone capture + pitch detection in an AudioWorklet. Only analysis data leaves this class. */
export class MicCapture {
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  onFrame: ((frame: CapturedFrame) => void) | null = null;

  get active(): boolean {
    return this.ctx !== null;
  }

  /** Must be called from a user gesture (tap). */
  async start(): Promise<void> {
    if (this.ctx) return;
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("Este navegador no permite usar el micrófono aquí (¿la página no está en HTTPS?)");
    }
    const ctx = new AudioContext({ latencyHint: "interactive" });
    this.ctx = ctx;
    try {
      // The browser's voice processing alters the signal and hurts pitch detection.
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 },
      });
      await ctx.audioWorklet.addModule(workletUrl);
      if (ctx.state === "suspended") await ctx.resume();
      const source = ctx.createMediaStreamSource(this.stream);
      const node = new AudioWorkletNode(ctx, "pitch-processor", { numberOfInputs: 1, numberOfOutputs: 1 });
      node.port.onmessage = (event: MessageEvent<PitchMessage>) => {
        const msg = event.data;
        const ageMs = Math.max(0, (ctx.currentTime - msg.time) * 1000);
        this.onFrame?.({ tClient: performance.now() - ageMs, f0: msg.f0, confidence: msg.confidence, rms: msg.rms });
      };
      // Keep the node in the rendering graph without making any sound.
      const mute = ctx.createGain();
      mute.gain.value = 0;
      source.connect(node).connect(mute).connect(ctx.destination);
      this.node = node;
    } catch (err) {
      this.stop();
      throw err;
    }
  }

  stop(): void {
    this.node?.port.close();
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    void this.ctx?.close();
    this.node = null;
    this.stream = null;
    this.ctx = null;
  }
}

/** Keep the phone screen on while singing (Wake Lock API, when available). */
export class ScreenWakeLock {
  private lock: WakeLockSentinel | null = null;
  private wanted = false;

  constructor() {
    document.addEventListener("visibilitychange", () => {
      if (this.wanted && document.visibilityState === "visible") void this.acquire();
    });
  }

  async enable(): Promise<void> {
    this.wanted = true;
    await this.acquire();
  }

  private async acquire(): Promise<void> {
    if (!("wakeLock" in navigator) || this.lock) return;
    try {
      this.lock = await navigator.wakeLock.request("screen");
      this.lock.addEventListener("release", () => (this.lock = null));
    } catch {
      /* not allowed (battery saver...) */
    }
  }
}
