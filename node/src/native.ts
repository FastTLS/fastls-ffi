import koffi, { type LibraryHandle } from "koffi";
import { parse, stringify } from "lossless-json";
import { realpathSync } from "node:fs";
import { resolve } from "node:path";
import { isMainThread } from "node:worker_threads";
import { FastlsError } from "./errors.js";

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
}

// Keep successful and partially loaded libraries alive until process exit.
// Worker teardown may unload a Go runtime, so worker_threads are not yet supported.
const libraries = new Map<string, LibraryHandle>();
const bindings = new Map<string, Native>();
type NativeFunction = ReturnType<LibraryHandle["func"]>;

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
      if (lib.func("uint32_t fastls_abi_version(void)")() !== 1) {
        throw new FastlsError("unsupported Fastls ABI version (expected 1)");
      }
      const signatures: Record<string, string[]> = {
        profiles: [], stats: [], session_create: [],
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
    } catch (cause) {
      throw new FastlsError(`Cannot bind Fastls library at ${path}; ABI v1 with session, cookie and stats symbols is required`, { cause });
    }
  }

  call<T = unknown>(name: string, ...args: unknown[]): T {
    const pointer = this.functions[name](...args);
    if (!pointer) throw new FastlsError("native library returned a null result");
    let envelope: { ok: boolean; result: T; error?: string };
    try {
      envelope = parseNativeJSON(koffi.decode.string(pointer)) as typeof envelope;
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
      throw new FastlsError(info.error ?? "native read failed");
    }
    if (![0, 1, 2].includes(code)) throw new FastlsError("native library returned an invalid read status");
    return { code, length };
  }
}
