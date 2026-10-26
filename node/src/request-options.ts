import { randomBytes } from "node:crypto";
import { basename } from "node:path";
import type { UploadBody } from "./upload.js";
import { terminalUploadChunks } from "./upload.js";

export type QueryScalar = string | Uint8Array | number | boolean | null | undefined;
export type FormFields = Record<string, QueryScalar | readonly QueryScalar[]> | ReadonlyMap<string, QueryScalar | readonly QueryScalar[]> | readonly (readonly [string | Uint8Array, QueryScalar | readonly QueryScalar[]])[];
export type QueryParams = FormFields | string;
export type JSONValue = null | boolean | number | string | JSONValue[] | { [key: string]: JSONValue };
/** Caller-owned binary reader. Return an empty array at EOF; never more than maxBytes. */
export interface MultipartReader { name?: string; read(maxBytes: number, signal: AbortSignal): Uint8Array | Promise<Uint8Array>; }
export type MultipartBody = Uint8Array | MultipartReader;
export type MultipartFile = MultipartBody | readonly [string | null, MultipartBody, string?];
export type MultipartFiles = Record<string, MultipartFile> | ReadonlyMap<string, MultipartFile> | readonly (readonly [string, MultipartFile])[];

function pairs(value: unknown, name: string): [unknown, unknown][] {
  if (value instanceof Map) return [...value];
  if (Array.isArray(value) && value.every(p => Array.isArray(p) && p.length === 2)) return value as [unknown, unknown][];
  if (value && (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null)) return Object.entries(value);
  throw new TypeError(`${name} must be an object, Map or array of pairs`);
}
function scalar(value: unknown): string | Uint8Array {
  if (typeof value === "string" || value instanceof Uint8Array) return value;
  if (typeof value === "boolean") return value ? "True" : "False";
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  throw new TypeError("form/query values must be strings, bytes, finite numbers or booleans");
}
function values(value: unknown, name: string): [string | Uint8Array, string | Uint8Array][] {
  const result: [string | Uint8Array, string | Uint8Array][] = [];
  for (const [key, item] of pairs(value, name)) {
    if (typeof key !== "string" && !(key instanceof Uint8Array)) throw new TypeError(`${name} names must be strings or bytes`);
    for (const part of Array.isArray(item) ? item : [item]) if (part !== null && part !== undefined) result.push([key, scalar(part)]);
  }
  return result;
}
function quote(value: string | Uint8Array): string {
  const bytes = typeof value === "string" ? Buffer.from(value) : value;
  let encoded = "";
  for (const b of bytes) encoded += b === 32 ? "+" : (b >= 65 && b <= 90) || (b >= 97 && b <= 122) || (b >= 48 && b <= 57) || [45,46,95,126].includes(b) ? String.fromCharCode(b) : `%${b.toString(16).toUpperCase().padStart(2,"0")}`;
  return encoded;
}
function form(value: unknown): string { return values(value, "form").map(([k,v]) => `${quote(k)}=${quote(v)}`).join("&"); }
function metadata(value: unknown): string {
  if (typeof value !== "string" || /[\x00-\x1f\x7f]/.test(value)) throw new TypeError("multipart metadata must be strings without control characters");
  return value.replaceAll("\\", "\\\\").replaceAll('"', "%22");
}
function validateJSON(value: unknown, stack = new Set<object>()): void {
  if (value === null || typeof value === "string" || typeof value === "boolean" || (typeof value === "number" && Number.isFinite(value))) return;
  if (!value || typeof value !== "object" || (!Array.isArray(value) && Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null)) throw new TypeError("json must contain only JSON values and finite numbers");
  if (stack.has(value)) throw new TypeError("json must not contain cycles");
  if ("toJSON" in value) throw new TypeError("json does not support toJSON hooks");
  stack.add(value);
  if (Array.isArray(value)) { for (let i=0;i<value.length;i++) validateJSON(value[i], stack); }
  else {
    for (const entry of Object.values(value)) validateJSON(entry, stack);
  }
  stack.delete(value);
}

