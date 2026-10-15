import { describe, expect, it } from "vitest";
import { bestOffset, ClockSync } from "./clock";

describe("ClockSync", () => {
  it("estimates the offset from symmetric round trips", () => {
    let now = 1000;
    const clock = new ClockSync(() => now);
    const serverAhead = 5000;
    for (const rtt of [40, 10, 200, 12, 300]) {
      const t0 = clock.ping();
      now += rtt; // server answers exactly half way
      clock.pong(t0, t0 + rtt / 2 + serverAhead);
      now += 100;
    }
    expect(clock.offset).toBeCloseTo(serverAhead, 5);
    expect(clock.rtt).toBe(10);
    expect(clock.toServer(0)).toBeCloseTo(serverAhead);
  });

  it("ignores asymmetric slow samples", () => {
    const samples = [
      { offset: 100, rtt: 10 },
      { offset: 102, rtt: 12 },
      { offset: 400, rtt: 500 },
      { offset: 98, rtt: 11 },
      { offset: -300, rtt: 600 },
      { offset: 350, rtt: 450 },
    ];
    expect(bestOffset(samples)).toBe(100);
  });
});
