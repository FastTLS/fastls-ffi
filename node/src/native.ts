import koffi, { type LibraryHandle } from "koffi";
import { parse, stringify } from "lossless-json";
import { libraryPath } from "./library-path.js";
import { isMainThread } from "node:worker_threads";
import { FastlsError, responseError } from "./errors.js";

export function parseNativeJSON(text: string): unknown {
  return parse(text, undefined, (token) => {
    const number = Number(token);
    return /^-?\d+$/.test(token) && !Number.isSafeInteger(number) ? BigInt(token) : number;
  });
}

export function encodeNativeJSON(value: unknown, limit = 1024 * 1024): Buffer {
  const raw = Buffer.from(stringify(value)!, "utf8");
  if (!raw.length || raw.length > limit) throw new RangeError(`native JSON exceeds ${limit} bytes`);
  return raw;
}

export function asHandle(value: unknown): bigint {
  if (typeof value !== "bigint" && !(typeof value === "number" && Number.isSafeInteger(value))) {
    throw new FastlsError("native library returned an invalid handle");
  }
  const handle = BigInt(value);
  if (handle < 1n || handle > 0xffffffffffffffffn) throw new FastlsError("invalid uint64 handle");
  return handle;
}

export interface NativeInfo {
  state: "pending" | "headers" | "eof" | "error";
  status?: number;
  headers?: Record<string, string>;
  header_values?: Record<string, string[]>;
  match: Readonly<Record<string, unknown>>;
  error?: string;
  error_code?: string;
  timeout_phase?: string;
  decoded?: boolean;
  protocol?: string;
  final_url?: string;
  trailer_values?: Record<string, string[]>;
  wire_header_values?: Record<string, string[]>;
  upload_discarded?: boolean;
  websocket?: { subprotocol: string; write_pending: boolean; close_code: number; close_reason: string; close_received: boolean; pings: number; pongs: number; last_pong_base64: string; max_message_bytes: number; alpn_policy: string };
}

// Keep successful and partially loaded libraries alive until process exit.
// Worker teardown may unload a Go runtime, so worker_threads are not yet supported.
const libraries = new Map<string, LibraryHandle>();
const bindings = new Map<string, Native>();
const pinned = new Set<string>();
let systemLoader: LibraryHandle | undefined;
type NativeFunction = ReturnType<LibraryHandle["func"]>;

function pinLibrary(path: string): void {
  if (pinned.has(path)) return;
  // N-API finalizers can unload libraries during environment teardown even when
  // JavaScript retained them. Go runtime threads require OS-level process lifetime.
  systemLoader ??= koffi.load(process.platform === "win32" ? "kernel32.dll" : null);
  if (process.platform === "win32") {
    const module = [null];
    const pin = systemLoader.func("int __stdcall GetModuleHandleExW(uint32_t flags, str16 name, _Out_ void **module)");
    if (!pin(1, path, module)) throw new FastlsError("cannot pin the Go runtime library for process lifetime");
  } else {
    const dlopen = systemLoader.func("void *dlopen(str path, int flags)");
    // RTLD_NOW | RTLD_NODELETE; values from Darwin / glibc dlfcn.h.
    if (!dlopen(path, 2 | (process.platform === "darwin" ? 0x80 : 0x1000))) {
      throw new FastlsError("cannot pin the Go runtime library for process lifetime");
    }
  }
  pinned.add(path);
}

export class Native {
  private functions: Record<string, NativeFunction> = {};
  readonly transportOptionsAvailable: boolean = false;
  readonly notificationsAvailable: boolean;
  readonly websocketAvailable: boolean;
  readonly path: string;

  static load(library?: string): Native {
    if (!isMainThread) throw new FastlsError("Fastls must be loaded on the main thread; worker_threads are not supported yet");
    const path = libraryPath(library);
    let native = bindings.get(path);
    if (!native) {
      native = new Native(path);
      bindings.set(path, native);
    }
    return native;
  }

