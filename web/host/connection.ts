import { Socket } from "../shared/ws";
import type { SongUpdatedMsg } from "../shared/types";

export type HostIn = SongUpdatedMsg;
export type HostOut = never;

/** The main screen's live connection to the server (progress, players, scores...). */
export const hostSocket = new Socket<HostIn, HostOut>("/ws/host");
