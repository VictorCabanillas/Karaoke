// Minimal typings for the Spotify Web Playback SDK (https://sdk.scdn.co/spotify-player.js).
declare namespace Spotify {
  interface PlayerInit {
    name: string;
    getOAuthToken(callback: (token: string) => void): void;
    volume?: number;
  }

  interface WebPlaybackError {
    message: string;
  }

  interface WebPlaybackInstance {
    device_id: string;
  }

  interface WebPlaybackState {
    paused: boolean;
    position: number;
    duration: number;
    track_window: { current_track: { uri: string } | null };
  }

  type ErrorEvent = "initialization_error" | "authentication_error" | "account_error" | "playback_error";

  class Player {
    constructor(init: PlayerInit);
    connect(): Promise<boolean>;
    disconnect(): void;
    addListener(event: "ready" | "not_ready", callback: (instance: WebPlaybackInstance) => void): boolean;
    addListener(event: "player_state_changed", callback: (state: WebPlaybackState | null) => void): boolean;
    addListener(event: ErrorEvent, callback: (error: WebPlaybackError) => void): boolean;
    getCurrentState(): Promise<WebPlaybackState | null>;
    pause(): Promise<void>;
    resume(): Promise<void>;
    seek(positionMs: number): Promise<void>;
    activateElement(): Promise<void>;
  }
}

interface Window {
  onSpotifyWebPlaybackSDKReady?: () => void;
  Spotify?: typeof Spotify;
}
