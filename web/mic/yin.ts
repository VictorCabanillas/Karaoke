/** YIN fundamental-frequency estimator (de Cheveigné & Kawahara, 2002). */

export interface PitchResult {
  /** Fundamental frequency in Hz, or null when no clear pitch was found. */
  f0: number | null;
  /** 0..1, 1 - aperiodicity of the chosen period. */
  confidence: number;
  rms: number;
}

export interface YinOptions {
  sampleRate: number;
  minHz?: number;
  maxHz?: number;
  threshold?: number;
}

export class Yin {
  readonly sampleRate: number;
  readonly tauMin: number;
  readonly tauMax: number;
  private threshold: number;
  private diff: Float32Array;

  constructor(opts: YinOptions) {
    this.sampleRate = opts.sampleRate;
    this.tauMin = Math.max(2, Math.floor(opts.sampleRate / (opts.maxHz ?? 1100)));
    this.tauMax = Math.ceil(opts.sampleRate / (opts.minHz ?? 70));
    this.threshold = opts.threshold ?? 0.15;
    this.diff = new Float32Array(this.tauMax + 1);
  }

  /** Minimum buffer length needed for detect(). */
  get minLength(): number {
    return this.tauMax * 2;
  }

  detect(buf: Float32Array): PitchResult {
    let energy = 0;
    for (let i = 0; i < buf.length; i++) energy += buf[i] * buf[i];
    const rms = Math.sqrt(energy / buf.length);

    const w = buf.length - this.tauMax;
    if (w <= 0 || rms === 0) return { f0: null, confidence: 0, rms };
    const d = this.diff;

    // Difference function.
    for (let tau = 1; tau <= this.tauMax; tau++) {
      let sum = 0;
      for (let j = 0; j < w; j++) {
        const delta = buf[j] - buf[j + tau];
        sum += delta * delta;
      }
      d[tau] = sum;
    }
    // Cumulative mean normalized difference.
    d[0] = 1;
    let running = 0;
    for (let tau = 1; tau <= this.tauMax; tau++) {
      running += d[tau];
      d[tau] = running > 0 ? (d[tau] * tau) / running : 1;
    }
    // Absolute threshold: first dip below the threshold, then walk down to its local minimum.
    let tau = -1;
    for (let t = this.tauMin; t <= this.tauMax; t++) {
      if (d[t] < this.threshold) {
        while (t + 1 <= this.tauMax && d[t + 1] < d[t]) t++;
        tau = t;
        break;
      }
    }
    if (tau < 0) {
      // No dip under the threshold (breathy voice, vibrato, background music): fall back to the global
      // minimum and let its confidence decide. Discarding these frames lost over half of real singing.
      tau = this.tauMin;
      for (let t = this.tauMin + 1; t <= this.tauMax; t++) if (d[t] < d[tau]) tau = t;
    }
    // Parabolic interpolation around the minimum.
    let refined = tau;
    if (tau > 1 && tau < this.tauMax) {
      const a = d[tau - 1];
      const b = d[tau];
      const c = d[tau + 1];
      const denom = a + c - 2 * b;
      if (denom !== 0) refined = tau + (a - c) / (2 * denom);
    }
    return { f0: this.sampleRate / refined, confidence: Math.max(0, Math.min(1, 1 - d[tau])), rms };
  }
}

export function hzToMidi(hz: number): number {
  return 69 + 12 * Math.log2(hz / 440);
}

const NOTE_NAMES = ["Do", "Do#", "Re", "Re#", "Mi", "Fa", "Fa#", "Sol", "Sol#", "La", "La#", "Si"];

export function midiToName(midi: number): string {
  const n = Math.round(midi);
  return `${NOTE_NAMES[((n % 12) + 12) % 12]}${Math.floor(n / 12) - 1}`;
}
