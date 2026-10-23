import { AbortError, FastlsError } from "./errors.js";
import { Headers } from "./headers.js";
import { Native, type NativeInfo } from "./native.js";
import { scheduler } from "./scheduler.js";

export const MAX_BUFFERED_BODY = 32 * 1024 * 1024;

/** Owns one native request and all cancellation hooks. Internal to the SDK. */
export class Transfer {
  private readonly controller = new AbortController();
  private closed = false;
  private failure?: Error;
  private readonly externalAbort = () => this.close(new AbortError(undefined, { cause: this.external?.reason }));

  constructor(private readonly native: Native, private readonly handle: bigint,
    private readonly release: () => void, private readonly external?: AbortSignal) {}

  attachSignal(): void {
    this.external?.addEventListener("abort", this.externalAbort, { once: true });
    if (this.external?.aborted) this.externalAbort();
  }

  async headers(): Promise<NativeInfo> {
    return scheduler.wait(() => {
      this.ensureOpen();
      const info = this.native.call<NativeInfo>("info", this.handle);
      if (info.state === "error") throw new FastlsError(info.error ?? "native request failed");
      return info.state === "pending" ? undefined : info;
    }, this.controller.signal);
  }

  async next(): Promise<Buffer | null> {
    this.ensureOpen();
    // Each yielded chunk owns its allocation, so retaining it is always safe.
    const buffer = Buffer.allocUnsafe(65536);
    const result = await scheduler.wait(() => {
      this.ensureOpen();
      const { code, length } = this.native.read(this.handle, buffer);
      if (code === 1) return undefined;
      if (code === 2) return null;
      if (!length) throw new FastlsError("native data result was empty");
      // Small fragmented responses must not retain a whole 64KiB allocation per byte.
      return length === buffer.length ? buffer : Buffer.from(buffer.subarray(0, length));
    }, this.controller.signal);
    this.ensureOpen();
    return result;
  }

  ensureOpen(): void {
    if (this.closed) throw this.failure ?? new FastlsError("response is closed");
  }

  close(error?: Error): void {
    if (this.closed) return;
    this.closed = true;
    this.failure = error;
    this.external?.removeEventListener("abort", this.externalAbort);
    this.controller.abort(error ?? new FastlsError("response is closed"));
    try { this.native.call("close", this.handle); }
    finally { this.release(); }
  }
}

export class Response {
  constructor(readonly status: number, readonly headers: Headers,
    readonly match: Readonly<Record<string, unknown>>, readonly content: Buffer) {}
  get ok(): boolean { return this.status >= 200 && this.status < 300; }
  text(): string { return this.content.toString("utf8"); }
  json<T = unknown>(): T { return JSON.parse(this.text()) as T; }
}

export class StreamResponse {
  readonly status: number;
  readonly headers: Headers;
  readonly match: Readonly<Record<string, unknown>>;
  readonly body: AsyncIterable<Buffer>;
  private consumed = false;

  constructor(private readonly transfer: Transfer, info: NativeInfo) {
    this.status = info.status!;
    this.headers = new Headers(info.header_values, info.headers);
    this.match = Object.freeze({ ...info.match });
    this.body = { [Symbol.asyncIterator]: () => this.iterate() };
  }

  get ok(): boolean { return this.status >= 200 && this.status < 300; }

  private async *iterate(): AsyncGenerator<Buffer> {
    if (this.consumed) throw new FastlsError("response body already has a consumer");
    this.consumed = true;
    try {
      while (true) {
        const chunk = await this.transfer.next();
        if (chunk === null) return;
        yield chunk;
      }
    } finally {
      this.close();
    }
  }

  async read(): Promise<Buffer> {
    let content = Buffer.alloc(0);
    let length = 0;
    for await (const chunk of this.body) {
      const required = length + chunk.length;
      if (required > MAX_BUFFERED_BODY) throw new FastlsError("buffered response exceeds 32MiB; use streaming");
      if (required > content.length) {
        const grown = Buffer.allocUnsafe(Math.min(MAX_BUFFERED_BODY, Math.max(required, content.length * 2, 65536)));
        content.copy(grown, 0, 0, length);
        content = grown;
      }
      chunk.copy(content, length);
      length = required;
    }
    return length === content.length ? content : Buffer.from(content.subarray(0, length));
  }
  async text(): Promise<string> { return (await this.read()).toString("utf8"); }
  async json<T = unknown>(): Promise<T> { return JSON.parse(await this.text()) as T; }
  cancel(): void { this.transfer.close(new AbortError()); }
  close(): void { this.transfer.close(); }
}
