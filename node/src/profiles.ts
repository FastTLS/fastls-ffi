import { Native } from "./native.js";

/** Trusted immutable session policy. Keys follow the native versioned schema. */
export interface AddressPolicy {
  readonly allow_schemes?: readonly ("http" | "https")[];
  readonly allow_hosts?: readonly string[];
  readonly allow_ports?: readonly number[];
  readonly allow_cidrs?: readonly string[];
  readonly deny_cidrs?: readonly string[];
}

/** Explicit snapshot of a complete schema-v1 profile, validated by the core. */
export class ProfileSpec {
  private readonly snapshot: string;
  constructor(value: Readonly<Record<string, unknown>>) {
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new TypeError("ProfileSpec requires an object");
    this.snapshot = JSON.stringify(value);
    if (Buffer.byteLength(this.snapshot) > 256 * 1024) throw new RangeError("profileSpec exceeds 256KiB");
  }
  static fromFingerprints(base: ProfileSpec, options: { ja3?: string; h2?: string; library?: string }): ProfileSpec {
    if (!(base instanceof ProfileSpec)) throw new TypeError("base must be ProfileSpec");
    const { ja3 = "", h2 = "", library } = options;
    if (typeof ja3 !== "string" || typeof h2 !== "string") throw new TypeError("ja3 and h2 must be strings");
    if (Object.keys(options).some(key => !["ja3", "h2", "library"].includes(key))) throw new TypeError("unknown fingerprint option");
    return new ProfileSpec(Native.load(library).adaptProfile({ schema_version: 1, base: base.toJSON(), ja3, h2 }));
  }
  toJSON(): Record<string, unknown> { return JSON.parse(this.snapshot); }
}
