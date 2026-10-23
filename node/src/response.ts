import { AbortError, FastlsError, FastlsTimeoutError, responseError } from "./errors.js";
import { Headers } from "./headers.js";
import { Native, type NativeInfo } from "./native.js";
import { scheduler } from "./scheduler.js";

export const MAX_BUFFERED_BODY = 32 * 1024 * 1024;
// ABI reads are synchronous and main-thread-only. Copy each result before another
// probe runs, so pending requests can share scratch space without retaining it.
const READ_BUFFER = Buffer.allocUnsafe(65536);

/** Owns one native request and all cancellation hooks. Internal to the SDK. */
export class Transfer {
  private readonly controller = new AbortController();
  private closed = false;
  private failure?: Error;
  private timer?: ReturnType<typeof setTimeout>;
  private deadline?: number;
  private readonly externalAbort = () => this.close(new AbortError(undefined, { cause: this.external?.reason }));

  constructor(private readonly native: Native, private readonly handle: bigint,
    private readonly release: () => void, private readonly external?: AbortSignal) {}

  attachSignal(total?: number, started = performance.now()): void {
    this.external?.addEventListener("abort", this.externalAbort, { once: true });
    if (this.external?.aborted) this.externalAbort();
    if (total && !this.closed) {
      this.deadline = started + total;
      this.timer = setTimeout(() => this.close(new FastlsTimeoutError("total deadline exceeded", "total")), Math.max(1, this.deadline - performance.now()));
    }
  }

  get signal(): AbortSignal { return this.controller.signal; }
  info(): NativeInfo {
    this.ensureOpen();
    const info = this.native.call<NativeInfo>("info", this.handle);
    if (info.state === "error") throw responseError(info);
    return info;
  }
  write(buffer: Uint8Array, end: boolean, signal: AbortSignal): Promise<number> {
    return scheduler.wait(() => {
      if (this.info().state !== "pending") return 2;
      const code = this.native.write(this.handle, buffer, end);
      return code === 1 ? undefined : code;
    }, signal);
  }

  async headers(): Promise<NativeInfo> {
    return scheduler.wait(() => {
      this.ensureOpen();
      const info = this.info();
      return info.state === "pending" ? undefined : info;
    }, this.controller.signal);
  }

  async next(): Promise<Buffer | null> {
    this.ensureOpen();
    const buffer = READ_BUFFER;
    const result = await scheduler.wait(() => {
      this.ensureOpen();
      const { code, length } = this.native.read(this.handle, buffer);
      if (code === 1) return undefined;
      if (code === 2) return null;
      if (!length) throw new FastlsError("native data result was empty");
      return Buffer.from(buffer.subarray(0, length));
    }, this.controller.signal);
    this.ensureOpen();
    return result;
  }

  ensureOpen(): void {
    if (!this.closed && this.deadline !== undefined && performance.now() >= this.deadline) {
      this.close(new FastlsTimeoutError("total deadline exceeded", "total"));
    }
    if (this.closed) throw this.failure ?? new FastlsError("response is closed");
  }

  close(error?: Error): void {
    if (this.closed) return;
    this.closed = true;
    this.failure = error;
    if (this.timer) clearTimeout(this.timer);
    this.external?.removeEventListener("abort", this.externalAbort);
    this.controller.abort(error ?? new FastlsError("response is closed"));
    try { this.native.call("close", this.handle); }
    finally { this.release(); }
  }
}

export class Response {
  constructor(readonly status: number, readonly headers: Headers,
    readonly match: Readonly<Record<string, unknown>>, readonly content: Buffer,
    readonly wireHeaders: Headers = headers, readonly decoded = false) {}
  get ok(): boolean { return this.status >= 200 && this.status < 300; }
  text(): string { return this.content.toString("utf8"); }
  json<T = unknown>(): T { return JSON.parse(this.text()) as T; }
}

export class StreamResponse {
  readonly status: number;
  readonly headers: Headers;
  readonly match: Readonly<Record<string, unknown>>;
  readonly wireHeaders: Headers;
  readonly decoded: boolean;
  readonly body: AsyncIterable<Buffer>;
  private consumed = false;

  constructor(private readonly transfer: Transfer, info: NativeInfo) {
    this.status = info.status!;
    this.headers = new Headers(info.header_values, info.headers);
    this.wireHeaders = info.wire_header_values ? new Headers(info.wire_header_values) : this.headers;
    this.decoded = info.decoded ?? false;
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
        const grown = Buffer.allocUnsafe(Math.min(MAX_BUFFERED_BODY, Math.max(required, content.length * 2)));
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
