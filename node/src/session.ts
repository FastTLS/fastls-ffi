import { readFileSync } from "node:fs";
import { Cookies } from "./cookies.js";
import { AbortError, FastlsError } from "./errors.js";
import { Native, asHandle, encodeNativeJSON } from "./native.js";
import { Response, StreamResponse, Transfer } from "./response.js";
import { timeoutFields, type Timeout } from "./timeout.js";
import { isStreamingBody, startUpload, type UploadBody } from "./upload.js";

export interface RequestOptions {
  profile?: string;
  headers?: Record<string, string>;
  /** Total deadline including response body consumption, in milliseconds. */
  timeoutMs?: Timeout;
  allowRedirects?: boolean;
  body?: UploadBody;
  decodeContent?: boolean;
  maxDecodedBytes?: number;
  signal?: AbortSignal;
}

export interface SessionOptions {
  profile?: string;
  headers?: Record<string, string>;
  timeoutMs?: Timeout;
  library?: string;
  /** Private CA roots replacing system roots for this session only. */
  caFile?: string;
  cookies?: boolean;
  decodeContent?: boolean;
  maxDecodedBytes?: number;
}

export interface Stats {
  open_requests: number;
  open_sessions: number;
  session_pools: number;
  workers: number;
  buffered_body_bytes: number;
  buffered_upload_bytes: number;
  goroutines: number;
  heap_alloc_bytes: number;
  heap_inuse_bytes: number;
}

function decoding(decode: boolean, maximum: number): void {
  if (typeof decode !== "boolean") throw new TypeError("decodeContent must be a boolean");
  if (!Number.isSafeInteger(maximum) || maximum < 1 || maximum > 2 ** 40) throw new RangeError("maxDecodedBytes must be 1..1099511627776");
}

function mergeHeaders(...sources: (Record<string, string> | undefined)[]): Record<string, string> {
  const merged = Object.create(null) as Record<string, string>;
  for (const source of sources) {
    for (const [key, value] of Object.entries(source ?? {})) {
      if (typeof value !== "string") throw new TypeError("header values must be strings");
      merged[key.toLowerCase()] = value;
    }
  }
  return merged;
}

export class Session {
  readonly cookies: Cookies;
  private readonly native: Native;
  private readonly handle: bigint;
  private readonly profile: string;
  private readonly headers: Record<string, string>;
  private readonly timeouts: Record<string, number>;
  private readonly decodeContent: boolean;
  private readonly maxDecodedBytes: number;
  private readonly caPem?: string;
  private readonly active = new Set<Transfer>();
  private closed = false;

  constructor(options: SessionOptions = {}) {
    this.profile = options.profile ?? "chrome/156/windows";
    this.timeouts = timeoutFields(options.timeoutMs ?? 30000);
    this.decodeContent = options.decodeContent ?? true;
    this.maxDecodedBytes = options.maxDecodedBytes ?? 256 * 1024 * 1024;
    decoding(this.decodeContent, this.maxDecodedBytes);
    this.headers = mergeHeaders(options.headers);
    if (options.cookies !== undefined && typeof options.cookies !== "boolean") throw new TypeError("cookies must be a boolean");
    this.caPem = options.caFile === undefined ? undefined : readFileSync(options.caFile, "utf8");
    if (this.caPem !== undefined && !this.caPem.trim()) throw new TypeError("caFile is empty");
    this.native = Native.load(options.library);
    this.handle = asHandle(this.native.call("session_create"));
    this.cookies = new Cookies(input => {
      this.ensureOpen();
      const raw = encodeNativeJSON(input, 16384);
      return this.native.call("session_cookies", this.handle, raw, raw.length);
    });
    try {
      if (options.cookies === false) {
        const raw = encodeNativeJSON({ action: "configure", enabled: false });
        this.native.call("session_cookies", this.handle, raw, raw.length);
      }
    } catch (error) { this.close(); throw error; }
  }

  private ensureOpen(): void {
    if (this.closed) throw new FastlsError("session is closed");
  }

  async stream(method: string, url: string, options: RequestOptions = {}): Promise<StreamResponse> {
    this.ensureOpen();
    if (options.signal !== undefined && !(options.signal instanceof AbortSignal)) {
      throw new TypeError("signal must be an AbortSignal");
    }
    if (options.signal?.aborted) throw new AbortError(undefined, { cause: options.signal.reason });
    const headers = mergeHeaders(this.headers, options.headers);
    const body = options.body;
    const streaming = isStreamingBody(body);
    const bodyBytes = streaming || body === undefined ? undefined : Buffer.from(body as string | Uint8Array);
    const timeouts = options.timeoutMs === undefined ? this.timeouts : timeoutFields(options.timeoutMs);
    const decodeContent = options.decodeContent ?? this.decodeContent;
    const maxDecodedBytes = options.maxDecodedBytes ?? this.maxDecodedBytes;
    decoding(decodeContent, maxDecodedBytes);
    if (options.allowRedirects !== undefined && typeof options.allowRedirects !== "boolean") {
      throw new TypeError("allowRedirects must be a boolean");
    }
    const started = performance.now();
    const handle = this.native.start({
      session: this.handle, url, method, headers,
      profile: options.profile ?? this.profile,
      ...timeouts, decode_content: decodeContent, max_decoded_bytes: maxDecodedBytes,
      disable_redirect: options.allowRedirects === false,
      ca_pem: this.caPem,
      body_base64: bodyBytes?.toString("base64"),
      stream_body: streaming || undefined,
    });
    const transfer = new Transfer(this.native, handle, () => this.active.delete(transfer), options.signal);
    this.active.add(transfer);
    try {
      transfer.attachSignal(timeouts.timeout_ms, started);
      const upload = streaming ? startUpload(transfer, body!) : undefined;
      let info;
      try { info = await transfer.headers(); }
      finally { if (upload) { upload.stop(); await upload.done; } }
      transfer.ensureOpen();
      return new StreamResponse(transfer, info);
    } catch (error) { transfer.close(); throw error; }
  }

  async request(method: string, url: string, options: RequestOptions = {}): Promise<Response> {
    const stream = await this.stream(method, url, options);
    try {
      return new Response(stream.status, stream.headers, stream.match, await stream.read(), stream.wireHeaders, stream.decoded);
    } finally { stream.close(); }
  }
  get(url: string, options?: RequestOptions): Promise<Response> { return this.request("GET", url, options); }
  post(url: string, options?: RequestOptions): Promise<Response> { return this.request("POST", url, options); }

  listProfiles(): string[] { this.ensureOpen(); return this.native.call("profiles"); }
  stats(): Stats { this.ensureOpen(); return this.native.call("stats"); }
  closeIdleConnections(): void { this.ensureOpen(); this.native.call("session_close_idle", this.handle); }
  close(): void {
    if (this.closed) return;
    this.closed = true;
    try {
      for (const transfer of this.active) transfer.close(new FastlsError("session is closed"));
    } finally { this.native.call("session_close", this.handle); }
  }
}
