/**
 * NTP-like clock synchronization with the server over WebSocket.
 *
 * The client sends t0 (its clock), the server answers with its own time, the client receives it at t1:
 * offset = t_server - (t0 + t1) / 2. The median offset of the samples with the lowest round trip wins.
 */
export interface ClockSample {
  offset: number;
  rtt: number;
}

const MAX_SAMPLES = 20;
const BEST_SHARE = 0.34;

export class ClockSync {
  private samples: ClockSample[] = [];
  offset = 0;

  constructor(private now: () => number = () => performance.now()) {}

  get synced(): boolean {
    return this.samples.length > 0;
  }

  get rtt(): number {
    if (!this.samples.length) return 0;
    return Math.min(...this.samples.map((s) => s.rtt));
  }

  /** Start a ping: returns t0 to send. */
  ping(): number {
    return this.now();
  }

  /** Handle a pong; returns the new offset. */
  pong(t0: number, tServer: number): number {
    const t1 = this.now();
    const rtt = t1 - t0;
    if (rtt < 0) return this.offset;
    this.samples.push({ offset: tServer - (t0 + t1) / 2, rtt });
    if (this.samples.length > MAX_SAMPLES) this.samples.shift();
    this.offset = bestOffset(this.samples);
    return this.offset;
  }

  toServer(clientMs: number): number {
    return clientMs + this.offset;
  }

  toClient(serverMs: number): number {
    return serverMs - this.offset;
  }

  serverNow(): number {
    return this.toServer(this.now());
  }
}

export function bestOffset(samples: ClockSample[]): number {
  const sorted = [...samples].sort((a, b) => a.rtt - b.rtt);
  const best = sorted.slice(0, Math.max(1, Math.ceil(sorted.length * BEST_SHARE)));
  const offsets = best.map((s) => s.offset).sort((a, b) => a - b);
  const mid = offsets.length >> 1;
  return offsets.length % 2 ? offsets[mid] : (offsets[mid - 1] + offsets[mid]) / 2;
}
