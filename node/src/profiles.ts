import { Native } from "./native.js";

/** Trusted immutable session policy. Keys follow the native versioned schema. */
export interface AddressPolicy {
  readonly allow_schemes?: readonly ("http" | "https")[];
  readonly allow_hosts?: readonly string[];
  readonly allow_ports?: readonly number[];
  readonly allow_cidrs?: readonly string[];
  readonly deny_cidrs?: readonly string[];
}

/** Optional stream settings within a complete schema-v1 profile. */
export interface HTTP2StreamConfig {
  /** 0 or omitted selects stream 1; other values are odd, up to 2^31-1. */
  readonly first_stream_id?: number;
  /** Receive credit sent once after initial request HEADERS; 0 disables it. */
  readonly stream_window_increment?: number;
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
    const native = Native.load(library);
    native.requireCapabilities(base.nativeCapabilities());
    return new ProfileSpec(native.adaptProfile({ schema_version: 1, base: base.toJSON(), ja3, h2 }));
  }
  toJSON(): Record<string, unknown> { return JSON.parse(this.snapshot); }
  /** @internal Capability negotiation happens before allocating a session. */
  nativeCapabilities(): string[] {
    const value = this.toJSON() as { http2?: HTTP2StreamConfig; capabilities?: { required_features?: unknown } };
    const stream = value.http2?.first_stream_id || value.http2?.stream_window_increment ||
      (Array.isArray(value.capabilities?.required_features) && value.capabilities.required_features.includes("http2.stream.v1"));
    return stream ? ["profile_spec_v1", "http2_stream_v1"] : ["profile_spec_v1"];
  }
}
