import { Native } from "./native.js";
import { FastlsError } from "./errors.js";

const fields = {
  initialStreamReceiveWindow: "initial_stream_receive_window", maxStreamReceiveWindow: "max_stream_receive_window",
  initialConnectionReceiveWindow: "initial_connection_receive_window", maxConnectionReceiveWindow: "max_connection_receive_window",
  maxIdleTimeoutMs: "max_idle_timeout_ms", keepAlivePeriodMs: "keep_alive_period_ms", maxResponseHeaderBytes: "max_response_header_bytes",
} as const;

export interface HTTP3ConfigOptions {
  initialStreamReceiveWindow?: number; maxStreamReceiveWindow?: number;
  initialConnectionReceiveWindow?: number; maxConnectionReceiveWindow?: number;
  maxIdleTimeoutMs?: number;
  /** Undefined selects the H3 default; zero disables periodic keepalive. */
  keepAlivePeriodMs?: number;
  maxResponseHeaderBytes?: number;
}
export type HTTP3EffectiveConfig = Readonly<Required<HTTP3ConfigOptions>>;

/** Immutable owner configuration. Sizes are bytes; durations are integer ms. */
export class HTTP3Config {
  private readonly values: Readonly<Record<string, number>>;
  constructor(options: HTTP3ConfigOptions = {}) {
    if (!options || typeof options !== "object" || Array.isArray(options)) throw new TypeError("HTTP3Config requires an object");
    const values: Record<string, number> = {};
    for (const name of Object.keys(options)) if (!Object.hasOwn(fields, name)) throw new TypeError(`unknown HTTP3Config field: ${name}`);
    for (const [key, wire] of Object.entries(fields)) {
      const value = options[key as keyof HTTP3ConfigOptions];
      if (value === undefined) continue;
      if (!Number.isSafeInteger(value) || value < 0) throw new TypeError(`${key} must be a nonnegative safe integer`);
      values[wire] = value;
    }
    const windows = [options.initialStreamReceiveWindow || 524288, options.maxStreamReceiveWindow || 6291456,
      options.initialConnectionReceiveWindow || 524288, options.maxConnectionReceiveWindow || 15728640];
    if (windows.some(n => n < 4096 || n > 67108864) || windows[0]! > windows[1]! || windows[2]! > windows[3]!) throw new RangeError("H3 windows must be zero/default or 4096..67108864; initial <= maximum");
    const idle = options.maxIdleTimeoutMs || 30000;
    if (idle < 100 || idle > 86400000) throw new RangeError("maxIdleTimeoutMs must be zero or 100..86400000");
    const keep = options.keepAlivePeriodMs;
    if (keep !== undefined && keep !== 0 && (keep < 10 || keep > idle / 2)) throw new RangeError("keepAlivePeriodMs must be zero or 10..effective idle/2");
    const header = options.maxResponseHeaderBytes || 10485760;
    if (header < 1024 || header > 16777216) throw new RangeError("maxResponseHeaderBytes must be zero or 1024..16777216");
    this.values = Object.freeze(values); Object.freeze(this);
  }
  toJSON(): Record<string, number> { return { ...this.values }; }
}

export function requireHTTP3(native: Native): void {
  if (!native.http3ConfigAvailable || !native.call<string[]>("features").includes("http3_config_v1")) {
    const error = new FastlsError("native H3 configuration capability is unavailable");
    error.code = "unsupported_capability"; throw error;
  }
}

export function effectiveHTTP3(value: Record<string, number>): HTTP3EffectiveConfig {
  return Object.freeze(Object.fromEntries(Object.entries(fields).map(([name, wire]) => [name,value[wire]]))) as HTTP3EffectiveConfig;
}

export function http3Capabilities(library?: string): Readonly<{
  schemaVersion: number; scope: string; httpVersion: string; defaults: HTTP3EffectiveConfig;
  fields: readonly Readonly<Record<string, unknown>>[];
}> {
  const native = Native.load(library); requireHTTP3(native);
  const raw = native.call<{schema_version: number; scope: string; http_version: string; defaults: Record<string, number>; fields: Record<string,unknown>[]}>("http3_capabilities");
  return Object.freeze({ schemaVersion: raw.schema_version, scope: raw.scope, httpVersion: raw.http_version, defaults: effectiveHTTP3(raw.defaults),
    fields: Object.freeze(raw.fields.map(field => Object.freeze({ ...field, name: Object.entries(fields).find(([,wire])=>wire===field.name)?.[0] }))) });
}
