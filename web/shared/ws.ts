type Handler<T> = (msg: T) => void;

/** WebSocket that reconnects on its own and dispatches JSON messages by their `type`. */
export class Socket<In extends { type: string }, Out> {
  private ws: WebSocket | null = null;
  private handlers = new Map<string, Set<Handler<In>>>();
  private openHandlers = new Set<() => void>();
  private closeHandlers = new Set<() => void>();
  private retry = 0;
  private closed = false;
  private timer: ReturnType<typeof setTimeout> | undefined;

  constructor(private path: string) {
    this.connect();
  }

  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  setPath(path: string): void {
    this.path = path;
  }

  private connect(): void {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${scheme}://${location.host}${this.path}`);
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.openHandlers.forEach((fn) => fn());
    };
    ws.onmessage = (event) => {
      let msg: In;
      try {
        msg = JSON.parse(event.data as string) as In;
      } catch {
        return;
      }
      this.handlers.get(msg.type)?.forEach((fn) => fn(msg));
      this.handlers.get("*")?.forEach((fn) => fn(msg));
    };
    ws.onclose = () => {
      this.closeHandlers.forEach((fn) => fn());
      if (this.closed) return;
      const delay = Math.min(5000, 300 * 2 ** this.retry++);
      this.timer = setTimeout(() => this.connect(), delay);
    };
  }

  on<K extends In["type"]>(type: K | "*", fn: Handler<Extract<In, { type: K }>>): () => void {
    let set = this.handlers.get(type);
    if (!set) this.handlers.set(type, (set = new Set()));
    set.add(fn as Handler<In>);
    return () => set!.delete(fn as Handler<In>);
  }

  onOpen(fn: () => void): () => void {
    this.openHandlers.add(fn);
    if (this.connected) fn();
    return () => this.openHandlers.delete(fn);
  }

  onClose(fn: () => void): () => void {
    this.closeHandlers.add(fn);
    return () => this.closeHandlers.delete(fn);
  }

  send(msg: Out): boolean {
    if (!this.connected) return false;
    this.ws!.send(JSON.stringify(msg));
    return true;
  }

  /** Drop the current connection and reconnect right away (e.g. with a new path). */
  reconnect(): void {
    clearTimeout(this.timer);
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
    }
    this.retry = 0;
    this.connect();
  }

  close(): void {
    this.closed = true;
    clearTimeout(this.timer);
    this.ws?.close();
  }
}
