import koffi, { type LibraryHandle } from "koffi";
import { parse, stringify } from "lossless-json";
import { realpathSync } from "node:fs";
import { resolve } from "node:path";
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
  wire_header_values?: Record<string, string[]>;
  upload_discarded?: boolean;
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
  readonly path: string;

  static load(library?: string): Native {
    if (!isMainThread) throw new FastlsError("Fastls must be loaded on the main thread; worker_threads are not supported yet");
    const name = process.platform === "win32" ? "fastls.dll"
      : process.platform === "darwin" ? "libfastls.dylib" : "libfastls.so";
    let path: string;
    try {
      path = realpathSync(resolve(library ?? process.env.FASTLS_LIBRARY ?? resolve(__dirname, "../../dist", name)));
    } catch (cause) {
      throw new FastlsError("Cannot find Fastls library; set FASTLS_LIBRARY or pass { library: absolutePath }", { cause });
    }
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
    let envelope: { ok: boolean; result: T; error?: string };
    try {
      const json = koffi.decode.string(pointer);
      // Only these two results contain uint64 handles. Profile catalogs, headers
      // and info metadata use strings and bounded numeric fields; avoid the
      // allocating JS numeric parser on every poll and catalog lookup.
      envelope = (name === "start" || name === "session_create" ? parseNativeJSON(json) : JSON.parse(json)) as typeof envelope;
    } finally {
      this.functions.free(pointer);
    }
    if (!envelope?.ok) throw new FastlsError(envelope?.error ?? "invalid native result");
    return envelope.result;
  }

  start(input: unknown): bigint {
    const raw = encodeNativeJSON(input);
    return asHandle(this.call("start", raw, raw.length));
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
