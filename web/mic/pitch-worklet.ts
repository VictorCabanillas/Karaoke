/// <reference path="./worklet-env.d.ts" />
import { Yin } from "./yin";

/** Analysis window and hop in samples after decimation (~43 ms / ~21 ms at 24 kHz). */
const WINDOW = 1024;
const HOP = 512;

export interface PitchMessage {
  f0: number | null;
  confidence: number;
  rms: number;
  /** AudioContext time (s) of the end of the analysed window. */
  time: number;
}

/** Runs YIN on the microphone signal and posts one PitchMessage per hop. */
class PitchProcessor extends AudioWorkletProcessor {
  private decimation = sampleRate >= 44100 ? 2 : 1;
  private yin = new Yin({ sampleRate: sampleRate / this.decimation, minHz: 70, maxHz: 1100 });
  private ring = new Float32Array(WINDOW);
  private window = new Float32Array(WINDOW);
  private pos = 0; // next write index in the ring
  private filled = 0;
  private sinceLast = 0;
  private carry = 0;
  private carryCount = 0;

  process(inputs: Float32Array[][]): boolean {
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    for (let i = 0; i < channel.length; i++) {
      // Average groups of samples: cheap low-pass + decimation.
      this.carry += channel[i];
      if (++this.carryCount < this.decimation) continue;
      this.ring[this.pos] = this.carry / this.decimation;
      this.carry = 0;
      this.carryCount = 0;
      this.pos = (this.pos + 1) % WINDOW;
      if (this.filled < WINDOW) this.filled++;

      if (this.filled === WINDOW && ++this.sinceLast >= HOP) {
        this.sinceLast = 0;
        // Unroll the ring (oldest sample first) into the analysis window.
        this.window.set(this.ring.subarray(this.pos), 0);
        this.window.set(this.ring.subarray(0, this.pos), WINDOW - this.pos);
        const result = this.yin.detect(this.window);
        const msg: PitchMessage = { ...result, time: currentTime + (i + 1) / sampleRate };
        this.port.postMessage(msg);
      }
    }
    return true;
  }
}

registerProcessor("pitch-processor", PitchProcessor);
