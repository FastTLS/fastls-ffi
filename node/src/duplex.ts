import { FastlsError } from "./errors.js";
import { StreamResponse, Transfer, type UploadLifecycle } from "./response.js";

/** A manual HTTP/2 exchange. At most one send/finish operation may be pending. */
export class DuplexExchange {
  private responsePromise?: Promise<StreamResponse>;
  private writing = false;
  private finished = false;
  private uploadStopped = false;
  private finish!: () => void;
  private readonly upload: UploadLifecycle;
  private readonly transferPromise: Promise<Transfer>;
  private transfer?: Transfer;
  private closed = false;

  constructor(transfer: Transfer | Promise<Transfer>, private readonly cancelPending?: () => void) {
    const done = new Promise<void>(resolve => { this.finish = resolve; });
    this.transferPromise = Promise.resolve(transfer).then(value => {
      this.transfer = value;
      if (this.closed) value.close();
      const stopped = () => this.finish();
      value.signal.addEventListener("abort", stopped, { once: true });
      if (value.signal.aborted) stopped();
      void done.then(() => value.signal.removeEventListener("abort", stopped));
      return value;
    });
    void this.transferPromise.catch(() => this.finish());
    this.upload = { done, cleanup: Promise.resolve(), complete: () => this.finished,
      check: () => this.transfer?.throwFailure() };
  }

  get response(): Promise<StreamResponse> {
    return this.responsePromise ??= this.transferPromise.then(async transfer => {
      if (this.closed) throw new FastlsError("exchange is closed");
      const info = await transfer.headers(); return new StreamResponse(transfer, info, this.upload);
    }).then(
      response => response,
      error => { this.close(); throw error; },
    );
  }

  private async write(bytes: Uint8Array, end: boolean): Promise<void> {
    if (this.writing) throw new FastlsError("another upload write is pending; await send() before sending again");
    if (this.finished || this.uploadStopped) throw new FastlsError("upload is closed");
    this.writing = true;
    const accepted = (code: number) => {
      if (code === 0) return;
      // The peer can stop receiving while its response remains readable.
      this.uploadStopped = true;
      this.finish();
      throw new FastlsError("server closed the upload");
    };
    try {
      const transfer = await this.transferPromise;
      if (this.closed) throw new FastlsError("exchange is closed");
      transfer.ensureOpen();
      if (end) {
        accepted(await transfer.write(Buffer.alloc(0), true, transfer.signal));
        this.finished = true;
        this.finish();
      } else {
        for (let offset = 0; offset < bytes.byteLength; offset += 65536) {
          accepted(await transfer.write(bytes.subarray(offset, offset + 65536), false, transfer.signal));
        }
      }
    } catch (error) {
      if (!this.uploadStopped) this.transfer?.close(error instanceof Error ? error : new Error(String(error)));
      throw error;
    } finally { this.writing = false; }
  }

  send(bytes: Uint8Array): Promise<void> {
    if (!(bytes instanceof Uint8Array)) return Promise.reject(new TypeError("send() requires Uint8Array or Buffer"));
    return this.write(bytes, false);
  }
  finishUpload(): Promise<void> {
    return this.finished ? Promise.resolve() : this.write(Buffer.alloc(0), true);
  }
  close(): void { this.closed = true; this.transfer?.close(); this.cancelPending?.(); }
  async closeAsync(): Promise<void> { this.close(); await this.transferPromise.then(transfer => transfer.close(), () => {}); }
}
