import { ClockSync } from "../shared/clock";
import { runClockSync } from "../shared/sync";
import type { HostToServer, Player, RoomMsg, ServerToHost } from "../shared/types";
import { Socket } from "../shared/ws";
import type { Playback } from "./playback";

/** The main screen's live connection to the server (progress, players, scores...). */
export const hostSocket = new Socket<ServerToHost, HostToServer>("/ws/host");
export const hostClock = new ClockSync();
runClockSync(hostSocket, hostClock);

/** Latest room state (code, QR URL, players). */
export let room: RoomMsg | null = null;
hostSocket.on("room", (msg) => (room = msg));

export function playerById(id: string): Player | undefined {
  return room?.players.find((p) => p.id === id);
}

const REPORT_EVERY_MS = 1000;

/** Tell the server where the song is (on every change and once a second while playing). */
export function reportPlayback(songId: string | null, playback: Playback | null, offsetMs = 0): void {
  hostSocket.send({
    type: "playback",
    song_id: songId,
    playing: playback?.isPlaying() ?? false,
    position_ms: playback?.positionMs() ?? 0,
    server_ms: hostClock.serverNow(),
    offset_ms: offsetMs,
  });
}

export function startPlaybackReports(getState: () => [string | null, Playback | null, number]): () => void {
  const send = () => reportPlayback(...getState());
  const interval = setInterval(() => {
    const [, playback] = getState();
    if (playback?.isPlaying()) send();
  }, REPORT_EVERY_MS);
  const offOpen = hostSocket.onOpen(send);
  return () => {
    clearInterval(interval);
    offOpen();
  };
}
