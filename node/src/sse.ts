import { setImmediate } from "node:timers/promises";
import { FastlsError } from "./errors.js";
import type { StreamResponse } from "./response.js";
import type { RequestOptions } from "./session.js";

export interface SSEOptions extends RequestOptions {
  /** Separate limits for a raw line and accumulated UTF-8 data; defaults to 1 MiB. */
  maxEventBytes?: number;
  /** Sets Last-Event-ID and the parser's initial ID. Reconnection is manual. */
  lastEventId?: string;
}

export class ServerSentEvent {
  constructor(readonly data: string, readonly event = "message", readonly id = "", readonly retry?: number) {
    Object.freeze(this);
  }
  json<T = unknown>(): T { return JSON.parse(this.data) as T; }
}

export function validateSSEOptions(maximum: number, initialId?: string): void {
  if (!Number.isSafeInteger(maximum) || maximum < 1 || maximum > 64 * 1024 * 1024) {
    throw new RangeError("maxEventBytes must be 1..67108864");
  }
  if (initialId !== undefined) {
    if (typeof initialId !== "string") throw new TypeError("lastEventId must be a string");
    if (/[\0\r\n]/.test(initialId)) throw new TypeError("lastEventId must not contain NUL, CR or LF");
    if (Buffer.byteLength(initialId) > maximum) throw new RangeError("lastEventId exceeds maxEventBytes");
  }
}

/** Internal pull parser. feed() pauses at each event, including within one chunk. */
export class SSEDecoder {
  lastEventId: string;
  retry?: number;
  private id: string;
  private blockRetry?: number;
  private line = Buffer.alloc(0);
  private length = 0;
  private data: string[] = [];
  private size = 0;
  private event = "";
  private first = true;
  private skipLF = false;

  constructor(private readonly maximum: number, initialId = "") {
    this.id = this.lastEventId = initialId;
  }
  private append(part: Uint8Array): void {
    const required = this.length + part.length;
    if (required > this.maximum) throw new FastlsError("SSE line exceeds maxEventBytes");
    if (required > this.line.length) {
      const grown = Buffer.allocUnsafe(Math.min(this.maximum, Math.max(required, this.line.length * 2, 256)));
      this.line.copy(grown, 0, 0, this.length);
      this.line = grown;
    }
    this.line.set(part, this.length);
    this.length = required;
  }
  private processLine(): ServerSentEvent | undefined {
    let line = this.line.toString("utf8", 0, this.length);
    this.length = 0;
    if (this.first) { if (line.startsWith("\ufeff")) line = line.slice(1); this.first = false; }
    if (line === "") {
      this.lastEventId = this.id;
      const event = this.data.length ? new ServerSentEvent(this.data.join("\n"), this.event || "message", this.id, this.blockRetry) : undefined;
      this.data = []; this.size = 0; this.event = ""; this.blockRetry = undefined;
      return event;
    }
    if (line.startsWith(":")) return;
    const colon = line.indexOf(":"), field = colon < 0 ? line : line.slice(0, colon);
    let value = colon < 0 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "data") {
      const size = this.size + Buffer.byteLength(value) + (this.data.length ? 1 : 0);
      if (size > this.maximum) throw new FastlsError("SSE event exceeds maxEventBytes");
      this.size = size; this.data.push(value);
    } else if (field === "event") this.event = value;
    else if (field === "id" && !value.includes("\0")) this.id = value;
    else if (field === "retry" && /^[0-9]+$/.test(value)) {
      const digits = value.replace(/^0+/, "") || "0", ceiling = "2147483647";
      this.retry = this.blockRetry = digits.length > ceiling.length || (digits.length === ceiling.length && digits > ceiling) ? 2147483647 : Number(digits);
    }
  }
  *feed(chunk: Uint8Array): Generator<ServerSentEvent> {
    if (!chunk.length) return;
    let start = 0;
    if (this.skipLF) { if (chunk[0] === 10) start = 1; this.skipLF = false; }
    for (let i = start; i < chunk.length; i++) {
      if (chunk[i] !== 10 && chunk[i] !== 13) continue;
      this.append(chunk.subarray(start, i));
      const event = this.processLine();
      if (chunk[i] === 13) {
        if (chunk[i + 1] === 10) i++;
        else if (i + 1 === chunk.length) this.skipLF = true;
      }
      start = i + 1;
      if (event) yield event;
    }
    this.append(chunk.subarray(start));
  }
  finish(): void {
    this.line = Buffer.alloc(0); this.length = 0; this.data = []; this.size = 0;
    this.event = ""; this.id = this.lastEventId;
  }
}

export class SSEStream implements AsyncIterable<ServerSentEvent> {
  private readonly decoder: SSEDecoder;
  private started = false;
  private closed = false;

  constructor(readonly response: StreamResponse, maximum = 1024 * 1024, initialId?: string) {
    validateSSEOptions(maximum, initialId);
    this.decoder = new SSEDecoder(maximum, initialId);
    if (response.status === 204) return;
    if (response.status !== 200) throw new FastlsError(`SSE requires HTTP 200, received ${response.status}`);
    const types = response.headers.getAll("content-type");
    if (types.length !== 1 || types[0].split(";", 1)[0].replace(/^[ \t]+|[ \t]+$/g, "").toLowerCase() !== "text/event-stream") {
      throw new FastlsError("SSE requires Content-Type text/event-stream");
    }
    const encodings = response.wireHeaders.getAll("content-encoding").flatMap(value => value.split(",")).map(value => value.trim().toLowerCase());
    if (!response.decoded && encodings.some(value => value !== "" && value !== "identity")) {
      throw new FastlsError("SSE requires decoded content; enable decodeContent");
    }
  }
  get lastEventId(): string { return this.decoder.lastEventId; }
  get retry(): number | undefined { return this.decoder.retry; }
  private checkOpen(): void {
    if (this.closed) throw new FastlsError("SSE stream is closed or cancelled");
    this.response.ensureOpen();
  }
  [Symbol.asyncIterator](): AsyncGenerator<ServerSentEvent> {
    this.checkOpen();
    if (this.started) throw new FastlsError("SSE stream permits only one consumer");
    this.started = true;
    return this.events();
  }
  private async *events(): AsyncGenerator<ServerSentEvent> {
    try {
      if (this.response.status === 204) return;
      for await (const chunk of this.response.body) {
        for (const event of this.decoder.feed(chunk)) {
          this.checkOpen();
          yield event;
          // Even many events in one native read must let timers and cancellation run.
          await setImmediate();
        }
      }
    } finally { await this.closeAsync(); }
  }
  cancel(): void { this.closed = true; this.decoder.finish(); this.response.cancel(); }
  close(): void { this.closed = true; this.decoder.finish(); this.response.close(); }
  async closeAsync(): Promise<void> { this.close(); await this.response.closeAsync(); }
}
