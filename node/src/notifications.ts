import { createServer, type Server, type Socket } from "node:net";
import { randomBytes, timingSafeEqual } from "node:crypto";
import { setImmediate as turn } from "node:timers/promises";
import { FastlsError } from "./errors.js";
import { asHandle, type Native } from "./native.js";

let budget = 32, resetScheduled = false;
function fairTurn(): Promise<void> | undefined {
  if (!resetScheduled) { resetScheduled = true; setImmediate(() => { budget = 32; resetScheduled = false; }); }
  return budget-- > 0 ? undefined : turn();
}
type State = { generation: number; waiters: Set<() => void> };

/** One authenticated wakeup socket per session; bytes and state stay in the ABI. */
export class Notifier {
  readonly states = new Map<bigint, State>();
  readonly sockets = new Set<Socket>();
  readonly token = randomBytes(32);
  private server?: Server;
  private writer?: Socket;
  private registered = false;
  private closed = false;
  private failure?: Error;
  private readonly pendingClose = new Set<Promise<void>>();
  private resolveReady!: () => void;
  private rejectReady!: (error: Error) => void;
  readonly ready = new Promise<void>((resolve, reject) => { this.resolveReady = resolve; this.rejectReady = reject; });
  isReady = false;
  setupWaiters = 0;

  constructor(private readonly native: Native, private readonly session: bigint) { void this.ready.catch(() => {}); }
  start(): Promise<void> {
    const timeout = setTimeout(() => this.fail(new FastlsError("could not start native notifications within 3 seconds")), 3000);
    void this.ready.then(() => clearTimeout(timeout), () => clearTimeout(timeout));
    try {
      const server = this.server = createServer(socket => this.accept(socket));
      server.on("error", error => this.fail(new FastlsError("could not start native notifications", { cause: error })));
      server.listen({ host: "127.0.0.1", port: 0, backlog: 2 }, () => {
        if (this.closed || this.failure) { server.close(); return; }
        try {
          const address = server.address();
          if (!address || typeof address === "string") throw new FastlsError("invalid notification endpoint");
          this.native.call("session_watch", this.session, address.port, this.token, this.token.length);
          this.registered = true;
        } catch (error) { this.fail(new FastlsError("could not start native notifications", { cause: error })); }
      });
    } catch (error) { this.fail(new FastlsError("could not start native notifications", { cause: error })); }
    return this.ready;
  }
  private accept(socket: Socket): void {
    socket.on("error", error => { if (socket === this.writer) this.fail(new FastlsError("native notification connection failed", { cause: error })); });
    if (this.closed || this.failure || this.writer || this.sockets.size >= 2) { socket.destroy(); return; }
    this.sockets.add(socket);
    const closing = new Promise<void>(resolve => socket.once("close", () => { this.sockets.delete(socket); resolve(); if (socket === this.writer && !this.closed && !this.failure) this.fail(new FastlsError("native notification connection closed")); }));
    this.pendingClose.add(closing); void closing.then(() => this.pendingClose.delete(closing));
    const handshake = Buffer.alloc(32); let length = 0;
    socket.setTimeout(1000, () => socket.destroy());
    socket.on("data", chunk => {
      try {
        if (this.closed || this.failure) return;
        if (socket !== this.writer) {
          const take = Math.min(32 - length, chunk.length);
          chunk.copy(handshake, length, 0, take); length += take; chunk = chunk.subarray(take);
          if (length !== 32) return;
          if (!timingSafeEqual(handshake, this.token) || this.writer) { socket.destroy(); return; }
          this.writer = socket; socket.setTimeout(0); this.server?.close();
          for (const candidate of this.sockets) if (candidate !== socket) candidate.destroy();
          this.isReady = true; this.resolveReady();
        }
        if (!chunk.length) return;
        if (chunk.some(byte => byte !== 1)) throw new FastlsError("invalid native notification packet");
        const batch = this.native.call<{ handles: (number | bigint)[]; overflow: boolean }>("session_events", this.session);
        if (!Array.isArray(batch.handles) || batch.handles.length > 256 || typeof batch.overflow !== "boolean") throw new FastlsError("invalid native notification batch");
        for (const handle of batch.overflow ? [...this.states.keys()] : batch.handles.map(asHandle)) this.pulse(handle);
      } catch (error) { this.fail(new FastlsError("native notifications failed", { cause: error })); }
    });
  }
  check(): void {
    if (this.failure) throw this.failure;
    if (this.closed) throw new FastlsError("notification session is closed");
  }
  track(handle: bigint): void { this.check(); this.states.set(handle, { generation: 0, waiters: new Set() }); }
  generation(handle: bigint): number { this.check(); return this.states.get(handle)?.generation ?? 0; }
  pulse(handle: bigint): void {
    const state = this.states.get(handle); if (!state) return;
    state.generation++; for (const wake of [...state.waiters]) wake();
  }
  forget(handle: bigint): void { const state = this.states.get(handle); this.states.delete(handle); if (state) for (const wake of [...state.waiters]) wake(); }
  wait(handle: bigint, generation: number, signal: AbortSignal): Promise<void> {
    this.check();
    if (signal.aborted) return Promise.reject(signal.reason);
    const state = this.states.get(handle);
    if (!state || state.generation !== generation) return Promise.resolve();
    return new Promise<void>((resolve, reject) => {
      const clean = () => { state.waiters.delete(wake); signal.removeEventListener("abort", abort); };
      const wake = () => { clean(); try { this.check(); resolve(); } catch (error) { reject(error); } };
      const abort = () => { clean(); reject(signal.reason); };
      state.waiters.add(wake); signal.addEventListener("abort", abort, { once: true });
    });
  }
  async probe<T>(handle: bigint, probe: () => T | undefined, signal: AbortSignal): Promise<T> {
    while (true) {
      const pause = fairTurn(); if (pause) await pause;
      this.check(); if (signal.aborted) throw signal.reason;
      const generation = this.generation(handle), value = probe();
      if (value !== undefined) return value;
      await this.wait(handle, generation, signal);
    }
  }
  private dispose(): void {
    if (this.registered) { this.registered = false; this.native.call("session_unwatch", this.session); }
    if (this.server?.listening) this.server.close();
    for (const socket of this.sockets) socket.destroy();
  }
  private fail(error: Error): void {
    if (this.closed || this.failure) return;
    this.failure = error; this.rejectReady(error);
    for (const handle of this.states.keys()) this.pulse(handle);
    // A failing ABI cleanup must not throw out of a socket event callback.
    try { this.dispose(); } catch { for (const socket of this.sockets) socket.destroy(); }
  }
  close(): void {
    if (this.closed) return;
    this.closed = true; this.rejectReady(new FastlsError("notification session is closed"));
    for (const handle of [...this.states.keys()]) this.forget(handle);
    this.dispose();
  }
  async closeAsync(): Promise<void> { this.close(); await Promise.all([...this.pendingClose]); }
}
