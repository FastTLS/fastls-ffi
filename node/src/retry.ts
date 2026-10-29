import { setTimeout as sleep } from "node:timers/promises";
import { AbortError, FastlsError, FastlsTimeoutError } from "./errors.js";
import type { UploadBody } from "./upload.js";

const SAFE = new Set(["GET", "HEAD", "OPTIONS", "PUT", "DELETE"]);
export interface RetryOptions {
  maxRetries?: number;
  methods?: readonly string[];
  statuses?: readonly number[];
  backoffMs?: number;
  maxDelayMs?: number;
  jitter?: boolean;
  retryAfter?: boolean;
  retryNetwork?: boolean;
  allowNonIdempotent?: boolean;
}

/** Immutable, opt-in business retries. Durations are milliseconds. */
export class RetryPolicy {
  readonly maxRetries: number;
  readonly methods: readonly string[];
  readonly statuses: readonly number[];
  readonly backoffMs: number;
  readonly maxDelayMs: number;
  readonly jitter: boolean;
  readonly retryAfter: boolean;
  readonly retryNetwork: boolean;
  constructor(options: RetryOptions = {}) {
    if (!options || typeof options !== "object" || Array.isArray(options)) throw new TypeError("retry options must be an object");
    const allowed = new Set(["maxRetries", "methods", "statuses", "backoffMs", "maxDelayMs", "jitter", "retryAfter", "retryNetwork", "allowNonIdempotent"]);
    if (Object.keys(options).some(key => !allowed.has(key))) throw new TypeError("unknown retry option");
    for (const key of ["methods", "statuses"] as const) if (options[key] !== undefined && !Array.isArray(options[key])) throw new TypeError(`${key} must be an array`);
    this.maxRetries = options.maxRetries === undefined ? 2 : options.maxRetries;
    if (!Number.isInteger(this.maxRetries) || this.maxRetries < 0 || this.maxRetries > 100) throw new RangeError("maxRetries must be 0..100");
    this.methods = Object.freeze([...(options.methods ?? SAFE)].map(method => {
      if (typeof method !== "string" || !/^[A-Za-z]+$/.test(method)) throw new TypeError("invalid retry method");
      return method.toUpperCase();
    }));
    for (const key of ["jitter", "retryAfter", "retryNetwork", "allowNonIdempotent"] as const) {
      if (options[key] !== undefined && typeof options[key] !== "boolean") throw new TypeError(`${key} must be a boolean`);
    }
    if (!options.allowNonIdempotent && this.methods.some(method => !SAFE.has(method))) throw new TypeError("non-idempotent methods require explicit authorization");
    this.statuses = Object.freeze([...(options.statuses ?? [429, 502, 503, 504])]);
    if (this.statuses.some(status => !Number.isInteger(status) || status < 400 || status > 599 || [401, 403, 407].includes(status))) throw new RangeError("invalid retry status");
    this.backoffMs = options.backoffMs === undefined ? 100 : options.backoffMs;
    this.maxDelayMs = options.maxDelayMs === undefined ? 10000 : options.maxDelayMs;
    for (const value of [this.backoffMs, this.maxDelayMs]) if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 86400000) throw new RangeError("retry delays must be within 0..86400000 milliseconds");
    this.jitter = options.jitter ?? true;
    this.retryAfter = options.retryAfter ?? true;
    this.retryNetwork = options.retryNetwork ?? true;
    Object.freeze(this);
  }
  eligible(method: string, used: number, replayable: boolean, error?: unknown, status?: number): boolean {
    if (used >= this.maxRetries || !this.methods.includes(method.toUpperCase()) || !replayable) return false;
    if (status !== undefined) return this.statuses.includes(status);
    return this.retryNetwork && error instanceof FastlsError && (error.code === "network_error" ||
      (error instanceof FastlsTimeoutError && ["connect", "headers"].includes(error.phase)));
  }
  delay(used: number, header?: string | null): number {
    let delay = Math.min(this.maxDelayMs, this.backoffMs * 2 ** used);
    if (this.jitter) delay *= Math.random();
    if (this.retryAfter && header) {
      const seconds = /^[0-9]+$/.test(header) ? Number(header) : undefined;
      const value = seconds === undefined ? Date.parse(header) - Date.now() : Math.min(seconds, this.maxDelayMs / 1000) * 1000;
      if (!Number.isNaN(value)) delay = Math.max(delay, Math.min(this.maxDelayMs, Math.max(0, value)));
    }
    return delay;
  }
}

export function replayable(body?: UploadBody, factory?: () => UploadBody): boolean {
  return factory !== undefined || body === undefined || typeof body === "string" || body instanceof Uint8Array;
}

export async function retryWait(delay: number, deadline: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) throw new AbortError(undefined, { cause: signal.reason });
  if (deadline - performance.now() <= delay) throw new FastlsTimeoutError("total deadline exceeded before retry", "total");
  try { await sleep(delay, undefined, { signal }); }
  catch (error) { if (signal.aborted) throw new AbortError(undefined, { cause: signal.reason }); throw error; }
}
