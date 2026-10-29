import { createHash, randomBytes } from "node:crypto";
import { openSync, readSync, closeSync, existsSync, mkdirSync, writeFileSync, fsyncSync, renameSync, unlinkSync } from "node:fs";
import { join, dirname } from "node:path";
import { parse } from "lossless-json";
import { ProfileSpec } from "./profiles.js";
import { Session } from "./session.js";

const LIMIT = 512 * 1024;
const HASH = /^[0-9a-f]{64}$/;
function strict(text: string): unknown {
  if (typeof text !== "string" || Buffer.byteLength(text) > LIMIT) throw new RangeError("profile bundle exceeds 512KiB");
  try {
    // lossless-json accepts repeated keys when their values are equal. Inspect
    // all object keys as well, so the bundle schema rejects every duplicate.
    const stack: (Set<string> | null)[] = [];
    for (const token of text.matchAll(/"(?:\\.|[^"\\])*"|[{}\[\]]/gs)) {
      if (token[0] === "{") stack.push(new Set());
      else if (token[0] === "[") stack.push(null);
      else if (token[0] === "}" || token[0] === "]") stack.pop();
      else {
        let offset = token.index + token[0].length;
        while (offset < text.length && /\s/.test(text[offset]!)) offset++;
        if (text[offset] === ":") {
          const keys = stack.at(-1), key = JSON.parse(token[0]) as string;
          if (!keys || keys.has(key)) throw new TypeError("duplicate profile bundle field");
          keys.add(key);
        }
      }
      if (stack.length > 64) throw new RangeError("profile bundle exceeds depth 64");
    }
    const value = parse(text, null, {
      parseNumber(token) {
        const number = Number(token);
        if (!/^-?\d+$/.test(token) || !Number.isSafeInteger(number)) throw new TypeError("profile bundle requires portable integer fields");
        return number;
      },
      onDuplicateKey() { throw new TypeError("duplicate profile bundle field"); },
    });
    function visit(item: unknown, depth = 0): void {
      if (depth > 64) throw new RangeError("profile bundle exceeds depth 64");
      if (typeof item === "string" && /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(item)) throw new TypeError("invalid Unicode");
      if (item && typeof item === "object") for (const [key, entry] of Object.entries(item)) { visit(key, depth + 1); visit(entry, depth + 1); }
    }
    visit(value); return value;
  } catch { throw new TypeError("invalid profile bundle JSON"); }
}
function record(value: unknown): value is Record<string, unknown> { return !!value && typeof value === "object" && !Array.isArray(value); }
function read(path: string): string {
  const fd = openSync(path, "r");
  try {
    const buffer = Buffer.alloc(LIMIT + 1);
    let length = 0, size: number;
    while (length < buffer.length && (size = readSync(fd, buffer, length, buffer.length - length, null)) > 0) length += size;
    if (length > LIMIT) throw new RangeError("profile bundle exceeds 512KiB");
    return new TextDecoder("utf-8", { fatal: true }).decode(buffer.subarray(0, length));
  } finally { closeSync(fd); }
}
function atomic(path: string, text: string): void {
  const temporary = join(dirname(path), ".fastls-" + randomBytes(16).toString("hex"));
  const fd = openSync(temporary, "wx", 0o600);
  try {
    try { writeFileSync(fd, text, "utf8"); fsyncSync(fd); } finally { closeSync(fd); }
    renameSync(temporary, path);
  } finally { if (existsSync(temporary)) unlinkSync(temporary); }
}
function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (record(value)) return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
  return value;
}

