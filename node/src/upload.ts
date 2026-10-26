import { setImmediate as yieldTurn } from "node:timers/promises";
import { Transfer, type UploadLifecycle } from "./response.js";

export type ByteSource = Iterable<Uint8Array> | AsyncIterable<Uint8Array>;
/** A factory can use the supplied signal to interrupt its own pending I/O. */
export type UploadBody = string | Uint8Array | ByteSource | ((signal: AbortSignal) => ByteSource);
/** Internal marker for a known last block; avoid an EOF race with early multipart responses. */
export const terminalUploadChunks = new WeakSet<Uint8Array>();

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

export function startUpload(transfer: Transfer, body: UploadBody): UploadLifecycle & { stop: () => void } {
  let completed = false;
  let failure: Error | undefined;
  let clean!: () => void;
  let failCleanup!: (error: Error) => void;
  const cleanup = new Promise<void>((resolve, reject) => { clean = resolve; failCleanup = reject; });
  // Cleanup is also awaited explicitly by closeAsync; keep synchronous close safe.
  void cleanup.catch(() => {});
  const controller = new AbortController();
  const forwardAbort = () => controller.abort(transfer.signal.reason);
  transfer.signal.addEventListener("abort", forwardAbort, { once: true });
  if (transfer.signal.aborted) forwardAbort();
  const stop = () => controller.abort(new Error("upload finished"));
  const done = (async () => {
    let iterator: Iterator<Uint8Array> | AsyncIterator<Uint8Array> | undefined;
    let nextPending = false;
    let pendingNext: Promise<IteratorResult<Uint8Array>> | undefined;
    let source: ByteSource | undefined;
    try {
      source = typeof body === "function" ? body(controller.signal)
        : typeof body === "string" || body instanceof Uint8Array ? scalarChunks(body) : body;
      iterator = Symbol.asyncIterator in source ? source[Symbol.asyncIterator]() : source[Symbol.iterator]();
      let turns = 0;
      while (!controller.signal.aborted && transfer.canWrite()) {
        nextPending = true;
        pendingNext = Promise.resolve(iterator.next());
        const result = await abortable(pendingNext, controller.signal);
        nextPending = false;
        pendingNext = undefined;
        if (result.done) { completed = await transfer.write(Buffer.alloc(0), true, controller.signal) === 0; return; }
        if (!(result.value instanceof Uint8Array)) throw new TypeError("upload chunks must be Uint8Array or Buffer");
        const terminal = terminalUploadChunks.has(result.value);
        for (let offset = 0; offset < result.value.byteLength; offset += 65536) {
          const end = terminal && offset + 65536 >= result.value.byteLength;
          if (await transfer.write(result.value.subarray(offset, offset + 65536), end, controller.signal) === 2) return;
          if (end) { completed = true; return; }
        }
        if (++turns % 16 === 0) await yieldTurn();
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        failure = error instanceof Error ? error : new Error(String(error));
        transfer.close(failure);
      }
    } finally {
      controller.abort(new Error("upload stopped"));
      transfer.signal.removeEventListener("abort", forwardAbort);
      const finishing = (async () => {
        // Readable.destroy interrupts pending next(); arbitrary promises require cooperation.
        if (source && "destroy" in source && typeof source.destroy === "function") source.destroy();
        // Calling return() queues cleanup behind an outstanding async next().
        // Observe both outcomes so an error thrown by finally is not swallowed
        // when cancellation already won the abortable() race.
        const results = await Promise.allSettled([pendingNext, iterator?.return?.()]);
        for (const result of results) {
          if (result.status !== "rejected") continue;
          const error = result.reason;
          if (controller.signal.aborted && (error === controller.signal.reason || (error instanceof Error && error.name === "AbortError"))) continue;
          throw error;
        }
      })();
      void finishing.then(clean, error => {
        failure ??= error instanceof Error ? error : new Error(String(error));
        transfer.close(failure);
        failCleanup(failure);
      });
      // A pending uncooperative next() must not delay native request cancellation.
      if (!nextPending) {
        try { await abortable(finishing, transfer.signal); } catch { /* failure retained above */ }
      }
    }
  })();
  return { done, cleanup, stop, complete: () => completed, check: () => { if (failure) throw failure; } };
}
