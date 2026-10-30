import { readFileSync } from "node:fs";
import { Cookies } from "./cookies.js";
import { Headers } from "./headers.js";
import { AbortError, FastlsError, FastlsTimeoutError } from "./errors.js";
import { Native, asHandle, encodeNativeJSON } from "./native.js";
import { Response, StreamResponse, Transfer } from "./response.js";
import { timeoutFields, type Timeout } from "./timeout.js";
import { abortable, isStreamingBody, startUpload, type UploadBody } from "./upload.js";
import { DuplexExchange } from "./duplex.js";
import type { UploadLifecycle } from "./response.js";
import { SSEStream, validateSSEOptions, type SSEOptions } from "./sse.js";
import { Notifier } from "./notifications.js";
import { prepare, type QueryParams, type JSONValue, type FormFields, type MultipartFiles } from "./request-options.js";
import { WebSocket, wsBudget, type WebSocketOptions } from "./websocket.js";
import { fetchWithSession, type FetchOptions, type FetchResponse } from "./fetch.js";
import { ProfileSpec, type AddressPolicy } from "./profiles.js";
import { RetryPolicy, replayable, retryWait } from "./retry.js";

export interface RequestOptions {
  retry?: RetryPolicy | null;
  bodyFactory?: () => UploadBody;
  profileSpec?: ProfileSpec;
  httpVersion?: "auto" | "h3";
  /** Undefined inherits the session proxy; null or an empty string connects directly. */
  proxy?: string | null;
  /** Undefined inherits proxy trust; null uses system roots. Only HTTPS proxies use this file. */
  proxyCaFile?: string | null;
  profile?: string;
  headers?: Record<string, string>;
  trailers?: Record<string, string | readonly string[]>;
  /** Total deadline including response body consumption, in milliseconds. */
  timeoutMs?: Timeout;
  allowRedirects?: boolean;
  maxRedirects?: number;
  body?: UploadBody;
  params?: QueryParams;
  json?: JSONValue;
  form?: FormFields;
  files?: MultipartFiles;
  decodeContent?: boolean;
  maxDecodedBytes?: number;
  signal?: AbortSignal;
  /** Explicit HTTP/2 simultaneous upload/download. Redirects are disabled. */
  duplex?: boolean;
}

export type DuplexOptions = Omit<RequestOptions, "body" | "duplex" | "json" | "form" | "files">;

export interface SessionOptions {
  retry?: RetryPolicy;
  addressPolicy?: AddressPolicy;
  profileSpec?: ProfileSpec;
  httpVersion?: "auto" | "h3";
  /** HTTP/HTTPS CONNECT, socks5 (local DNS), or socks5h (proxy DNS) URL. */
  proxy?: string | null;
  /** Private roots for proxy TLS, independent of the target's caFile. */
  proxyCaFile?: string | null;
  notifications?: "auto" | "required" | "poll";
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
  notifiers?: number;
  open_requests: number;
  open_sessions: number;
  session_pools: number;
  workers: number;
  buffered_body_bytes: number;
  buffered_upload_bytes: number;
  goroutines: number;
  heap_alloc_bytes: number;
  heap_inuse_bytes: number;
  buffered_ws_write_bytes?: number;
}

function decoding(decode: boolean, maximum: number): void {
  if (typeof decode !== "boolean") throw new TypeError("decodeContent must be a boolean");
  if (!Number.isSafeInteger(maximum) || maximum < 1 || maximum > 2 ** 40) throw new RangeError("maxDecodedBytes must be 1..1099511627776");
}

function proxyValue(value: unknown): string | null | undefined {
  if (value !== undefined && value !== null && typeof value !== "string") throw new TypeError("proxy must be a URL string or null");
  // The native validator owns URL parsing and redacted errors. Do not include
  // credentials in JavaScript URL parser errors or normalize invalid input.
  return value as string | null | undefined;
}