/** A pinned digest verifies exact Spec bytes, not producer authenticity. */
export class ProfileBundle {
  readonly sha256: string;
  readonly profileId: string;
  readonly source: string;
  private readonly text: string;
  private readonly specText: string;
  constructor(text: string, options: { sha256: string; library?: string }) {
    if (typeof options.sha256 !== "string" || !HASH.test(options.sha256)) throw new TypeError("an explicit lowercase SHA-256 pin is required");
    const envelope = strict(text);
    const keys = ["profile_id", "schema_version", "sha256", "source", "spec_json"];
    if (!record(envelope) || JSON.stringify(Object.keys(envelope).sort()) !== JSON.stringify(keys) || envelope.schema_version !== 1) throw new TypeError("unsupported profile bundle schema");
    const raw = envelope.spec_json, source = envelope.source;
    if (typeof raw !== "string" || typeof source !== "string" || source.length < 1 || Buffer.byteLength(source) > 512 || /[\x00-\x1f\x7f]/.test(source)) throw new TypeError("invalid profile bundle fields");
    const digest = createHash("sha256").update(raw, "utf8").digest("hex");
    if (digest !== options.sha256 || digest !== envelope.sha256) throw new TypeError("profile bundle digest mismatch");
    const value = strict(raw);
    if (!record(value) || typeof value.id !== "string" || value.id !== envelope.profile_id) throw new TypeError("profile bundle ID mismatch");
    const spec = new ProfileSpec(value);
    const session = new Session({ library: options.library, profileSpec: spec });
    session.close();
    this.sha256 = digest; this.profileId = value.id; this.source = source;
    this.text = JSON.stringify(canonical(envelope)) + "\n";
    this.specText = JSON.stringify(value);
    Object.freeze(this);
  }
  static create(spec: ProfileSpec, options: { source: string; library?: string }): ProfileBundle {
    if (!(spec instanceof ProfileSpec)) throw new TypeError("spec must be ProfileSpec");
    const raw = JSON.stringify(canonical(spec.toJSON()));
    const digest = createHash("sha256").update(raw, "utf8").digest("hex");
    const text = JSON.stringify({ schema_version: 1, profile_id: spec.toJSON().id, spec_json: raw, sha256: digest, source: options.source });
    return new ProfileBundle(text, { sha256: digest, library: options.library });
  }
  get spec(): ProfileSpec { return new ProfileSpec(JSON.parse(this.specText)); }
  dumps(): string { return this.text; }
  export(path: string): void { atomic(path, this.text); }
}

/** Trusted local directories only; callers must serialize concurrent writers. */
export class ProfileStore {
  private readonly objects: string;
  private readonly statePath: string;
  constructor(readonly directory: string, private readonly options: { library?: string } = {}) {
    this.objects = join(directory, "objects"); this.statePath = join(directory, "state.json");
    mkdirSync(this.objects, { recursive: true });
  }
  private state(): { current: string | null; previous: string | null } {
    if (!existsSync(this.statePath)) return { current: null, previous: null };
    const value = strict(read(this.statePath));
    if (!record(value) || Object.keys(value).sort().join(",") !== "current,previous" || Object.values(value).some(pin => pin !== null && (typeof pin !== "string" || !HASH.test(pin)))) throw new TypeError("invalid profile store state");
    return value as { current: string | null; previous: string | null };
  }
  load(sha256?: string): ProfileBundle {
    const pin = sha256 ?? this.state().current;
    if (typeof pin !== "string" || !HASH.test(pin)) throw new TypeError("no valid profile pin");
    return new ProfileBundle(read(join(this.objects, pin + ".json")), { ...this.options, sha256: pin });
  }
  install(path: string, sha256: string): ProfileBundle {
    const bundle = new ProfileBundle(read(path), { ...this.options, sha256 });
    const target = join(this.objects, bundle.sha256 + ".json");
    if (existsSync(target)) this.load(bundle.sha256); else atomic(target, bundle.dumps());
    return this.pin(bundle.sha256);
  }
  pin(sha256: string): ProfileBundle {
    const bundle = this.load(sha256), state = this.state();
    if (state.current !== sha256) atomic(this.statePath, JSON.stringify({ current: sha256, previous: state.current }) + "\n");
    return bundle;
  }
  rollback(): ProfileBundle {
    const previous = this.state().previous;
    if (previous === null) throw new TypeError("no previous profile pin");
    return this.pin(previous);
  }
}
