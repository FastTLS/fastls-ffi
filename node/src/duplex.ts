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

  constructor(private readonly transfer: Transfer) {
    const done = new Promise<void>(resolve => { this.finish = resolve; });
    const stopped = () => this.finish();
    transfer.signal.addEventListener("abort", stopped, { once: true });
    if (transfer.signal.aborted) stopped();
    void done.then(() => transfer.signal.removeEventListener("abort", stopped));
    this.upload = { done, cleanup: Promise.resolve(), complete: () => this.finished,
      check: () => transfer.throwFailure() };
  }

  get response(): Promise<StreamResponse> {
    return this.responsePromise ??= this.transfer.headers().then(
      info => new StreamResponse(this.transfer, info, this.upload),
      error => { this.close(); throw error; },
    );
  }

  private async write(bytes: Uint8Array, end: boolean): Promise<void> {
    if (this.writing) throw new FastlsError("another upload write is pending; await send() before sending again");
    this.transfer.ensureOpen();
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
      if (end) {
        accepted(await this.transfer.write(Buffer.alloc(0), true, this.transfer.signal));
        this.finished = true;
        this.finish();
      } else {
        for (let offset = 0; offset < bytes.byteLength; offset += 65536) {
          accepted(await this.transfer.write(bytes.subarray(offset, offset + 65536), false, this.transfer.signal));
        }
      }
    } catch (error) {
      if (!this.uploadStopped) this.transfer.close(error instanceof Error ? error : new Error(String(error)));
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
  close(): void { this.transfer.close(); }
  async closeAsync(): Promise<void> { this.close(); }
}
