import { ClockSync } from "./clock";
import type { ClockPing, ClockPong } from "./types";
import type { Socket } from "./ws";

const BURST = 8;
const BURST_GAP_MS = 120;
const REFRESH_EVERY_MS = 10_000;
const REFRESH_BURST = 3;

/** Keep `clock` synced with the server over `socket`: a burst on connect, then a few pings every ~10 s. */
export function runClockSync<In extends { type: string }, Out>(socket: Socket<In, Out>, clock: ClockSync): () => void {
  const timers: ReturnType<typeof setTimeout>[] = [];
  const burst = (n: number) => {
    for (let i = 0; i < n; i++) {
      timers.push(
        setTimeout(() => {
          const ping: ClockPing = { type: "ping", t0: clock.ping() };
          socket.send(ping as Out);
        }, i * BURST_GAP_MS),
      );
    }
  };
  const offPong = socket.on("pong" as In["type"], (msg) => {
    const pong = msg as unknown as ClockPong;
    clock.pong(pong.t0, pong.t_server);
  });
  const offOpen = socket.onOpen(() => burst(BURST));
  const interval = setInterval(() => burst(REFRESH_BURST), REFRESH_EVERY_MS);
  return () => {
    offPong();
    offOpen();
    clearInterval(interval);
    timers.forEach(clearTimeout);
  };
}