function proxyTrust(path: string | null | undefined): string | undefined {
  if (path === null || path === undefined) return undefined;
  if (typeof path !== "string") throw new TypeError("proxyCaFile must be a path string or null");
  const pem = readFileSync(path, "utf8");
  if (!pem.trim()) throw new TypeError("proxyCaFile is empty");
  return pem;
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
  /** Construct and initialize; failed initialization closes the new session. */
  static async open(options: SessionOptions = {}): Promise<Session> {
    const session = new Session(options);
    try { await session.ready(); return session; }
    catch (error) { await session.closeAsync(); throw error; }
  }
  readonly notificationMode: "async-events" | "poll";
  readonly cookies: Cookies;
  private readonly native: Native;
  private readonly handle: bigint;
  private readonly profile: string;
  private readonly proxy: string | null | undefined;
  private readonly proxyCaPem: string | undefined;
  private readonly headers: Record<string, string>;
  private readonly timeouts: Record<string, number>;
  private readonly decodeContent: boolean;
  private readonly maxDecodedBytes: number;
  private readonly caPem?: string;
  private readonly active = new Set<Transfer>();
  private readonly websockets = new Set<WebSocket>();
  private readonly producers = new Set<UploadLifecycle>();
  private closed = false;
  private readonly closing = new AbortController();
  private readonly retry?: RetryPolicy;
  private notifier?: Notifier;
  private setup?: Promise<void>;

  constructor(options: SessionOptions = {}) {
    if (options.retry !== undefined && !(options.retry instanceof RetryPolicy)) throw new TypeError("retry must be RetryPolicy");
    this.retry = options.retry;
    this.proxy = proxyValue(options.proxy);
    this.proxyCaPem = proxyTrust(options.proxyCaFile);
    const notifications = options.notifications === undefined ? "auto" : options.notifications;
    if (!["auto", "required", "poll"].includes(notifications)) throw new TypeError("notifications must be auto, required or poll");
    if (options.profile !== undefined && typeof options.profile !== "string") throw new TypeError("profile must be a selector string");
    if (options.profileSpec !== undefined && !(options.profileSpec instanceof ProfileSpec)) throw new TypeError("profileSpec must be a ProfileSpec");
    if (options.profile !== undefined && options.profileSpec !== undefined) throw new TypeError("profile and profileSpec are mutually exclusive");
    if (options.httpVersion !== undefined && !["auto", "h3"].includes(options.httpVersion)) throw new TypeError("httpVersion must be auto or h3");
    this.profile = options.profile ?? (options.profileSpec !== undefined || options.httpVersion === "h3" ? "" : "chrome/156/windows");
    this.timeouts = timeoutFields(options.timeoutMs ?? 30000);
    this.decodeContent = options.decodeContent ?? true;
    this.maxDecodedBytes = options.maxDecodedBytes ?? 256 * 1024 * 1024;
    decoding(this.decodeContent, this.maxDecodedBytes);
    this.headers = mergeHeaders(options.headers);
    if (options.cookies !== undefined && typeof options.cookies !== "boolean") throw new TypeError("cookies must be a boolean");
    this.caPem = options.caFile === undefined ? undefined : readFileSync(options.caFile, "utf8");
    if (this.caPem !== undefined && !this.caPem.trim()) throw new TypeError("caFile is empty");
    this.native = Native.load(options.library);
    const supported = this.native.notificationsAvailable && this.native.call<string[]>("features").includes("session_notifications");
    if (notifications === "required" && !supported) throw new FastlsError("notifications require a newer Fastls native library");
    this.notificationMode = notifications !== "poll" && supported ? "async-events" : "poll";
    if (!this.native.call<string[]>("features").includes("transport_context_v1")) throw new FastlsError("SDK-owned sessions require the Fastls 0.1.4 native library or newer");
    const configuration: Record<string, unknown> = { schema_version: 1 };
    const required: string[] = [];
    if (options.addressPolicy !== undefined) {
      if (!options.addressPolicy || typeof options.addressPolicy !== "object" || Array.isArray(options.addressPolicy)) throw new TypeError("addressPolicy must be an object");
      configuration.address_policy = options.addressPolicy;
      required.push("transport_address_policy_v1");
    }
    if (options.profileSpec !== undefined) {
      configuration.profile_spec = options.profileSpec.toJSON();
      required.push(...options.profileSpec.nativeCapabilities());
    }
    if (options.httpVersion === "h3") {
      configuration.http_version = "h3";
      required.push("http3_v1");
    }
    if (required.length) {
      this.native.requireCapabilities(required);
      if (options.profile !== undefined) configuration.profile = options.profile;
      if (this.proxy && (options.addressPolicy !== undefined || options.httpVersion === "h3")) {
        const error = new FastlsError("unsupported transport combination");error.code = "unsupported_capability";throw error;
      }
      const raw = encodeNativeJSON(configuration);
      this.handle = asHandle(this.native.call("transport_create_with_options", raw, raw.length));
    } else this.handle = asHandle(this.native.call("transport_create"));
    this.cookies = new Cookies(() => this.ensureOpen(), options.cookies !== false);
  }

  private ensureOpen(): void {
    if (this.closed) throw new FastlsError("session is closed");
  }

  /** Initialize the shared wakeup channel before any HTTP request is allocated. */
  async ready(signal?: AbortSignal): Promise<void> {
    this.ensureOpen();
    if (signal !== undefined && !(signal instanceof AbortSignal)) throw new TypeError("signal must be an AbortSignal");
    if (signal?.aborted) throw new AbortError(undefined, { cause: signal.reason });
    if (this.notificationMode === "poll") return;
    if (!this.notifier) {
      const notifier = this.notifier = new Notifier(this.native, this.handle);
      const setup = this.setup = notifier.start().catch(async error => {
        await notifier.closeAsync();
        if (this.notifier === notifier) { this.notifier = undefined; this.setup = undefined; }
        throw error;
      });
      void setup.catch(() => {});
    }
    const notifier = this.notifier;
    notifier.setupWaiters++;
    try {
      if (signal) await abortable(this.setup!, signal); else await this.setup;
      this.ensureOpen(); notifier.check();
    } catch (error) {
      if (signal?.aborted) throw new AbortError(undefined, { cause: signal.reason });
      throw error;
    } finally {
      if (--notifier.setupWaiters === 0 && !notifier.isReady && this.notifier === notifier) {
        notifier.close(); this.notifier = undefined; this.setup = undefined;
      }
    }
  }

  private begin(method: string, url: string, options: RequestOptions, manual = false, completeHeaders = false): { transfer: Transfer; body: UploadBody | undefined; streaming: boolean } {
    this.ensureOpen();
    if ("addressPolicy" in options) throw new TypeError("addressPolicy is a Session creation option");
    if (options.signal !== undefined && !(options.signal instanceof AbortSignal)) {
      throw new TypeError("signal must be an AbortSignal");
    }
    if (options.signal?.aborted) throw new AbortError(undefined, { cause: options.signal.reason });
    const headers = mergeHeaders(completeHeaders ? undefined : this.headers, options.headers);
    for (const name of ["proxy-authorization", "proxy-connection"]) {
      if (name in headers) throw new FastlsError("proxy headers must not be sent as target headers; configure proxy URL credentials");
    }
    const proxy = proxyValue(options.proxy === undefined ? this.proxy : options.proxy);
    if (proxy && !this.native.call<string[]>("features").includes("proxy_tunnels")) {
      throw new FastlsError("proxy support requires a newer Fastls native library");
    }
    const httpsProxy = proxy?.toLowerCase().startsWith("https://") ?? false;
    if (httpsProxy && !this.native.call<string[]>("features").includes("https_proxy_tls")) {
      throw new FastlsError("HTTPS proxy support requires a newer Fastls native library");
    }
    const proxyCaPem = !httpsProxy ? undefined : options.proxyCaFile === undefined
      ? this.proxyCaPem : proxyTrust(options.proxyCaFile);
    if (options.duplex !== undefined && typeof options.duplex !== "boolean") throw new TypeError("duplex must be a boolean");
    const duplex = options.duplex ?? false;
    if (duplex && !this.native.call<string[]>("features").includes("duplex_h2")) {
      throw new FastlsError("HTTP/2 duplex requires a newer Fastls native library");
    }
    const prepared = prepare(url, options, headers);
    url = prepared.url;
    if (!("cookie" in headers)) {
      const cookie = this.cookies.header(url);
      if (cookie) headers.cookie = cookie;
    }
    const body = prepared.body === undefined && duplex && !manual ? Buffer.alloc(0) : prepared.body;
    const streaming = isStreamingBody(body) || duplex;
    const bodyBytes = streaming || body === undefined ? undefined : Buffer.from(body as string | Uint8Array);
    const timeouts = options.timeoutMs === undefined ? this.timeouts : timeoutFields(options.timeoutMs);
    const decodeContent = options.decodeContent ?? this.decodeContent;
    const maxDecodedBytes = options.maxDecodedBytes ?? this.maxDecodedBytes;
    decoding(decodeContent, maxDecodedBytes);
    if (options.allowRedirects !== undefined && typeof options.allowRedirects !== "boolean") {
      throw new TypeError("allowRedirects must be a boolean");
    }
    const started = performance.now();
    let trailers: Record<string, string[]> | undefined;
    if (options.trailers !== undefined) {
      if (!this.native.call<string[]>("features").includes("request_trailers")) throw new FastlsError("request trailers require a newer Fastls native library");
      if (!options.trailers || typeof options.trailers !== "object" || Array.isArray(options.trailers)) throw new TypeError("trailers must be a mapping");
      trailers = {};
      for (const [name, value] of Object.entries(options.trailers)) {
        const values = typeof value === "string" ? [value] : value;
        if (!Array.isArray(values) || values.some(item => typeof item !== "string")) throw new TypeError("trailer values must be strings");
        trailers[name] = [...values];
      }
    }
    if (options.profileSpec !== undefined && !(options.profileSpec instanceof ProfileSpec)) throw new TypeError("profileSpec must be a ProfileSpec");
    if (options.profileSpec !== undefined && options.profile !== undefined) throw new TypeError("profile and profileSpec are mutually exclusive");
    const required: string[] = [];
    if (options.profileSpec !== undefined) required.push(...options.profileSpec.nativeCapabilities());
    if (options.httpVersion !== undefined) {
      if (!["auto", "h3"].includes(options.httpVersion)) throw new TypeError("httpVersion must be auto or h3");
      required.push("http3_v1");
    }
    if (required.length) this.native.requireCapabilities(required);
    const handle = this.native.start({
      session: this.handle, url, method, headers, trailers, proxy: proxy || undefined, proxy_ca_pem: proxyCaPem,
      profile: options.profile ?? (options.profileSpec !== undefined || options.httpVersion === "h3" ? "" : this.profile),
      profile_spec: options.profileSpec?.toJSON(), http_version: options.httpVersion,
      ...timeouts, decode_content: decodeContent, max_decoded_bytes: maxDecodedBytes,
      disable_redirect: true,
      ca_pem: this.caPem,
      body_base64: bodyBytes?.toString("base64"),
      stream_body: streaming || undefined,
      duplex: duplex || undefined,
    });
    const transfer = new Transfer(this.native, handle, () => { this.active.delete(transfer); this.notifier?.forget(handle); }, options.signal, duplex, this.notifier,
      info => this.cookies.extract(info.final_url ?? url, new Headers(info.header_values, info.headers).getAll("set-cookie")));
    this.active.add(transfer);
    try {
      this.notifier?.track(handle);
      transfer.attachSignal(timeouts.timeout_ms, started);
      return { transfer, body, streaming };
    } catch (error) { transfer.close(); throw error; }
  }

  private async streamHop(method: string, url: string, options: RequestOptions): Promise<StreamResponse> {
    await this.ready(options.signal);
    const { transfer, body, streaming } = this.begin(method, url, options, false, true);
    try {
      const upload = streaming ? startUpload(transfer, body!) : undefined;
      if (upload) {
        this.producers.add(upload);
        void upload.cleanup.then(() => this.producers.delete(upload), () => this.producers.delete(upload));
      }
      let info;
      try {
        info = await transfer.headers();
        if (!transfer.duplex && upload && info.status! < 300 && !info.upload_discarded && !upload.complete()) {
          throw new FastlsError("server responded before upload finished; duplex streaming is not supported");
        }
      }
      finally { if (upload && (!transfer.duplex || !info || info.status! >= 300)) { upload.stop(); await upload.done; } }
      transfer.ensureOpen();
      const response = new StreamResponse(transfer, info, transfer.duplex ? upload : undefined);
      return response;
    } catch (error) { transfer.close(); throw error; }
  }

  async stream(method: string, url: string, options: RequestOptions = {}): Promise<StreamResponse> {
    const retry = options.retry === undefined ? this.retry : options.retry ?? undefined;
    if (retry !== undefined && !(retry instanceof RetryPolicy)) throw new TypeError("retry must be RetryPolicy or null");
    let factory = options.bodyFactory;
    if (factory !== undefined && typeof factory !== "function") throw new TypeError("bodyFactory must be a function");
    if (factory !== undefined && ["body", "json", "form", "files"].some(key => key in options)) throw new TypeError("bodyFactory cannot be combined with body, json, form or files");
    if (options.duplex && (retry !== undefined || factory !== undefined)) throw new TypeError("duplex does not support retry or bodyFactory");
    if (options.signal !== undefined && !(options.signal instanceof AbortSignal)) throw new TypeError("signal must be an AbortSignal");
    const signal = options.signal === undefined ? this.closing.signal : AbortSignal.any([options.signal, this.closing.signal]);
    const headers = mergeHeaders(this.headers, options.headers);
    const prepared = prepare(url, options, headers);
    url = prepared.url;
    // Session close has its own established transfer error. Its signal only
    // interrupts retry backoff; forwarding it as a caller abort changes that error.
    options = { ...options, headers, body: prepared.body };
    for (const key of ["params", "json", "form", "files"] as const) delete options[key];
    const maximum = options.maxRedirects ?? 30;
    if (!Number.isSafeInteger(maximum) || maximum < 0) throw new RangeError("maxRedirects must be a non-negative integer");
    const policy = options.timeoutMs === undefined ? this.timeouts : timeoutFields(options.timeoutMs);
    const deadline = performance.now() + policy.timeout_ms;
    let used = 0, redirects = 0;
    for (;;) {
      this.ensureOpen();
      if (signal.aborted) throw new AbortError(undefined, { cause: signal.reason });
      if (factory !== undefined) options.body = factory();
      const remaining = Math.ceil(deadline - performance.now());
      if (remaining <= 0) throw new FastlsTimeoutError("total deadline exceeded", "total");
      options.timeoutMs = { total: remaining, connect: policy.connect_timeout_ms, write: policy.write_timeout_ms, headers: policy.headers_timeout_ms, read: policy.read_timeout_ms };
      let response: StreamResponse;
      try { response = await this.streamHop(method, url, options); }
      catch (error) {
        if (!retry?.eligible(method, used, replayable(options.body, factory), error)) throw error;
        await retryWait(retry.delay(used), deadline, signal); used++; continue;
      }
      if (retry?.eligible(method, used, replayable(options.body, factory), undefined, response.status)) {
        const delay = retry.delay(used, response.headers.get("retry-after"));
        await response.closeAsync();
        await retryWait(delay, deadline, signal); used++; continue;
      }
      const location = response.headers.get("location");
      if (options.duplex || options.allowRedirects === false || ![301,302,303,307,308].includes(response.status) || !location || ([307,308].includes(response.status) && !replayable(options.body, factory))) return response;
      try {
        if (redirects >= maximum) throw new FastlsError("maximum redirects exceeded");
        const previous = new URL(url), target = new URL(location, url);
        if (!["http:", "https:"].includes(target.protocol) || target.username || target.password) throw new FastlsError("invalid redirect URL");
        target.hash = "";
        if (previous.origin !== target.origin) for (const name of ["authorization", "cookie", "host"]) delete headers[name];
        if (([302,303].includes(response.status) && method.toUpperCase() !== "HEAD") || (response.status === 301 && method.toUpperCase() === "POST")) {
          method = "GET"; delete options.body; delete options.trailers; factory = undefined;
          for (const name of ["content-type", "content-length", "transfer-encoding"]) delete headers[name];
        }
        url = target.href;
        redirects++;
        await response.read();
      } finally { await response.closeAsync(); }
    }
  }

  async sse(method: string, url: string, options: SSEOptions = {}): Promise<SSEStream> {
    const { maxEventBytes = 1024 * 1024, lastEventId, ...request } = options;
    validateSSEOptions(maxEventBytes, lastEventId);
    const headers = mergeHeaders({ accept: "text/event-stream", "cache-control": "no-cache" }, request.headers);
    if (lastEventId !== undefined) headers["last-event-id"] = lastEventId;
    const response = await this.stream(method, url, { ...request, headers });
    try { return new SSEStream(response, maxEventBytes, lastEventId); }
    catch (error) { await response.closeAsync(); throw error; }
  }

  async request(method: string, url: string, options: RequestOptions = {}): Promise<Response> {
    const stream = await this.stream(method, url, options);
    try {
      const content = await stream.read();
      return new Response(stream.status, stream.headers, stream.match, content, stream.wireHeaders, stream.decoded, stream.httpVersion, stream.url, stream.trailers);
    } finally { await stream.closeAsync(); }
  }

  /** Open a manual exchange; send() and response reads can run concurrently. */
  duplex(method: string, url: string, options: DuplexOptions = {}): DuplexExchange {
    if (options.retry !== undefined || options.bodyFactory !== undefined) throw new TypeError("manual duplex does not support retry or bodyFactory");
    if (["body", "duplex", "json", "form", "files"].some(key => key in options)) throw new TypeError("manual duplex uses send() and finishUpload(), not body, json, form, files or duplex options");
    this.ensureOpen();
    const cancellation = new AbortController();
    const signal = options.signal === undefined ? cancellation.signal : AbortSignal.any([options.signal, cancellation.signal]);
    const transfer = this.ready(signal).then(() => {
      if (cancellation.signal.aborted) throw new FastlsError("exchange is closed");
      return this.begin(method, url, { ...options, signal, duplex: true }, true).transfer;
    }, error => { if (cancellation.signal.aborted) throw new FastlsError("exchange is closed"); throw error; });
    return new DuplexExchange(transfer, () => cancellation.abort());
  }
  get(url: string, options?: RequestOptions): Promise<Response> { return this.request("GET", url, options); }
  post(url: string, options?: RequestOptions): Promise<Response> { return this.request("POST", url, options); }
  put(url: string, options?: RequestOptions): Promise<Response> { return this.request("PUT", url, options); }
  patch(url: string, options?: RequestOptions): Promise<Response> { return this.request("PATCH", url, options); }
  delete(url: string, options?: RequestOptions): Promise<Response> { return this.request("DELETE", url, options); }
  head(url: string, options: RequestOptions = {}): Promise<Response> { return this.request("HEAD", url, { allowRedirects: false, ...options }); }
  options(url: string, options?: RequestOptions): Promise<Response> { return this.request("OPTIONS", url, options); }
  fetch(input: string | URL | Request, options: FetchOptions = {}): Promise<FetchResponse> {
    if (options.library !== undefined || options.caFile !== undefined) throw new TypeError("library and caFile must be configured on Session");
    return fetchWithSession(this, input, options);
  }

  async websocket(url:string, options:WebSocketOptions={}):Promise<WebSocket> {
    await this.ready(options.signal);this.ensureOpen();
    if(options.signal?.aborted)throw new AbortError(undefined,{cause:options.signal.reason});
    if(!this.native.websocketAvailable)throw new FastlsError("WebSocket requires a newer Fastls native library");
    const cookieMode=options.cookieMode===undefined?"session":options.cookieMode;
    if(cookieMode!=="session" && cookieMode!=="manual")throw new TypeError("cookieMode must be session or manual");
    const features=this.native.call<string[]>("features");
    const sessionCookies=features.includes("websocket_cookie_control_v1")||features.includes("websocket_cookies");
    if(cookieMode==="manual" && !sessionCookies && features.includes("websocket_cookies_v1"))throw new FastlsError("This native library cannot disable automatic WebSocket cookies; a newer library with websocket_cookie_control_v1 is required");
    if(cookieMode==="session" && !sessionCookies)throw new FastlsError("WebSocket session cookies require a newer Fastls native library; use cookieMode manual for legacy behavior");
    const maximum=(options.maxMessageBytes===undefined?1048576:options.maxMessageBytes);
    if(!Number.isSafeInteger(maximum)||maximum<1||maximum>16777216)throw new RangeError("maxMessageBytes must be 1..16777216");
    const close=wsBudget((options.closeTimeoutMs===undefined?1000:options.closeTimeoutMs),"closeTimeoutMs",60000);
    const headers=mergeHeaders(this.headers,options.headers),prepared=prepare(url,{params:options.params},headers);
    if(cookieMode==="session" && !("cookie" in headers)){
      const cookie=this.cookies.header(prepared.url);if(cookie)headers.cookie=cookie;
    }
    const proxy=proxyValue(options.proxy===undefined?this.proxy:options.proxy);
    const proxyCA=proxy?.toLowerCase().startsWith("https://")?(options.proxyCaFile===undefined?this.proxyCaPem:proxyTrust(options.proxyCaFile)):undefined;
    if(options.subprotocols!==undefined && (!Array.isArray(options.subprotocols)||options.subprotocols.some(p=>typeof p!=="string")))throw new TypeError("subprotocols must be an array of strings");
    const handle=this.native.wsStart({url:prepared.url,session:this.handle,disable_cookies:true,headers,profile:options.profile??this.profile,proxy:proxy||undefined,ca_pem:this.caPem,proxy_ca_pem:proxyCA,subprotocols:options.subprotocols??[],max_message_bytes:maximum,
      timeout_ms:wsBudget((options.handshakeTimeoutMs===undefined?30000:options.handshakeTimeoutMs),"handshakeTimeoutMs"),read_timeout_ms:options.readTimeoutMs===null||options.readTimeoutMs===undefined?0:wsBudget(options.readTimeoutMs,"readTimeoutMs"),write_timeout_ms:wsBudget((options.writeTimeoutMs===undefined?30000:options.writeTimeoutMs),"writeTimeoutMs")});
    const ws=new WebSocket(this.native,handle,()=>{this.websockets.delete(ws);this.notifier?.forget(handle);},maximum,close,this.notifier,options.signal);
    this.websockets.add(ws);
    try{this.notifier?.track(handle);return await ws.opened();}catch(error){ws.abort();throw error;}
    finally{if(cookieMode==="session" && !this.closed){const info=ws.info;if(info.status!==101 || info.state==="headers" || info.state==="eof")this.cookies.extract(prepared.url,ws.headers.getAll("set-cookie"));}}
  }

  listProfiles(): string[] { this.ensureOpen(); return this.native.call("profiles"); }
  stats(): Stats { this.ensureOpen(); return this.native.call("stats"); }
  closeIdleConnections(): void { this.ensureOpen(); this.native.call("session_close_idle", this.handle); }
  close(): void {
    if (this.closed) return;
    this.cookies.clear();
    this.closed = true;
    this.closing.abort(new AbortError("session is closed"));
    try {
      for (const transfer of this.active) transfer.close(new FastlsError("session is closed"));
      for (const ws of this.websockets) ws.abort(new FastlsError("session is closed"));
    } finally { try { this.notifier?.close(); } finally { this.native.call("session_close", this.handle); } }
  }
  /** Release native resources immediately, then wait for cooperative upload cleanup. */
  async closeAsync(): Promise<void> {
    const producers = [...this.producers];
    this.close();
    await this.notifier?.closeAsync();
    const results = await Promise.allSettled(producers.map(task => task.cleanup));
    for (const result of results) if (result.status === "rejected") throw result.reason;
    for (const task of producers) task.check();
  }
}