class Multipart {
  readonly boundary = "fastls-" + randomBytes(24).toString("hex");
  private readonly parts: { header: Buffer; body: MultipartBody }[] = [];
  private used = false;
  constructor(fields: FormFields | undefined, files: MultipartFiles) {
    for (const [key, value] of fields === undefined ? [] : values(fields, "form")) {
      const name = typeof key === "string" ? key : new TextDecoder("utf-8", { fatal:true }).decode(key);
      this.add(`Content-Disposition: form-data; name="${metadata(name)}"\r\n`, typeof value === "string" ? Buffer.from(value) : value);
    }
    for (const [key, spec] of pairs(files, "files")) {
      let filename: unknown, body: unknown, kind: unknown;
      if (Array.isArray(spec)) {
        if (spec.length !== 2 && spec.length !== 3) throw new TypeError("files tuples must be [filename, body, contentType?]");
        [filename,body,kind] = spec;
      } else { body=spec; filename=basename(String((body as MultipartReader)?.name ?? key).replaceAll("\\","/")); }
      if (!(body instanceof Uint8Array) && (!body || typeof (body as MultipartReader).read !== "function")) throw new TypeError("files bodies must be bytes or binary readers");
      let header = `Content-Disposition: form-data; name="${metadata(key)}"`;
      if (filename !== null) header += `; filename="${metadata(filename)}"`;
      // Stable cross-platform subset; callers supply other media types explicitly.
      const extension = typeof filename === "string" ? filename.slice(filename.lastIndexOf(".")).toLowerCase() : "";
      kind ??= ({".txt":"text/plain",".json":"application/json",".html":"text/html",".csv":"text/csv",".png":"image/png",".jpg":"image/jpeg",".jpeg":"image/jpeg",".pdf":"application/pdf"} as Record<string,string>)[extension] ?? "application/octet-stream";
      metadata(kind);
      this.add(header + `\r\nContent-Type: ${kind}\r\n`, body as MultipartBody);
    }
  }
  private add(header: string, body: MultipartBody): void { this.parts.push({header:Buffer.from(`--${this.boundary}\r\n${header}\r\n`), body}); }
  async *chunks(signal: AbortSignal): AsyncGenerator<Uint8Array> {
    if (this.used) throw new TypeError("multipart bodies cannot be replayed");
    this.used = true;
    for (const {header,body} of this.parts) {
      signal.throwIfAborted();
      yield header;
      if (body instanceof Uint8Array) {
        for (let offset=0;offset<body.byteLength;offset+=65536) yield body.subarray(offset,offset+65536);
      } else {
        while (true) {
          signal.throwIfAborted();
          const block = await body.read(65536, signal);
          if (!(block instanceof Uint8Array) || block.byteLength > 65536) throw new TypeError("multipart readers must return at most 64KiB of bytes");
          if (!block.byteLength) break;
          yield block;
        }
      }
      yield Buffer.from("\r\n");
    }
    const terminal = Buffer.from(`--${this.boundary}--\r\n`);
    terminalUploadChunks.add(terminal);
    yield terminal;
  }
}

export interface EncodingOptions { params?: QueryParams; json?: JSONValue; form?: FormFields; files?: MultipartFiles; body?: UploadBody; duplex?: boolean; }
/** Called before native handle allocation, with headers already merged. No file reads. */
export function prepare(url: string, options: EncodingOptions, headers: Record<string,string>): {url:string; body:UploadBody | undefined} {
  const hasJSON = Object.hasOwn(options,"json");
  if (hasJSON && (options.body !== undefined || options.form !== undefined || options.files !== undefined)) throw new TypeError("json cannot be combined with body, form or files");
  if (options.body !== undefined && (options.form !== undefined || options.files !== undefined)) throw new TypeError("body cannot be combined with form or files");
  if (options.params !== undefined) {
    const query = typeof options.params === "string" ? options.params : form(options.params);
    if (/[^\x00-\x7f]|#/.test(query)) throw new TypeError("raw params must be an ASCII query without a fragment");
    if (query) {
      const fragment = url.indexOf("#"), base = fragment < 0 ? url : url.slice(0,fragment), hash = fragment < 0 ? "" : url.slice(fragment);
      url = base + (base.includes("?") ? base.endsWith("?") ? "" : "&" : "?") + query + hash;
    }
  }
  let body = options.body, kind: string | undefined;
  if (options.files !== undefined) {
    if (options.duplex) throw new TypeError("multipart cannot be combined with duplex");
    if ("content-type" in headers) throw new TypeError("multipart manages Content-Type and its boundary");
    const multipart = new Multipart(options.form, options.files); body=signal=>multipart.chunks(signal);
    kind = `multipart/form-data; boundary=${multipart.boundary}`;
  } else if (hasJSON) { validateJSON(options.json); body=JSON.stringify(options.json); kind="application/json"; }
  else if (options.form !== undefined) { body=form(options.form); kind="application/x-www-form-urlencoded"; }
  if (kind) {
    if ("content-length" in headers || "transfer-encoding" in headers) throw new TypeError("encoded bodies manage Content-Length and Transfer-Encoding");
    headers["content-type"] ??= kind;
  }
  return {url,body};
}
