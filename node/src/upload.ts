import { setImmediate as yieldTurn } from "node:timers/promises";
import { Transfer } from "./response.js";

export type ByteSource = Iterable<Uint8Array> | AsyncIterable<Uint8Array>;
/** A factory can use the supplied signal to interrupt its own pending I/O. */
export type UploadBody = string | Uint8Array | ByteSource | ((signal: AbortSignal) => ByteSource);

export function abortable<T>(promise: PromiseLike<T> | T, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const abort = () => { signal.removeEventListener("abort", abort); reject(signal.reason); };
    Promise.resolve(promise).then(value => {
      signal.removeEventListener("abort", abort); resolve(value);
    }, error => { signal.removeEventListener("abort", abort); reject(error); });
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
  });
}

function* scalarChunks(value: string | Uint8Array): Generator<Uint8Array> {
  if (typeof value === "string") {
    for (let offset = 0; offset < value.length;) {
      let end = Math.min(value.length, offset + 16384);
      if (end < value.length && value.charCodeAt(end - 1) >= 0xd800 && value.charCodeAt(end - 1) <= 0xdbff) end--;
      yield Buffer.from(value.slice(offset, end), "utf8");
      offset = end;
    }
  } else {
    for (let offset = 0; offset < value.byteLength; offset += 65536) yield value.subarray(offset, offset + 65536);
  }
}

export function isStreamingBody(value: UploadBody | undefined): boolean {
  if (value === undefined) return false;
  if (typeof value === "string") return value.length > 512 * 1024 || Buffer.byteLength(value) > 512 * 1024;
  if (value instanceof Uint8Array) return value.byteLength > 512 * 1024;
  if (typeof value === "function" || (value && (Symbol.iterator in value || Symbol.asyncIterator in value))) return true;
  throw new TypeError("body must be string, Uint8Array, an iterable of bytes or an upload factory");
}

export function startUpload(transfer: Transfer, body: UploadBody): { done: Promise<void>; stop: () => void } {
  const controller = new AbortController();
  const forwardAbort = () => controller.abort(transfer.signal.reason);
  transfer.signal.addEventListener("abort", forwardAbort, { once: true });
  if (transfer.signal.aborted) forwardAbort();
  const stop = () => controller.abort(new Error("upload finished"));
  const done = (async () => {
    let iterator: Iterator<Uint8Array> | AsyncIterator<Uint8Array> | undefined;
    let nextPending = false;
    let source: ByteSource | undefined;
    try {
      source = typeof body === "function" ? body(controller.signal)
        : typeof body === "string" || body instanceof Uint8Array ? scalarChunks(body) : body;
      iterator = Symbol.asyncIterator in source ? source[Symbol.asyncIterator]() : source[Symbol.iterator]();
      let turns = 0;
      while (!controller.signal.aborted && transfer.info().state === "pending") {
        nextPending = true;
        const result = await abortable(iterator.next(), controller.signal);
        nextPending = false;
        if (result.done) { await transfer.write(Buffer.alloc(0), true, controller.signal); return; }
        if (!(result.value instanceof Uint8Array)) throw new TypeError("upload chunks must be Uint8Array or Buffer");
        for (let offset = 0; offset < result.value.byteLength; offset += 65536) {
          if (await transfer.write(result.value.subarray(offset, offset + 65536), false, controller.signal) === 2) return;
        }
        if (++turns % 16 === 0) await yieldTurn();
      }
    } catch (error) {
      if (!controller.signal.aborted) transfer.close(error instanceof Error ? error : new Error(String(error)));
    } finally {
      controller.abort(new Error("upload stopped"));
      transfer.signal.removeEventListener("abort", forwardAbort);
      // Readable.destroy interrupts outstanding next(). Arbitrary JS promises cannot be forcibly cancelled.
      if (source && "destroy" in source && typeof source.destroy === "function") source.destroy();
      if (iterator?.return) {
        try {
          const cleanup = Promise.resolve(iterator.return());
          if (nextPending) void cleanup.catch(() => {});
          else await abortable(cleanup, transfer.signal);
        } catch (error) {
          if (!transfer.signal.aborted) transfer.close(error instanceof Error ? error : new Error(String(error)));
        }
      }
    }
  })();
  return { done, stop };
}
