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
  toJSON(): Record<string, unknown> { return JSON.parse(this.snapshot); }
}
