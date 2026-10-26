import { Readable } from "node:stream";
import { Session, type SessionOptions, type RequestOptions } from "./session.js";

export interface FetchOptions extends RequestInit {
  duplex?: "half";
  profile?: string;
  proxy?: string | null;
  proxyCaFile?: string | null;
  caFile?: string;
  library?: string;
  timeoutMs?: RequestOptions["timeoutMs"];
  maxRedirects?: number;
}

/** A standard Node Response with Fastls transport metadata. */
export interface FetchResponse extends globalThis.Response {
  readonly match: Readonly<Record<string, unknown>>;
  readonly httpVersion: string;
}

/** @internal Used by Session.fetch; ownership is explicit to avoid closing shared pools. */
export async function fetchWithSession(session: Session, input: string | URL | Request,
  init: FetchOptions = {}, owned = false): Promise<FetchResponse> {
  try {
    for (const key of ["cache", "credentials", "integrity", "keepalive", "mode", "referrer", "referrerPolicy"] as const) {
      if (init[key] !== undefined) throw new TypeError(`Fastls fetch does not support ${key}`);
    }
    const allowed = new Set(["method", "headers", "body", "redirect", "signal", "duplex", "profile", "proxy", "proxyCaFile", "caFile", "library", "timeoutMs", "maxRedirects"]);
    for (const key of Object.keys(init)) if (!allowed.has(key)) throw new TypeError(`Fastls fetch does not support ${key}`);
    // Request performs the platform's normal URL/method/header validation and
    // serializes URLSearchParams, Blob and FormData without a second encoder.
    const request = new Request(input, init);
    const headers = Object.fromEntries(request.headers);
    let body: RequestOptions["body"];
    if (request.body) {
      if (typeof init.body === "string" || init.body instanceof Uint8Array) body = init.body;
      else if (init.body instanceof URLSearchParams) body = init.body.toString();
      else body = Readable.fromWeb(request.body as import("node:stream/web").ReadableStream<Uint8Array>);
    }
    const stream = await session.stream(request.method, request.url, {
      headers, body, profile: init.profile, proxy: init.proxy, proxyCaFile: init.proxyCaFile,
      timeoutMs: init.timeoutMs, maxRedirects: init.maxRedirects,
      allowRedirects: request.redirect === "follow", signal: request.signal,
    });
    let closing: Promise<void> | undefined;
    const close = (): Promise<void> => closing ??= (async () => {
      stream.closedSignal.removeEventListener("abort", abort);
      try { await stream.closeAsync(); } finally { if (owned) await session.closeAsync(); }
    })();
    const abort = () => { void close().catch(() => {}); };
    stream.closedSignal.addEventListener("abort", abort, { once: true });
    if (stream.closedSignal.aborted) { await close(); throw stream.closedSignal.reason; }
    try {
      if (request.redirect === "error" && [301,302,303,307,308].includes(stream.status)) throw new TypeError("redirect encountered with redirect: error");
      const responseHeaders = new globalThis.Headers();
      for (const [name, value] of stream.headers.multiItems()) responseHeaders.append(name, value);
      const empty = request.method === "HEAD" || [204,205,304].includes(stream.status);
      const iterator = stream.body[Symbol.asyncIterator]();
      const responseBody = empty ? null : new ReadableStream<Uint8Array>({
        async pull(controller) {
          try {
            const item = await iterator.next();
            if (item.done) { await close(); controller.close(); }
            else controller.enqueue(item.value);
          } catch (error) { try { await close(); } finally { controller.error(error); } }
        },
        async cancel() { try { await iterator.return?.(); } finally { await close(); } },
      }, { highWaterMark: 0 });
      const response = new globalThis.Response(responseBody, { status: stream.status, headers: responseHeaders }) as FetchResponse;
      Object.defineProperties(response, {
        url: { value: stream.url }, redirected: { value: stream.url !== request.url },
        match: { value: stream.match }, httpVersion: { value: stream.httpVersion },
      });
      if (empty) await close();
      return response;
    } catch (error) { await close(); throw error; }
  } catch (error) { if (owned) await session.closeAsync(); throw error; }
}

/** Fetch-compatible entry point. No persistent cookie jar is created. */
export async function fetch(input: string | URL | Request, init: FetchOptions = {}): Promise<FetchResponse> {
  const options: SessionOptions = { cookies: false, profile: init.profile, proxy: init.proxy,
    proxyCaFile: init.proxyCaFile, caFile: init.caFile, library: init.library, timeoutMs: init.timeoutMs };
  return fetchWithSession(new Session(options), input, init, true);
}