  private constructor(path: string) {
    this.path = path;
    try {
      let lib = libraries.get(path);
      if (!lib) {
        lib = koffi.load(path);
        libraries.set(path, lib);
      }
      pinLibrary(path);
      if (lib.func("uint32_t fastls_abi_version(void)")() !== 1) {
        throw new FastlsError("unsupported Fastls ABI version (expected 1)");
      }
      const signatures: Record<string, string[]> = {
        profiles: [], stats: [], features: [], session_create: [],
        session_close: ["uint64_t"], session_close_idle: ["uint64_t"],
        session_cookies: ["uint64_t", "void *", "size_t"],
        start: ["void *", "size_t"], info: ["uint64_t"],
        cancel: ["uint64_t"], close: ["uint64_t"],
      };
      for (const [name, args] of Object.entries(signatures)) {
        // Return an owned pointer, never an auto-converted string: we must free it.
        this.functions[name] = lib.func(`fastls_${name}`, "void *", args);
      }
      this.functions.free = lib.func("void fastls_free(void *ptr)");
      this.functions.read = lib.func("int fastls_read(uint64_t handle, _Out_ void *buffer, size_t capacity, _Out_ size_t *length)");
      this.functions.write = lib.func("int fastls_write(uint64_t handle, const void *buffer, size_t length, int end)");
      const features = new Set(this.call<string[]>("features"));
      if (features.has("transport_options_v1")) {
        try {
          this.functions.transport_create_with_options = lib.func("void *fastls_transport_create_with_options(void *json, size_t length)");
          this.transportOptionsAvailable = true;
        } catch { /* Optional symbol: old default transport stays loadable. */ }
      }
      if (features.has("transport_context_v1")) {
        this.functions.transport_create = lib.func("void *fastls_transport_create(void)");
      }
      let websocketAvailable = false;
      if (features.has("websocket_v1")) {
        try {
          const start = lib.func("void *fastls_ws_start(void *json, size_t length)");
          const send = lib.func("int fastls_ws_send(uint64_t handle, int opcode, const void *data, size_t length)");
          const read = lib.func("int fastls_ws_read(uint64_t handle, _Out_ void *buffer, size_t capacity, _Out_ size_t *length, _Out_ int *opcode, _Out_ int *end)");
          Object.assign(this.functions, { ws_start: start, ws_send: send, ws_read: read });
          websocketAvailable = true;
        } catch { /* Optional as a group; HTTP stays available with old libraries. */ }
      }
      this.websocketAvailable = websocketAvailable;
      let notificationsAvailable = false;
      if (features.has("session_notifications")) {
        try {
          for (const [name, args] of Object.entries({ session_watch: ["uint64_t", "uint16_t", "void *", "size_t"], session_events: ["uint64_t"], session_unwatch: ["uint64_t"] })) {
            this.functions[name] = lib.func(`fastls_${name}`, "void *", args);
          }
          this.functions.wait = lib.func("int fastls_wait(uint64_t handle, uint32_t events, uint32_t timeout_ms)");
          notificationsAvailable = true;
        } catch { /* Old ABI-v1 libraries can still use polling. */ }
      }
      this.notificationsAvailable = notificationsAvailable;
      for (const feature of ["stream_decode", "phase_timeouts", "stream_upload", "write_timeout"]) {
        if (!features.has(feature)) throw new FastlsError(`native feature required: ${feature}`);
      }
    } catch (cause) {
      throw new FastlsError(`Cannot bind Fastls library at ${path}; ABI v1 with decoding, phase timeouts and streaming uploads is required`, { cause });
    }
  }

  call<T = unknown>(name: string, ...args: unknown[]): T {
    const pointer = this.functions[name](...args);
    if (!pointer) throw new FastlsError("native library returned a null result");
    let envelope: { ok: boolean; result: T; error?: string; error_code?: string };
    try {
      const json = koffi.decode.string(pointer);
      // Starts, sessions and notification batches contain uint64 handles. Catalogs, headers
      // and info metadata use strings and bounded numeric fields; avoid the
      // allocating JS numeric parser on every poll and catalog lookup.
      envelope = (name === "start" || name === "ws_start" || name === "session_create" || name === "transport_create" || name === "transport_create_with_options" || name === "session_events" ? parseNativeJSON(json) : JSON.parse(json)) as typeof envelope;
    } finally {
      this.functions.free(pointer);
    }
    if (!envelope?.ok) {
      const error = new FastlsError(envelope?.error ?? "invalid native result");
      error.code = envelope?.error_code;
      throw error;
    }
    return envelope.result;
  }

  start(input: unknown): bigint {
    const raw = encodeNativeJSON(input);
    return asHandle(this.call("start", raw, raw.length));
  }

  requireCapabilities(required: readonly string[]): void {
    const features = new Set(this.call<string[]>("features"));
    if (!this.transportOptionsAvailable || required.some(name => !features.has(name))) {
      const error = new FastlsError("native transport capability is unavailable");
      error.code = "unsupported_capability";
      throw error;
    }
  }

  wsStart(input: unknown): bigint {
    if (!this.websocketAvailable) throw new FastlsError("WebSocket requires a newer Fastls native library");
    const raw = encodeNativeJSON(input);
    return asHandle(this.call("ws_start", raw, raw.length));
  }
  wsSend(handle: bigint, opcode: number, data: Uint8Array): number {
    return this.functions.ws_send(handle, opcode, data, data.byteLength);
  }
  wsRead(handle: bigint, buffer: Buffer): { code: number; length: number; opcode: number; end: boolean } {
    const length = [0], opcode = [0], end = [0];
    const code = this.functions.ws_read(handle, buffer, buffer.length, length, opcode, end);
    if (![0,1,2].includes(code) && code >= 0) throw new FastlsError("invalid WebSocket read status");
    if (!Number.isSafeInteger(Number(length[0])) || Number(length[0]) < 0 || Number(length[0]) > buffer.length || ![0,1].includes(end[0])) throw new FastlsError("invalid WebSocket read result");
    return { code, length: Number(length[0]), opcode: opcode[0], end: end[0] === 1 };
  }

  read(handle: bigint, buffer: Buffer): { code: number; length: number } {
    const out: [number | bigint] = [0];
    const code = this.functions.read(handle, buffer, buffer.length, out);
    const length = Number(out[0]);
    if (!Number.isSafeInteger(length) || length < 0 || length > buffer.length) {
      throw new FastlsError("native library returned an invalid read length");
    }
    if (code < 0) {
      const info = this.call<NativeInfo>("info", handle);
      throw responseError(info);
    }
    if (![0, 1, 2].includes(code)) throw new FastlsError("native library returned an invalid read status");
    return { code, length };
  }

  write(handle: bigint, buffer: Uint8Array, end = false): number {
    const code = this.functions.write(handle, buffer, buffer.byteLength, Number(end));
    if (code < 0) {
      const info = this.call<NativeInfo>("info", handle);
      if (info.state === "error") throw responseError(info);
      if (info.state === "headers" || info.state === "eof") return 2;
      throw new FastlsError("upload is closed or invalid");
    }
    return code;
  }
}
